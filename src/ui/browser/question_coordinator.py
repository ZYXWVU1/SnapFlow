"""Application-owned Qt dispatch into the existing Context worker and pool."""
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import threading
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot


class BrowserQuestionCoordinator(QObject):
    changed = Signal(str)

    def __init__(self, owner, service, parent=None):
        super().__init__(parent)
        self.owner, self.service = owner, service
        self.pending = {}
        self.service.question_runtime = self.routing_policy
        self.service.question_requested.connect(self.dispatch, Qt.ConnectionType.QueuedConnection)
        self.service.changed.connect(self.refresh)

    def routing_policy(self):
        config = self.owner.client.config
        # Hash all runtime settings internally; the preview exposes only three safe fields.
        settings = asdict(config) if is_dataclass(config) else vars(config)
        revision = sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()
        return {'mode': config.ai_execution_mode,
                'private_mode': bool(config.private_mode or self.owner.client.policy.private_mode),
                'allow_cloud_fallback': bool(config.allow_cloud_fallback), 'settings_revision': revision}

    def status(self, job_id):
        manager = self.service.manager
        if manager is None:
            return None
        with manager.lock:
            manager.questions.prune()
            job = manager.questions.jobs.get(job_id)
            return job.public() if job else None

    def submit(self, session_id, source, question, runtime_choice=None):
        from src.browser.protocol import envelope, validate_envelope
        manager = self.service.manager
        if manager is None or source is None:
            raise ValueError('Review a live browser source first.')
        with manager.lock:
            manager.context.validate_sources((source,))
            session = manager.session_store.require(source.connection_id, session_id)
            identity = manager.session_store.identity(session)
            preview = manager.questions.preview(source.connection_id, session_id, identity)
            payload = {'identity': identity, **{key: preview[key] for key in
                ('snapshot_id', 'source_digest', 'routing_revision')}, 'question': question,
                'client_question_id': uuid4().hex, 'runtime_choice': runtime_choice}
            validate_envelope(envelope('question_start', payload, connection_id=source.connection_id,
                session_id=session_id, now=manager.clock()), now=manager.clock())
            public, dispatch = manager.questions.start(source.connection_id, session_id, payload)
        if dispatch:
            self.dispatch(dispatch)
        return public

    def cancel(self, job_id):
        manager = self.service.manager
        if manager:
            with manager.lock:
                job = manager.questions.jobs.get(job_id)
                if job:
                    manager.questions.cancel(job)
        self.refresh()

    @Slot(str)
    def dispatch(self, job_id):
        manager = self.service.manager
        if self.owner.quitting or manager is None or not manager.questions.begin(job_id):
            return
        # A cancelled runnable may still be unwinding or waiting in QThreadPool.
        # Keep those runnables in the budget until their finished signal arrives.
        if len(self.pending) >= 8:
            with manager.lock:
                manager.questions._terminate(manager.questions.jobs[job_id], 'failed', 'busy')
            self.changed.emit(job_id)
            return
        from src.context.models import ContextRequest, ContextSession
        from src.context.permissions import ContextPermissionService
        from src.context.controller import ContextController
        from src.app import ContextWorker
        request = None
        try:
            with manager.lock:
                job = manager.questions.jobs[job_id]
                source, question, choice = job.source, job.question, job.runtime_choice
            permissions = ContextPermissionService(browser_validator=manager.context.validate_sources)
            session = ContextSession.for_browser()
            permissions.select_browser_sources(session, (source,))
            request = ContextRequest.new(session, question, conversation_enabled=False)

            def cancelled():
                return (self.owner.quitting or self.service.manager is not manager
                        or not manager.questions.valid(job_id))

            scoped = self.owner.client.scoped(cancelled)
            execution = {'route': 'unknown', 'model': ''}
            client = self.owner.client

            class BrowserClient:
                cancellation_callback = staticmethod(cancelled)

                def request_text(inner, *args, **kwargs):
                    if cancelled():
                        raise ValueError('Browser question cancelled.')
                    # The existing router receives the initiating UI's closed choice;
                    # no worker-side runtime modal is required.
                    kwargs['runtime_choice'] = choice
                    scope = getattr(client, '_request_scope', None)
                    if isinstance(scope, threading.local):
                        scope.last_execution = None
                    answer = scoped.request_text(*args, **kwargs)
                    metadata = getattr(scope, 'last_execution', None)
                    if (type(metadata) is dict and set(metadata) == {'route', 'model'}
                            and metadata['route'] in ('local', 'cloud') and type(metadata['model']) is str):
                        execution.update(route=metadata['route'], model=metadata['model'][:200])
                    return answer

            worker = ContextWorker(ContextController(self.owner.memory_store, BrowserClient(), permissions), session, request)
            worker.signals.finished.connect(self.finished, Qt.ConnectionType.QueuedConnection)
            self.pending[request.request_id] = (manager, job_id, permissions, session, execution)
            self.owner.context_workers[request.request_id] = worker
            self.owner.context_pool.start(worker)
            self.changed.emit(job_id)
        except Exception:
            if request is not None:
                self.pending.pop(request.request_id, None)
                self.owner.context_workers.pop(request.request_id, None)
            manager.questions.complete(job_id, error=True)
            self.changed.emit(job_id)

    @Slot(str, object, str)
    def finished(self, request_id, result, error):
        self.owner.context_workers.pop(request_id, None)
        pending = self.pending.pop(request_id, None)
        if pending:
            manager, job_id, permissions, session, execution = pending
            if not error and result is not None:
                manager.questions.complete(job_id, result.answer, result.source_ids, **execution)
            else:
                manager.questions.complete(job_id, error=True)
            permissions.revoke_permission(session)
            session.history.clear()
            self.changed.emit(job_id)
        if self.owner.quitting:
            QTimer.singleShot(0, self.owner.finish_quit)

    def refresh(self):
        manager = self.service.manager
        if manager:
            manager.questions.prune()
        for request_id, (authority, job_id, permissions, session, execution) in tuple(self.pending.items()):
            if self.service.manager is not authority or not authority.questions.valid(job_id):
                permissions.revoke_permission(session)
                session.history.clear()
                self.changed.emit(job_id)

    def cancel_all(self):
        manager = self.service.manager
        if manager:
            manager.questions.clear()
        self.refresh()
