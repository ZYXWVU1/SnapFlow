"""Transactional mission history with immutable plan versions and durable claims."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from src.observability.store import _check_regular_path
from .models import AgentPlan, AgentSession, STEP_STATUSES, canonical, now


class AgentStore:
    def __init__(self, path):
        self.path = Path(path)
        self.owner_id = uuid4().hex
        self.lock = RLock()
        self.initialized = False
        _check_regular_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1) or db.execute('PRAGMA integrity_check(1)').fetchone()[0] != 'ok':
                raise ValueError('Mission history needs repair; original data preserved.')
            if version == 0:
                if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                    raise ValueError('Unrecognized mission history.')
                db.execute('PRAGMA auto_vacuum=FULL')
                db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, owner TEXT, locked INTEGER NOT NULL DEFAULT 0)')
                db.execute('CREATE TABLE plans (id TEXT PRIMARY KEY, session TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(session, version))')
                db.execute('CREATE TABLE steps (plan TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL, attempted INTEGER NOT NULL DEFAULT 0, output TEXT, evidence TEXT, invocation TEXT, PRIMARY KEY(plan,id))')
                db.execute('CREATE TABLE approvals (plan TEXT NOT NULL, step TEXT NOT NULL, token TEXT NOT NULL, PRIMARY KEY(plan,step))')
                db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE IF NOT EXISTS contexts (session TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS budgets (session TEXT PRIMARY KEY, ai_calls INTEGER NOT NULL DEFAULT 0, writes INTEGER NOT NULL DEFAULT 0, replans INTEGER NOT NULL DEFAULT 0)')
        self.initialized = True

    @contextmanager
    def connection(self):
        with self.lock:
            _check_regular_path(self.path)
            for suffix in ('-journal', '-wal', '-shm'):
                _check_regular_path(self.path.with_name(self.path.name + suffix))
            if self.path.exists() and self.path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError('Mission history exceeds its storage limit.')
            target = self.path.absolute().as_uri() + '?mode=rw' if self.initialized else self.path
            db = sqlite3.connect(target, uri=self.initialized, timeout=2)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA secure_delete=ON')
                db.execute('PRAGMA synchronous=FULL')
                db.execute('PRAGMA max_page_count=8192')
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def create_session(self, session):
        with self.connection() as db:
            if db.execute('SELECT count(*) FROM sessions').fetchone()[0] >= 500:
                raise ValueError('Delete old missions before creating more.')
            db.execute('INSERT INTO sessions(id,payload) VALUES (?,?)', (session.session_id, canonical(session.to_dict())))
            db.execute('INSERT INTO budgets(session) VALUES (?)', (session.session_id,))

    def get_session(self, session_id):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (session_id,)).fetchone()
            if not row:
                raise ValueError('Mission does not exist.')
            return AgentSession.from_dict(json.loads(row[0]))

    def sessions(self):
        with self.connection() as db:
            return [AgentSession.from_dict(json.loads(row[0])) for row in db.execute('SELECT payload FROM sessions ORDER BY rowid DESC')]

    def update_session(self, session_id, **changes):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (session_id,)).fetchone()
            if not row:
                raise ValueError('Mission does not exist.')
            session = replace(AgentSession.from_dict(json.loads(row[0])), updated_at=now(), **changes)
            db.execute('UPDATE sessions SET payload=? WHERE id=?', (canonical(session.to_dict()), session_id))
            return session

    def add_plan(self, plan):
        with self.connection() as db:
            row = db.execute('SELECT payload,locked FROM sessions WHERE id=?', (plan.session_id,)).fetchone()
            if not row or row['locked']:
                raise ValueError('Mission missing or executing; plan cannot change.')
            session = AgentSession.from_dict(json.loads(row['payload']))
            if session.status == 'cancelled':
                raise ValueError('Cancelled missions cannot publish a late plan.')
            previous = db.execute('SELECT id,version,payload FROM plans WHERE session=? ORDER BY version DESC LIMIT 1', (plan.session_id,)).fetchone()
            if plan.version != (previous['version'] + 1 if previous else 1) or plan.supersedes_plan_id != (previous['id'] if previous else None):
                raise ValueError('Plan must supersede the current version.')
            receipts = {}
            if previous:
                old = AgentPlan.from_dict(json.loads(previous['payload']))
                old_steps = {s.step_id: s for s in old.steps}
                new_steps = {s.step_id: s for s in plan.steps}
                claimed_caps = set()
                for state in db.execute('SELECT * FROM steps WHERE plan=?', (old.plan_id,)):
                    if state['status'] == 'success' or state['attempted']:
                        sid = state['id']
                        if sid not in new_steps or new_steps[sid].to_dict() != old_steps[sid].to_dict():
                            raise ValueError('Completed or attempted steps must remain unchanged.')
                        receipts[sid] = dict(state)
                        if state['attempted']:
                            claimed_caps.add(old_steps[sid].capability_id)
                if any(s.step_id not in old_steps and s.capability_id in claimed_caps for s in plan.steps):
                    raise ValueError('Revised plans cannot duplicate attempted write capabilities.')
            try:
                db.execute('INSERT INTO plans VALUES (?,?,?,?)', (plan.plan_id, plan.session_id, plan.version, canonical(plan.to_dict())))
            except sqlite3.IntegrityError:
                raise ValueError('Plan versions cannot be replaced.') from None
            for step in plan.steps:
                receipt = receipts.get(step.step_id)
                if receipt:
                    db.execute('INSERT INTO steps(plan,id,status,attempted,output,evidence,invocation) VALUES (?,?,?,?,?,?,?)',
                        (plan.plan_id, step.step_id, receipt['status'], receipt['attempted'], receipt['output'], receipt['evidence'], receipt['invocation']))
                else:
                    db.execute("INSERT INTO steps(plan,id,status) VALUES (?,?,'pending')", (plan.plan_id, step.step_id))
            session = replace(session, current_plan_id=plan.plan_id, status='awaiting_approval', updated_at=now())
            db.execute('UPDATE sessions SET payload=? WHERE id=?', (canonical(session.to_dict()), session.session_id))
            db.execute('DELETE FROM approvals WHERE plan IN (SELECT id FROM plans WHERE session=?)', (session.session_id,))

    def get_plan(self, plan_id):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM plans WHERE id=?', (plan_id,)).fetchone()
            if not row:
                raise ValueError('Plan does not exist.')
            return AgentPlan.from_dict(json.loads(row[0]))

    def plans(self, session_id):
        with self.connection() as db:
            return [AgentPlan.from_dict(json.loads(row[0])) for row in db.execute('SELECT payload FROM plans WHERE session=? ORDER BY version', (session_id,))]

    def acquire(self, session_id):
        with self.connection() as db:
            return db.execute('UPDATE sessions SET owner=?,locked=1 WHERE id=? AND locked=0', (self.owner_id, session_id)).rowcount == 1

    def release(self, session_id):
        with self.connection() as db:
            db.execute('UPDATE sessions SET owner=NULL,locked=0 WHERE id=? AND owner=?', (session_id, self.owner_id))

    def claim_step(self, plan_id, step_id, *, side_effecting=False):
        with self.connection() as db:
            row = db.execute('SELECT s.* FROM steps s JOIN plans p ON p.id=s.plan JOIN sessions m ON m.id=p.session WHERE s.plan=? AND s.id=? AND m.locked=1 AND m.owner=?', (plan_id, step_id, self.owner_id)).fetchone()
            if not row or row['status'] in ('success', 'running', 'verification_failed') or (side_effecting and row['attempted']):
                raise ValueError('Step cannot be replayed or claimed twice.')
            db.execute("UPDATE steps SET status='running',attempted=? WHERE plan=? AND id=?", (1 if side_effecting else row['attempted'], plan_id, step_id))

    def set_step(self, plan_id, step_id, status, *, output=None, evidence=None, invocation=None):
        if status not in STEP_STATUSES:
            raise ValueError('Invalid step status.')
        with self.connection() as db:
            db.execute('UPDATE steps SET status=?,output=COALESCE(?,output),evidence=COALESCE(?,evidence),invocation=COALESCE(?,invocation) WHERE plan=? AND id=?',
                       (status, canonical(output) if output is not None else None, canonical(evidence) if evidence is not None else None, invocation, plan_id, step_id))

    def step_states(self, plan_id):
        with self.connection() as db:
            result = {}
            for row in db.execute('SELECT * FROM steps WHERE plan=?', (plan_id,)):
                item = dict(row)
                for key in ('output', 'evidence'):
                    item[key] = json.loads(item[key]) if item[key] else None
                result[row['id']] = item
            return result

    def grant(self, plan_id, step_id, token):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO approvals VALUES (?,?,?)', (plan_id, step_id, token))

    def approval(self, plan_id, step_id):
        with self.connection() as db:
            row = db.execute('SELECT token FROM approvals WHERE plan=? AND step=?', (plan_id, step_id)).fetchone()
            return row[0] if row else None

    def revoke(self, plan_id):
        with self.connection() as db:
            db.execute('DELETE FROM approvals WHERE plan=?', (plan_id,))

    def recover_interrupted(self):
        with self.connection() as db:
            rows = db.execute('SELECT id,payload FROM sessions WHERE locked=1 AND owner!=?', (self.owner_id,)).fetchall()
            for row in rows:
                session = replace(AgentSession.from_dict(json.loads(row['payload'])), status='interrupted', updated_at=now())
                db.execute('UPDATE sessions SET payload=?,owner=NULL,locked=0 WHERE id=?', (canonical(session.to_dict()), row['id']))
                db.execute("UPDATE steps SET status='interrupted' WHERE status='running' AND plan IN (SELECT id FROM plans WHERE session=?)", (row['id'],))
                db.execute('DELETE FROM approvals WHERE plan IN (SELECT id FROM plans WHERE session=?)', (row['id'],))
            return len(rows)

    def export_session(self, session_id):
        return {'version': 1, 'session': self.get_session(session_id).to_dict(),
                'plans': [{'plan': p.to_dict(), 'steps': self.step_states(p.plan_id)} for p in self.plans(session_id)]}

    def save_context(self, session_id, metadata):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO contexts VALUES (?,?)', (session_id, canonical(metadata)))

    def context(self, session_id):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM contexts WHERE session=?', (session_id,)).fetchone()
            if not row:
                raise ValueError('Mission context is unavailable; create a new reviewed plan.')
            return json.loads(row[0])

    def budget(self, session_id):
        with self.connection() as db:
            row = db.execute('SELECT ai_calls,writes,replans FROM budgets WHERE session=?', (session_id,)).fetchone()
            if row is None:
                raise ValueError('Mission budget is unavailable.')
            return dict(row)

    def charge_runtime(self, session_id, seconds):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (session_id,)).fetchone()
            if row is None:
                raise ValueError('Mission does not exist.')
            session = AgentSession.from_dict(json.loads(row[0]))
            updated = replace(session, elapsed_runtime_seconds=session.elapsed_runtime_seconds + seconds, updated_at=now())
            db.execute('UPDATE sessions SET payload=? WHERE id=?', (canonical(updated.to_dict()), session_id))

    def validation_context(self, plan):
        return {'consumed': self.budget(plan.session_id), 'step_states': self.step_states(plan.plan_id)}

    def charge(self, session_id, *, ai_calls=0, writes=0, replans=0):
        with self.connection() as db:
            session = AgentSession.from_dict(json.loads(db.execute('SELECT payload FROM sessions WHERE id=?', (session_id,)).fetchone()[0]))
            counts = db.execute('SELECT * FROM budgets WHERE session=?', (session_id,)).fetchone()
            updated = {name: counts[name] + increment for name, increment in (('ai_calls', ai_calls), ('writes', writes), ('replans', replans))}
            if any(type(n) is not int or n < 0 for n in (ai_calls, writes, replans)):
                raise ValueError('Invalid budget charge.')
            if updated['ai_calls'] > session.constraints.max_ai_calls or updated['writes'] > session.constraints.max_external_writes or updated['replans'] > session.constraints.max_replans:
                raise ValueError('Mission budget exhausted.')
            db.execute('UPDATE budgets SET ai_calls=?,writes=?,replans=? WHERE session=?', (updated['ai_calls'], updated['writes'], updated['replans'], session_id))
            return updated

    def carry_completed(self, old_plan, new_plan):
        """Receipts are copied to the new version; claimed effects stay non-replayable."""
        old_steps = {s.step_id: s.to_dict() for s in old_plan.steps}
        old_states = self.step_states(old_plan.plan_id)
        new_steps = {s.step_id: s.to_dict() for s in new_plan.steps}
        for sid, state in old_states.items():
            if state['status'] == 'success' or state['attempted']:
                if new_steps.get(sid) != old_steps[sid]:
                    raise ValueError('Completed or attempted steps must remain unchanged in a revised plan.')
        with self.connection() as db:
            for sid, state in old_states.items():
                if state['status'] == 'success' or state['attempted']:
                    db.execute('UPDATE steps SET status=?,attempted=?,output=?,evidence=?,invocation=? WHERE plan=? AND id=?',
                        (state['status'], state['attempted'], canonical(state['output']) if state['output'] is not None else None,
                         canonical(state['evidence']) if state['evidence'] is not None else None, state['invocation'], new_plan.plan_id, sid))

    def delete_session(self, session_id):
        with self.connection() as db:
            row = db.execute('SELECT locked FROM sessions WHERE id=?', (session_id,)).fetchone()
            if row and row[0]:
                raise ValueError('Stop this mission before deleting history.')
            for table in ('steps', 'approvals'):
                db.execute(f'DELETE FROM {table} WHERE plan IN (SELECT id FROM plans WHERE session=?)', (session_id,))
            db.execute('DELETE FROM plans WHERE session=?', (session_id,))
            db.execute('DELETE FROM sessions WHERE id=?', (session_id,))
            db.execute('DELETE FROM contexts WHERE session=?', (session_id,))
            db.execute('DELETE FROM budgets WHERE session=?', (session_id,))
