"""Bounded, bilingual transcript mining using real Spark ML and SQL operators."""
from __future__ import annotations

import re


def split_transcript(text: str, segment_chars=800) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError('empty_transcript')
    text = text.strip()
    if len(text) > 400000:
        raise ValueError('transcript_too_large')
    return [text[i:i + segment_chars] for i in range(0, len(text), segment_chars)]


def tokens(text):
    import jieba
    return [word.lower() for word in jieba.cut(text)
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9_+-]+|[\u4e00-\u9fff]{2,}', word)
            and word not in {'我们', '你们', '他们', '这个', '那个', '就是', '一个', '可以', '什么', '然后', '因为', '所以', '以及'}]


def analyze_transcript(spark, text):
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.feature import CountVectorizer, IDF, Normalizer, StopWordsRemover
    from pyspark.sql import Window, functions as F, types as T

    segments = split_transcript(text)
    rows = [(i, segment, tokens(segment)) for i, segment in enumerate(segments)]
    frame = spark.createDataFrame(rows, 'segment_id int, text string, raw_tokens array<string>')
    frame = StopWordsRemover(inputCol='raw_tokens', outputCol='tokens').transform(frame)
    frame = frame.filter(F.size('tokens') > 0)
    count = frame.count()
    if not count:
        raise ValueError('no_usable_words')
    cv = CountVectorizer(inputCol='tokens', outputCol='tf', vocabSize=20000).fit(frame)
    tf = cv.transform(frame)
    tfidf = IDF(inputCol='tf', outputCol='features').fit(tf).transform(tf)
    nonzero = F.udf(lambda v: bool(v.numNonzeros()), T.BooleanType())
    weighting = 'tfidf'
    if not tfidf.filter(nonzero('features')).limit(1).count():
        tfidf = tfidf.drop('features').withColumn('features', F.col('tf'))
        weighting = 'term_frequency'  # Single/identical segments have zero IDF; expose this fallback.
    distinct = tfidf.select('features').distinct().limit(4).count()
    if distinct >= 2:
        clustered = KMeans(k=min(4, distinct), seed=42, maxIter=20,
                           featuresCol='features', predictionCol='cluster_id').fit(tfidf).transform(tfidf)
        topic_mode = 'kmeans'
    else:
        clustered = tfidf.withColumn('cluster_id', F.lit(0))
        topic_mode = 'single_group'
    clustered = Normalizer(inputCol='features', outputCol='normalized').transform(clustered).cache()
    try:
        pair_type = T.ArrayType(T.StructType([T.StructField('term', T.IntegerType()),
                                             T.StructField('weight', T.DoubleType())]))
        sparse_entries = F.udf(lambda v: [(int(i), float(w)) for i, w in zip(v.indices, v.values) if w > 0], pair_type)
        entries = clustered.select('segment_id', F.explode(sparse_entries('normalized')).alias('entry'))
        left, right = entries.alias('a'), entries.alias('b')
        similarities = (left.join(right, (F.col('a.entry.term') == F.col('b.entry.term')) &
                                   (F.col('a.segment_id') != F.col('b.segment_id')))
                        .groupBy(F.col('a.segment_id').alias('origin'), F.col('b.segment_id').alias('target'))
                        .agg(F.sum(F.col('a.entry.weight') * F.col('b.entry.weight')).alias('score')))
        ranking = Window.partitionBy('origin').orderBy(F.desc('score'), F.asc('target'))
        top = similarities.withColumn('rank', F.row_number().over(ranking)).filter('rank <= 3')
        related = {}
        for row in top.collect():
            related.setdefault(row.origin, []).append({'segment_id': row.target, 'score': float(row.score)})
        output = []
        for row in clustered.select('segment_id', 'text', 'features', 'tf', 'cluster_id').orderBy('segment_id').collect():
            keyword_vector = row.features if row.features.numNonzeros() else row.tf
            segment_weighting = weighting if row.features.numNonzeros() else 'term_frequency'
            weighted = sorted(zip(keyword_vector.indices, keyword_vector.values), key=lambda p: (-p[1], p[0]))[:8]
            output.append({'segment_id': row.segment_id, 'text': row.text, 'cluster_id': int(row.cluster_id),
                           'keyword_weighting': segment_weighting,
                           'keywords': [{'term': cv.vocabulary[i], 'weight': float(w)} for i, w in weighted if w > 0],
                           'similar_segments': sorted(related.get(row.segment_id, []), key=lambda r: (-r['score'], r['segment_id']))})
        return {'segments': output, 'segment_count': len(output), 'skipped_segments': len(segments) - count,
                'weighting': weighting, 'topic_mode': topic_mode,
                'spark_application_id': spark.sparkContext.applicationId,
                'spark_master': spark.sparkContext.master}
    finally:
        clustered.unpersist()
