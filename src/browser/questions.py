"""RAM-only question receipts and bounded jobs under the manager's authority lock."""
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import uuid4

from src.agent.models import canonical

JOB_CODES = ('', 'stale_context', 'permission_changed', 'provider_unavailable', 'busy', 'timeout', 'response_too_large', 'cancelled', 'invalid_request')
JOB_STATES = ('queued', 'answering', 'completed', 'cancelled', 'failed')
MAX_RESULT_BYTES = 16384


@dataclass
class QuestionJob:
    job_id: str
    connection_id: str
    session_id: str
    identity: dict
    client_id: str
    receipt: dict
    source: object
    question: str
    runtime_choice: str | None
    created_at: float
    state: str = 'queued'
    code: str = ''
    answer: str = ''
    source_ids: tuple = ()
    route: str = 'unknown'
    model: str = ''
    terminal_at: float | None = None

    def public(self):
        return {'job_id': self.job_id, 'state': self.state, 'code': self.code, 'answer': self.answer,
                'source_ids': list(self.source_ids), 'execution': {'route': self.route, 'model': self.model}}


class BrowserQuestionStore:
    def __init__(self, manager):
        self.manager = manager
        self.jobs = {}
        self.runtime = lambda: {'mode': 'local_only', 'private_mode': False, 'allow_cloud_fallback': False}

    def routing(self):
        value = self.runtime()
        return {name: value[name] for name in ('mode', 'private_mode', 'allow_cloud_fallback')}

    def signature(self):
        with self.manager.lock:
            return tuple((job.job_id, job.state, job.code) for job in self.jobs.values())

    def revision(self):
        # Internal policy fields may include a settings generation; never disclose it.
        return sha256(canonical(self.runtime()).encode()).hexdigest()

    def preview(self, connection_id, session_id, identity):
        session = self.manager.session_store.require(connection_id, session_id, identity)
        source = self.manager.context.source(session_id)
        captured = self.manager.context.latest(session_id).data['captured_at']
        data = json.loads(source.content)
        readable = bool(data['relevant_text'].strip() or data['headings'] or data['form_fields'])
        return {'session_id': session_id, 'identity': dict(identity),
                'snapshot_id': source.resource_uri.rsplit('/', 1)[-1],
                'source_digest': sha256(source.content.encode()).hexdigest(),
                'routing_revision': self.revision(), 'captured_at': captured,
                'expires_at': min(captured + 120000, int(session.expires_at * 1000)),
                'title': data['page_title'], 'origin': session.origin, 'content': source.content,
                'source_id': 'B1', 'routing': self.routing(),
                'readiness': 'ready' if readable else 'no_readable_context'}

    def _terminate(self, job, state, code):
        job.state, job.code = state, code
        job.answer, job.source_ids, job.question, job.source = '', (), '', None
        job.route, job.model = 'unknown', ''
        job.terminal_at = self.manager.clock()

    def _invalid(self, job):
        if job.receipt['routing_revision'] != self.revision():
            return 'permission_changed'
        try:
            current = self.preview(job.connection_id, job.session_id, job.identity)
            if any(current[key] != job.receipt[key] for key in ('snapshot_id', 'source_digest')):
                return 'stale_context'
        except (ValueError, PermissionError, StopIteration):
            return 'stale_context'
        return ''

    def prune(self):
        with self.manager.lock:
            now = self.manager.clock()
            for job in tuple(self.jobs.values()):
                if job.state in ('queued', 'answering') and now - job.created_at >= 120:
                    self._terminate(job, 'failed', 'timeout')
                invalid = self._invalid(job)
                if invalid and (job.state in ('queued', 'answering', 'completed')):
                    self._terminate(job, 'cancelled', invalid)
                if job.terminal_at is not None and now - job.terminal_at >= 120:
                    self.jobs.pop(job.job_id, None)

    def clear(self):
        with self.manager.lock:
            for job in self.jobs.values():
                self._terminate(job, 'cancelled', 'permission_changed')
            self.jobs.clear()

    def revoke_session(self, session_id):
        with self.manager.lock:
            for job in self.jobs.values():
                if job.session_id == session_id:
                    self._terminate(job, 'cancelled', 'permission_changed')

    def start(self, connection_id, session_id, payload):
        self.prune()
        receipt = self.preview(connection_id, session_id, payload['identity'])
        for old in self.jobs.values():
            if (old.connection_id == connection_id and old.session_id == session_id
                    and old.client_id == payload['client_question_id']
                    and old.receipt['source_digest'] == payload['source_digest']
                    and old.receipt['snapshot_id'] == payload['snapshot_id']):
                return old.public(), None
        job = QuestionJob(uuid4().hex, connection_id, session_id, dict(payload['identity']),
            payload['client_question_id'], {key: receipt[key] for key in
                ('snapshot_id', 'source_digest', 'routing_revision')}, self.manager.context.source(session_id),
            payload['question'].strip(), payload.get('runtime_choice'), self.manager.clock())
        if receipt['readiness'] != 'ready' or any(receipt[key] != payload[key] for key in ('snapshot_id', 'source_digest')):
            self._terminate(job, 'failed', 'stale_context')
        elif receipt['routing_revision'] != payload['routing_revision']:
            self._terminate(job, 'failed', 'permission_changed')
        else:
            routing = receipt['routing']
            choice = job.runtime_choice
            if ((choice is not None and routing['mode'] != 'ask_every_time')
                    or (routing['private_mode'] and choice == 'cloud_only')
                    or (routing['mode'] == 'ask_every_time' and not routing['private_mode'] and choice is None)):
                self._terminate(job, 'failed', 'permission_changed')
        active = [item for item in self.jobs.values() if item.state in ('queued', 'answering')]
        if job.state == 'queued' and (len(active) >= 8 or
                sum(item.connection_id == connection_id for item in active) >= 2 or
                any(item.session_id == session_id for item in active)):
            self._terminate(job, 'failed', 'busy')
        # Bound terminal receipts too; active jobs are never evicted to make space.
        if len(self.jobs) >= 64:
            terminal = next((item for item in self.jobs.values() if item.state not in ('queued', 'answering')), None)
            if terminal:
                self.jobs.pop(terminal.job_id)
        self.jobs[job.job_id] = job
        return job.public(), job.job_id if job.state == 'queued' else None

    def owned(self, connection_id, session_id, identity, job_id):
        self.manager.session_store.require(connection_id, session_id, identity)
        self.prune()
        job = self.jobs.get(job_id)
        if job is None or (job.connection_id, job.session_id, job.identity) != (connection_id, session_id, identity):
            raise PermissionError('Browser question is unavailable or belongs to another session.')
        return job

    def begin(self, job_id):
        with self.manager.lock:
            self.prune()
            job = self.jobs.get(job_id)
            if job is None or job.state != 'queued':
                return False
            job.state = 'answering'
            return True

    def valid(self, job_id):
        with self.manager.lock:
            self.prune()
            job = self.jobs.get(job_id)
            return job is not None and job.state in ('queued', 'answering')

    def cancel(self, job):
        self._terminate(job, 'cancelled', 'cancelled')
        return job.public()

    def complete(self, job_id, answer='', source_ids=(), *, route='unknown', model='', error=False):
        with self.manager.lock:
            self.prune()
            job = self.jobs.get(job_id)
            if job is None or job.state not in ('queued', 'answering'):
                return
            if error:
                self._terminate(job, 'failed', 'provider_unavailable')
                return
            job.state, job.answer, job.source_ids = 'completed', answer, tuple(source_ids)
            job.route, job.model = route, str(model or '')[:200]
            job.question, job.source, job.terminal_at = '', None, self.manager.clock()
            try:
                canonical({'ok': True, 'kind': 'question', 'job': job.public()}, limit=MAX_RESULT_BYTES)
                if not set(job.source_ids) <= {'B1'} or not isinstance(answer, str):
                    raise ValueError('Invalid answer.')
            except (ValueError, TypeError):
                self._terminate(job, 'failed', 'response_too_large')
