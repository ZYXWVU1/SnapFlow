"""Content-free runtime provenance and measured local/cloud activity."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from src.database import initialize_learning_database


class AIExecutionHistory:
    def __init__(self, path):
        self.path = Path(path)
        initialize_learning_database(self.path)

    def record(self, response, decision, *, private_mode=False):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO ai_execution_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (response.request_id, datetime.now(timezone.utc).isoformat(), response.runtime_id,
                 response.model_id, int(response.local), response.latency_ms, int(private_mode),
                 int('LOCAL_MODEL_FAILED' in decision.reason_codes or 'CLOUD_FALLBACK_ALLOWED' in decision.reason_codes),
                 json.dumps(decision.reason_codes), json.dumps(response.image_metadata) if response.image_metadata else None))
            # Bounded metadata history; provider usage retains its existing ledger.
            db.execute('DELETE FROM ai_execution_history WHERE request_id NOT IN '
                       '(SELECT request_id FROM ai_execution_history ORDER BY timestamp DESC LIMIT 10000)')

    def recent(self, limit=50):
        if not 1 <= limit <= 100:
            raise ValueError('Invalid history limit.')
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM ai_execution_history ORDER BY timestamp DESC LIMIT ?', (limit,)).fetchall()
        return [dict(dict(row), reasons=json.loads(row['reasons'])) for row in rows]

    def summary(self):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('''SELECT SUM(local), SUM(1-local),
                AVG(CASE WHEN local=1 THEN latency_ms END), AVG(CASE WHEN local=0 THEN latency_ms END),
                SUM(fallback), SUM(private_mode) FROM ai_execution_history''').fetchone()
        local, cloud, local_ms, cloud_ms, fallback, private = row
        total = (local or 0) + (cloud or 0)
        return dict(local_requests=local or 0, cloud_requests=cloud or 0,
            local_percentage=(local or 0)*100/total if total else 0,
            cloud_percentage=(cloud or 0)*100/total if total else 0,
            avg_local_latency_ms=local_ms, avg_cloud_latency_ms=cloud_ms,
            fallback_count=fallback or 0, private_requests=private or 0)
