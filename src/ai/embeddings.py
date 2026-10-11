"""Local semantic indexing in Memory's existing SQLite store, with revision isolation."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math

from src.memory.models import MemorySearchResult
from src.network_policy import get_network_policy
from .cancellation import run_cancellable
from src.observability.performance import measured


@dataclass(frozen=True)
class EmbeddingManifest:
    model_id: str
    runtime_id: str
    dimension: int
    version: str = '1'
    endpoint: str = ''
    normalization: str = 'l2'
    distance_metric: str = 'cosine'

    def __post_init__(self):
        if not self.model_id or not self.runtime_id or not self.version or type(self.dimension) is not int or not 1 <= self.dimension <= 65536:
            raise ValueError('Embedding identity and dimension must be configured.')
        if self.normalization != 'l2' or self.distance_metric != 'cosine':
            raise ValueError('This index supports L2 normalization and cosine distance.')

    @property
    def namespace(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


class LocalEmbeddingProvider:
    local = True

    def __init__(self, runtime, manifest):
        if not runtime.local or not runtime.capabilities.embeddings:
            raise ValueError('Choose a loopback embedding runtime.')
        if runtime.model_id != manifest.model_id or runtime.runtime_id != manifest.runtime_id:
            raise ValueError('Embedding manifest must identify the selected runtime and model.')
        self.runtime, self.manifest = runtime, manifest

    def embed(self, texts):
        return self.runtime.embed(texts)


class SemanticMemoryIndex:
    def __init__(self, store, provider, policy=None):
        self.store, self.provider = store, provider
        self.manifest = provider.manifest
        self.policy = policy or get_network_policy()

    def _allowed(self):
        if not self.provider.local:
            self.policy.require_allowed('https://embedding.invalid', 'remote_embedding')

    @staticmethod
    def _check_cancelled(cancelled):
        if cancelled and cancelled():
            raise ValueError('Semantic search cancelled.')

    @measured('embedding', 'local_runtime')
    def _embed(self, texts, cancelled=None):
        def operation():
            self._check_cancelled(cancelled)
            self._allowed()
            return self.provider.embed(texts)
        vectors = run_cancellable(operation, cancelled=cancelled or (lambda: False), timeout=120)
        self._check_cancelled(cancelled)
        return vectors

    def _normalize(self, vector):
        if not isinstance(vector, (list, tuple)) or len(vector) != self.manifest.dimension or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
            raise ValueError('Embedding dimension or values do not match the configured index.')
        norm = math.hypot(*(float(x) for x in vector))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError('Embedding vector has no valid direction.')
        return [float(x) / norm for x in vector]

    def status(self):
        with self.store._connect() as db:
            total = db.execute('SELECT count(*) FROM memory_records').fetchone()[0]
            count = db.execute('''SELECT count(*) FROM memory_embeddings e JOIN memory_records m ON m.id=e.memory_id
                WHERE e.namespace=? AND e.revision=m.updated_at AND e.dimension=?''',
                (self.manifest.namespace, self.manifest.dimension)).fetchone()[0]
        return dict(total=total, indexed=count, pending=total-count, model_id=self.manifest.model_id)

    def rebuild(self, *, cancelled=None, progress=None, batch_size=16, max_records=10000):
        self._allowed()
        if type(batch_size) is not int or not 1 <= batch_size <= 32 or type(max_records) is not int or not 1 <= max_records <= 100000:
            raise ValueError('Invalid semantic rebuild bounds.')
        indexed = 0
        attempted = 0
        while attempted < max_records:
            if cancelled and cancelled():
                return dict(self.status(), indexed=indexed, cancelled=True)
            with self.store._connect() as db:
                rows = db.execute('''SELECT m.id, m.updated_at, m.title, m.description, m.searchable_text
                    FROM memory_records m LEFT JOIN memory_embeddings e ON e.memory_id=m.id AND e.namespace=?
                    WHERE e.memory_id IS NULL OR e.revision<>m.updated_at OR e.dimension<>?
                    ORDER BY m.id LIMIT ?''', (self.manifest.namespace, self.manifest.dimension,
                                              min(batch_size, max_records-attempted))).fetchall()
            if not rows:
                break
            attempted += len(rows)
            self._allowed()
            try:
                vectors = self._embed(['\n'.join((row['title'], row['description'], row['searchable_text']))[:200000]
                                      for row in rows], cancelled)
            except ValueError:
                if cancelled and cancelled():
                    return dict(self.status(), indexed=indexed, cancelled=True)
                raise
            if len(vectors) != len(rows):
                raise ValueError('Embedding batch size mismatch.')
            vectors = [self._normalize(vector) for vector in vectors]
            if cancelled and cancelled():
                return dict(self.status(), indexed=indexed, cancelled=True)
            installed = 0
            cancelled_batch = False
            with self.store._connect() as db:
                for row, vector in zip(rows, vectors):
                    if cancelled and cancelled():
                        cancelled_batch = True
                        break
                    current = db.execute('SELECT updated_at FROM memory_records WHERE id=?', (row['id'],)).fetchone()
                    if current and current[0] == row['updated_at']:
                        db.execute('INSERT OR REPLACE INTO memory_embeddings VALUES (?, ?, ?, ?, ?)',
                            (self.manifest.namespace, row['id'], row['updated_at'], self.manifest.dimension, json.dumps(vector, allow_nan=False)))
                        installed += 1
                if cancelled_batch or (cancelled and cancelled()):
                    db.rollback()
                    cancelled_batch = True
            if cancelled_batch:
                return dict(self.status(), indexed=indexed, cancelled=True)
            indexed += installed
            if progress:
                progress(indexed, self.status()['total'])
        status = self.status()
        return dict(status, indexed=indexed, cancelled=False)

    def search(self, query, *, filters=None, limit=20, offset=0, cancelled=None):
        self._check_cancelled(cancelled)
        self._allowed()
        if not isinstance(query, str) or len(query) > 500 or not 1 <= limit <= 100 or offset < 0:
            raise ValueError('Invalid semantic search options.')
        vectors = self._embed([query], cancelled)
        if len(vectors) != 1:
            raise ValueError('Embedding batch size mismatch.')
        vector = self._normalize(vectors[0])
        self._check_cancelled(cancelled)
        filters = filters or {}
        clauses = ['e.namespace=?', 'e.revision=m.updated_at', 'e.dimension=?']
        args = [self.manifest.namespace, self.manifest.dimension]
        for key, column in (('saved_from', 'm.saved_at'), ('saved_to', 'm.saved_at'),
                            ('source_type', 'm.source_type'), ('skill_id', 'm.skill_id')):
            if filters.get(key):
                op = '>=' if key == 'saved_from' else '<=' if key == 'saved_to' else '='
                clauses.append(f'{column}{op}?')
                args.append(filters[key])
        if filters.get('tag'):
            clauses.append('EXISTS (SELECT 1 FROM memory_tags t WHERE t.memory_id=m.id AND t.tag=?)')
            args.append(filters['tag'])
        if filters.get('has_screenshot'):
            clauses.append('m.screenshot_path IS NOT NULL')
        if filters.get('has_structured_data'):
            clauses.append("m.structured_data_json<>'{}'")
        with self.store._connect() as db:
            rows = db.execute('SELECT m.*, e.vector FROM memory_embeddings e JOIN memory_records m ON m.id=e.memory_id WHERE '
                + ' AND '.join(clauses) + ' ORDER BY m.id LIMIT 10000', args).fetchall()
        ranked = []
        for row in rows:
            self._check_cancelled(cancelled)
            try:
                stored = self._normalize(json.loads(row['vector']))
                score = sum(x * y for x, y in zip(vector, stored))
            except (ValueError, TypeError, OverflowError):
                continue
            if score > 0:
                ranked.append(MemorySearchResult(row['id'], row['title'],
                    (row['description'] or row['searchable_text'])[:220], row['saved_at'],
                    row['skill_id'], row['thumbnail_path'], score))
        ranked.sort(key=lambda hit: (-hit.relevance_score, hit.memory_id))
        self._check_cancelled(cancelled)
        return ranked[offset:offset+limit]

    def hybrid_search(self, query, *, filters=None, limit=20, offset=0, cancelled=None):
        self._check_cancelled(cancelled)
        keyword = self.store.search(query, filters=filters, limit=min(100, limit+offset))
        try:
            semantic = self.search(query, filters=filters, limit=min(100, limit+offset), cancelled=cancelled)
        except (ValueError, OSError, RuntimeError):
            self._check_cancelled(cancelled)
            semantic = []
        scores, hits = {}, {}
        for group in (keyword, semantic):
            for rank, hit in enumerate(group):
                hits[hit.memory_id] = hit
                scores[hit.memory_id] = scores.get(hit.memory_id, 0) + 1 / (60 + rank + 1)
        ids = sorted(hits, key=lambda key: (-scores[key], key))
        self._check_cancelled(cancelled)
        return [hits[key] for key in ids[offset:offset+limit]]
