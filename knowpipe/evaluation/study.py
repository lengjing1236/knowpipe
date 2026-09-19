"""Freeze actual recommendation candidates, blind-label offline, then evaluate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from .metrics import evaluate
from ..web import baseline, mongo_sink, score_job


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def prepare(db, spark, profiles, k=10):
    if type(k) is not int or not 1 <= k <= 100 or not isinstance(profiles,list) or len(profiles)<2:
        raise ValueError('at least two learning tasks and 1 <= k <= 100 required')
    seen = set()
    for p in profiles:
        if not isinstance(p,dict) or not isinstance(p.get('query_id'),str) or not p['query_id'] or p['query_id'] in seen or not isinstance(p.get('task'),str) or not p['task'].strip():
            raise ValueError('unique query_id and task required')
        seen.add(p['query_id'])
        for name in ('known_topics','known_keywords'):
            if not isinstance(p.get(name,[]),list) or not all(isinstance(v,str) for v in p.get(name,[])):
                raise ValueError('profile keywords/topics must be string arrays')
        if not isinstance(p.get('read_doc_ids',[]),list) or any(not isinstance(r,dict) or not all(isinstance(r.get(f),str) for f in ('source','doc_id')) for r in p.get('read_doc_ids',[])):
            raise ValueError('invalid read_doc_ids')
    units = mongo_sink.get_knowledge_units(db)
    # Match Mongo knowledge_id upsert semantics, including repeated keywords across clusters.
    units = list({u['knowledge_id']:u for u in units}.values())
    qualities=score_job.current_qualities(db)
    documents = {(d['source'],d['doc_id']): d for d in db.documents.find({}, {'_id':0})}
    queries=[]
    for profile in profiles:
        scores=score_job.compute_scores(spark,score_job.rows_for_profile(units,profile,qualities,profile['query_id']))
        lookup={row['knowledge_id']:row['score'] for row in scores}
        ranked=sorted(units,key=lambda u:(-lookup[u['knowledge_id']],u['knowledge_id']))
        personalized=[]
        for unit in ranked:
            key=(unit['source'],unit['doc_id']); identity=':'.join(key)
            if key in documents and identity not in personalized:
                personalized.append(identity)
            if len(personalized)>=k: break
        base=baseline.get_baseline_recommendations(db,profile.get('read_doc_ids',[]),limit=k)
        baseline_ids=list(dict.fromkeys(':'.join((r['document']['source'],r['document']['doc_id'])) for r in base))
        candidates=[]
        for identity in set(personalized+baseline_ids):
            source,doc_id=identity.split(':',1); doc=documents[(source,doc_id)]
            candidates.append({'id':identity,'title':doc['title'],'body_text':doc['body_text'],
                               'source_url':doc['source_url'],'mining_batch_id':(doc.get('mining') or {}).get('batch_id')})
        if not candidates: raise ValueError('no_candidates')
        candidates.sort(key=lambda c:digest([profile['query_id'],c['id']]))
        queries.append({'query_id':profile['query_id'],'task':profile['task'],'profile':profile,
                        'personalized':personalized,'baseline':baseline_ids,'candidates':candidates})
    return {'study_id':uuid.uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),'k':k,
            'corpus_fingerprint':digest(sorted((s,i,digest(d)) for (s,i),d in documents.items())),
            'spark_application_id':spark.sparkContext.applicationId,'queries':queries,
            'ranking_scope':'existing knowledge-unit representatives, deduplicated by document; stable knowledge_id tie-break',
            'profile_origin':'explicit learning-task scenarios; not observed user behavior'}


def import_labels(manifest, labels):
    if labels.get('study_id')!=manifest['study_id'] or not isinstance(labels.get('reviewer'),str) or not labels['reviewer'].strip():
        raise ValueError('matching study and human reviewer required')
    try:
        when=datetime.fromisoformat(labels['reviewed_at'].replace('Z','+00:00'))
        if when.tzinfo is None: raise ValueError('timezone required')
    except (KeyError,TypeError,AttributeError,ValueError) as exc:
        raise ValueError('reviewed_at must be an ISO datetime with timezone') from exc
    records=labels.get('queries')
    if not isinstance(records,list): raise ValueError('queries required')
    by_id={r.get('query_id'):r.get('judgments') for r in records if isinstance(r,dict)}
    if len(by_id)!=len(records) or set(by_id)!={q['query_id'] for q in manifest['queries']}:
        raise ValueError('exactly one label set per query required')
    queries=[]
    for q in manifest['queries']:
        judgments=by_id[q['query_id']]
        if not isinstance(judgments,dict) or set(judgments)!={c['id'] for c in q['candidates']}:
            raise ValueError('all and only frozen candidates must be judged')
        if any(type(v) is not int or not 0<=v<=3 for v in judgments.values()):
            raise ValueError('complete human integer grades 0..3 required')
        queries.append({**q,'judgments':judgments})
    result=evaluate(queries,manifest['k'])
    return {**result,'study_id':manifest['study_id'],'manifest_sha256':digest(manifest),
            'reviewer':labels['reviewer'],'reviewed_at':labels['reviewed_at'],
            'limitation':'small task-based candidate-pool evaluation; no statistical significance claim'}


def render_html(manifest):
    blind={'study_id':manifest['study_id'],'queries':[{'query_id':q['query_id'],'task':q['task'],
           'candidates':[{key:c[key] for key in ('id','title','body_text','source_url')} for c in q['candidates']]} for q in manifest['queries']]}
    payload=json.dumps(blind,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    return '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>推荐相关性标注</title>
<style>body{max-width:850px;margin:32px auto;padding:16px;font:17px sans-serif}article{border:1px solid #ccc;padding:18px;margin:16px 0}pre{white-space:pre-wrap}select,input,button{font:inherit;padding:8px}small{display:block}</style>
<h1>推荐相关性标注</h1><p>请按学习任务评估每篇文档：0 无关，1 略相关，2 相关，3 高度相关。请亲自阅读后选择；候选顺序不代表推荐顺序。</p>
<label>评审代号（不用真名） <input id="reviewer" required></label><main id="tasks"></main><button id="save">检查并下载标签</button><p id="status"></p>
<script id="data" type="application/json">'''+payload+'''</script><script>
const data=JSON.parse(document.getElementById('data').textContent), inputs=[];
function node(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
for(const q of data.queries){const section=node('section','');section.append(node('h2',q.task));
for(const c of q.candidates){const card=node('article','');card.append(node('h3',c.title));
const details=node('details','');details.append(node('summary','阅读正文'),node('pre',c.body_text));card.append(details);
try{const u=new URL(c.source_url);if(['http:','https:'].includes(u.protocol)){const a=node('a','原始来源');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';card.append(a);}}catch(e){}
const label=node('label',' 相关性：'), select=document.createElement('select');select.required=true;
for(const [value,text] of [['','请选择'],['0','0 无关'],['1','1 略相关'],['2','2 相关'],['3','3 高度相关']]){const o=node('option',text);o.value=value;select.append(o);}
label.append(select);card.append(label);section.append(card);inputs.push({q:q.query_id,id:c.id,select});}
document.getElementById('tasks').append(section);}
document.getElementById('save').onclick=()=>{const reviewer=document.getElementById('reviewer').value.trim();if(!reviewer||inputs.some(x=>x.select.value==='')){document.getElementById('status').textContent='请填写评审代号并完成全部标注。';return;}
const result={study_id:data.study_id,reviewer,reviewed_at:new Date().toISOString(),queries:data.queries.map(q=>({query_id:q.query_id,judgments:Object.fromEntries(inputs.filter(x=>x.q===q.query_id).map(x=>[x.id,Number(x.select.value)]))}))};
const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=node('a','');a.href=url;a.download='labels.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
</script></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','evaluate'])
    parser.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27017')
    parser.add_argument('--mongo-db'); parser.add_argument('--profiles',type=Path)
    parser.add_argument('--manifest',type=Path); parser.add_argument('--labels',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--k',type=int,default=10)
    a=parser.parse_args()
    try:
        if a.action=='prepare':
            if not a.mongo_db or not a.profiles: parser.error('--mongo-db and --profiles required')
            if a.output.exists(): raise ValueError('output exists; preserve frozen study')
            from pymongo import MongoClient
            from pyspark.sql import SparkSession
            spark=SparkSession.builder.master(os.environ.get('SPARK_MASTER','local[2]')).appName('knowpipe-study').getOrCreate()
            try:
                with MongoClient(a.mongo_uri,serverSelectionTimeoutMS=5000) as c:
                    manifest=prepare(c[a.mongo_db],spark,json.loads(a.profiles.read_text()),a.k)
            finally: spark.stop()
            a.output.mkdir(parents=True)
            (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,default=str))
            (a.output/'label.html').write_text(render_html(manifest))
            print(json.dumps({'study_id':manifest['study_id'],'queries':len(manifest['queries']),'status':'awaiting_human_labels'}))
        else:
            if not a.manifest or not a.labels: parser.error('--manifest and --labels required')
            result=import_labels(json.loads(a.manifest.read_text()),json.loads(a.labels.read_text()))
            a.output.parent.mkdir(parents=True,exist_ok=True)
            a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,TypeError) as exc: parser.error(str(exc))


if __name__=='__main__': main()
