"""Bounded, versioned local semantic features; no implicit model downloads.

The models produce fallible features, not facts about a user's knowledge. The
recommendation layer retains original evidence and limits every comparison.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from ..learning.providers import TextResult, processor_identity

SEMANTIC_VERSION = 'bounded-native-context-onnx-semantic-v4'
MAX_BATCH_INPUTS = 512
MAX_TEXT_CHARACTERS = 4000
MODEL_LIMITS = {'rank': 512, 'embed': 256, 'nli': 512}


class SemanticUnavailable(RuntimeError):
    pass


class UnsupportedSemanticInput(ValueError):
    pass


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_text(text, allow_chinese=False):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
        raise UnsupportedSemanticInput('semantic_input_unsupported')
    if not allow_chinese and re.search(r'[\u4e00-\u9fff]', text):
        raise UnsupportedSemanticInput('semantic_english_required')


class LocalSemantic:
    """Three fixed-purpose ONNX CPU models; initialized only on first inference."""

    def __init__(self, root, *, threads=2, batch_size=8):
        self.root = Path(root)
        self.threads = max(1, min(4, int(threads)))
        self.batch_size = max(1, min(16, int(batch_size)))
        self._models = {}
        self._manifest = None
        self._processor_id = None
        self.text = EnglishText()

    def check_available(self):
        if self._manifest is not None:
            return
        try:
            manifest = json.loads((self.root / 'manifest.json').read_text())
            if manifest['format'] != 'knowpipe-semantic-onnx-v1' or manifest['language'] not in ('en', 'multilingual'):
                raise ValueError('manifest')
            fingerprints = []
            for name in MODEL_LIMITS:
                entry = manifest['models'][name]
                for filename in ('model.onnx', 'tokenizer.json', 'config.json'):
                    path = self.root / name / filename
                    expected = entry['files'][filename]
                    if path.stat().st_size != expected['bytes'] or _hash_file(path) != expected['sha256']:
                        raise ValueError('checksum')
                    fingerprints.append([name, filename, expected['sha256']])
            self._processor_id = 'semantic-' + hashlib.sha256(json.dumps(
                [SEMANTIC_VERSION, fingerprints], sort_keys=True).encode()).hexdigest()
            self._manifest = manifest
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SemanticUnavailable('semantic_model_unavailable') from error

    @property
    def language(self):
        self.check_available()
        return self._manifest['language']

    @property
    def processor_id(self):
        try:
            self.check_available()
            return self._processor_id
        except SemanticUnavailable:
            return 'semantic-unavailable-' + hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()

    def _model(self, name):
        self.check_available()
        if name not in self._models:
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
                folder = self.root / name
                config = json.loads((folder / 'config.json').read_text())
                tokenizer = Tokenizer.from_file(str(folder / 'tokenizer.json'))
                # Never truncate evidence silently. Inputs beyond the model's
                # supported window are explicitly unsupported.
                tokenizer.no_truncation()
                tokenizer.enable_padding(pad_id=int(config.get('pad_token_id', 0)))
                options = ort.SessionOptions()
                options.intra_op_num_threads = self.threads
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                session = ort.InferenceSession(str(folder / 'model.onnx'), sess_options=options,
                                               providers=['CPUExecutionProvider'])
                self._models[name] = (tokenizer, session)
            except Exception as error:
                raise SemanticUnavailable('semantic_model_unavailable') from error
        return self._models[name]

    def _batches(self, name, inputs):
        import numpy as np
        if len(inputs) > MAX_BATCH_INPUTS:
            raise UnsupportedSemanticInput('semantic_batch_limit_exceeded')
        if self._manifest is None and (self.root / 'manifest.json').is_file():
            self.check_available()
        for item in inputs:
            for text in (item if isinstance(item, tuple) else (item,)):
                # Model capability, rather than script detection, determines
                # whether native Chinese can be scored directly.
                allow_chinese = name != 'nli' and self._manifest is not None and self._manifest['language'] == 'multilingual'
                _validate_text(text, allow_chinese=allow_chinese)
        if not inputs:
            return
        tokenizer, session = self._model(name)
        for start in range(0, len(inputs), self.batch_size):
            encoded = tokenizer.encode_batch(inputs[start:start + self.batch_size])
            window = 128 if name == 'embed' and self._manifest is not None and self._manifest['language'] == 'multilingual' else MODEL_LIMITS[name]
            if any(len(row.ids) > window for row in encoded):
                raise UnsupportedSemanticInput('semantic_token_limit_exceeded')
            values = {
                'input_ids': np.asarray([row.ids for row in encoded], dtype=np.int64),
                'attention_mask': np.asarray([row.attention_mask for row in encoded], dtype=np.int64),
                'token_type_ids': np.asarray([row.type_ids for row in encoded], dtype=np.int64),
            }
            feeds = {entry.name: values[entry.name] for entry in session.get_inputs()}
            try:
                output = session.run(None, feeds)[0]
            except Exception as error:
                raise SemanticUnavailable('semantic_inference_failed') from error
            if not np.isfinite(output).all():
                raise SemanticUnavailable('semantic_inference_failed')
            yield output, values['attention_mask']

    def relevance(self, query, passages):
        """Return sigmoid ranking features, never calibrated correctness probabilities."""
        result = []
        for output, _ in self._batches('rank', [(query, passage) for passage in passages]):
            for row in output:
                value = float(row.reshape(-1)[0])
                result.append(1. / (1. + math.exp(-max(-60., min(60., value)))))
        return result

    def encode(self, sentences):
        """Attention-mask mean pooling and normalization specified by model card."""
        import numpy as np
        result = []
        for output, mask in self._batches('embed', sentences):
            expanded = mask[..., None].astype(np.float32)
            vectors = (output * expanded).sum(axis=1) / expanded.sum(axis=1).clip(min=1e-9)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-9)
            result.extend(vectors.tolist())
        return result

    def infer(self, pairs):
        """Direction: history premise → candidate hypothesis. Labels are model output."""
        import numpy as np
        result = []
        for output, _ in self._batches('nli', pairs):
            probabilities = np.exp(output - output.max(axis=1, keepdims=True))
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            for row in probabilities:
                result.append(dict(zip(('contradiction', 'entailment', 'neutral'), map(float, row))))
        return result

    def close(self):
        self._models.clear()


class EnglishText:
    """Translate finite Chinese evidence without changing its original offsets.

    Only successful translations are cached; keys include exact text and provider
    identity. Cache contains public evidence text, never a user's read history.
    """

    def __init__(self, translator=None, cache=None):
        self.translator, self.cache = translator, cache
        self._local = OrderedDict()

    @property
    def processor_id(self):
        return 'english-text-' + processor_identity(self.translator)

    def convert(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
            raise UnsupportedSemanticInput('semantic_input_unsupported')
        if not re.search(r'[\u4e00-\u9fff]', text):
            return text
        if self.translator is None:
            raise SemanticUnavailable('semantic_translation_unavailable')
        key = hashlib.sha256(json.dumps([SEMANTIC_VERSION, text, self.processor_id],
                                        ensure_ascii=False).encode()).hexdigest()
        found = self._local.get(key)
        if found is None and self.cache is not None:
            record = self.cache.find_one({'_id': key})
            found = record.get('text') if record else None
        if found is not None:
            _validate_text(found)
            return found
        try:
            translated = self.translator.translate(text, 'zh', 'en')
            if not isinstance(translated, TextResult) or not translated.complete or not translated.language.lower().startswith('en'):
                raise ValueError('invalid_translation')
            _validate_text(translated.text)
        except Exception as error:
            raise SemanticUnavailable('semantic_translation_failed') from error
        self._local[key] = translated.text
        while len(self._local) > 256:
            self._local.popitem(last=False)
        if self.cache is not None:
            # Public evidence translations are disposable. Keep storage bounded
            # even when a user repeatedly submits new goals/material.
            if self.cache.count_documents({}) >= 5000:
                stale = [r['_id'] for r in self.cache.find({}, {'_id': 1}).sort('created_at', 1).limit(100)]
                self.cache.delete_many({'_id': {'$in': stale}})
            self.cache.update_one({'_id': key}, {'$setOnInsert': {'text': translated.text,
                'processor_id': self.processor_id, 'rules_version': SEMANTIC_VERSION,
                'created_at': datetime.now(timezone.utc)}}, upsert=True)
        return translated.text


def configured_semantic(env=None):
    config = os.environ if env is None else env
    path = config.get('KNOWPIPE_SEMANTIC_MODEL_PATH')
    return LocalSemantic(path, threads=config.get('KNOWPIPE_MODEL_THREADS', 2)) if path else None
