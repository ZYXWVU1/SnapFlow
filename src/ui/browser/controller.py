"""Application-owned browser service with optional desktop presentation."""
from PySide6.QtCore import QObject


class BrowserCompanionController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.service = self.dialog = self.coordinator = None
        self.active_request = None
        self._agent_provider = None
        self.resume_store = self.resume_coordinator = None

    def _resume_store(self):
        if self.resume_store is None:
            from src.resume.storage import ResumeStore
            self.resume_store = ResumeStore(self.owner.paths.resume_database)
        return self.resume_store

    def selected_browser_session(self):
        if self.dialog is None:
            raise PermissionError('Open Browser Companion and explicitly review a page first.')
        session_id = self.dialog.selected_session()
        self.validate_agent_sources((self.dialog.selected_source(),))
        return session_id

    def gateway(self):
        if self.service is None or self.service.manager is None or not self.service.enabled:
            raise PermissionError('Enable Browser Companion and authorize the selected page first.')
        from src.browser.execution import BrowserExecutionGateway
        return BrowserExecutionGateway(self.service.manager)

    def _automation_store(self):
        self.owner.automation_controller.ensure()
        return self.owner.automation_controller.store

    def web_runtime(self):
        from src.browser.automation import BrowserAutomationRuntime
        return BrowserAutomationRuntime(self.gateway(), self.selected_browser_session,
            enabled=self.owner.automation_controller.enabled)

    def agent_sources(self):
        if self.dialog is None:
            return ()
        try:
            source = self.dialog.selected_source()
            self.validate_agent_sources((source,))
            return (source,)
        except (ValueError, PermissionError):
            return ()

    def validate_agent_sources(self, sources):
        if self.service is None or self.service.context is None:
            raise PermissionError('Browser source authorization is unavailable.')
        self.service.context.validate_sources(sources)

    def agent_provider(self):
        if self._agent_provider is None:
            from src.browser.agent import BrowserActionCapabilityProvider
            from src.browser.automation import BrowserAutomationCapabilityProvider
            actions = BrowserActionCapabilityProvider(self.gateway)
            skills = BrowserAutomationCapabilityProvider(self._automation_store, self.web_runtime)
            class Providers:
                def register(_self, registry, session, bundle):
                    actions.register(registry, session, bundle)
                    skills.register(registry, session, bundle)
            self._agent_provider = Providers()
        return self._agent_provider

    def _sources_changed(self):
        missions = getattr(getattr(self.owner, 'agent_controller', None), 'service', None)
        if missions is None:
            return
        for bundle in tuple(missions.contexts.values()):
            sources = tuple(s for s in bundle.sources if s.source_type == 'browser_dom')
            if not sources:
                continue
            try:
                self.validate_agent_sources(sources)
            except (ValueError, PermissionError):
                missions.release_browser_context()
                break

    def pick_target(self, parent, callback, *, manual=False):
        from PySide6.QtWidgets import QInputDialog
        from src.automation.models import BrowserTargetTemplate
        from src.agent.models import thaw
        session_id = self.selected_browser_session()
        manager = self.gateway().manager
        snapshot = manager.context.latest(session_id)
        session = next(s for s in manager.sessions() if s.session_id == session_id)
        elements = thaw(snapshot.data['interactive_elements'])
        elements = [e for e in elements if e['visible'] and not e['sensitive'] and
            (manual or e['enabled'] and e['role'] in ('textbox','combobox','checkbox','link'))]
        labels = [str(i+1)+'. '+(e['accessible_name'] or e['label'])+' · '+e['role'] for i,e in enumerate(elements)]
        if not labels:
            raise ValueError('No supported semantic controls in the reviewed page.')
        selected, accepted = QInputDialog.getItem(parent, 'Reviewed Browser Target', session.origin, labels, editable=False)
        if accepted:
            element = elements[labels.index(selected)]
            callback(BrowserTargetTemplate(session.origin, element['role'], element['accessible_name'] or element['label']))

    def ensure_service(self):
        if self.service is None:
            from src.browser.service import BrowserCompanionService
            from .question_coordinator import BrowserQuestionCoordinator
            self.service = BrowserCompanionService(self.owner.paths, self.owner.credential_service, self,
                journal=self.owner.execution_journal,
                actions_allowed=lambda: not (self.owner.config.private_mode or self.owner.client.policy.private_mode),
                automation_store_provider=self._automation_store,
                teaching_enabled=self.owner.automation_controller.enabled,
                resume_store_provider=self._resume_store)
            from .autofill_coordinator import ResumeCoordinator
            self.resume_coordinator = ResumeCoordinator(self.owner, self.service, self._resume_store, self)
            self.service.changed.connect(self._sources_changed)
            self.coordinator = BrowserQuestionCoordinator(self.owner, self.service, self)
            self.coordinator.changed.connect(self.question_changed)
        return self.service

    def show(self):
        self.ensure_service()
        if self.dialog is None:
            from .browser_page import BrowserCompanionDialog
            self.dialog = BrowserCompanionDialog(self.service, self.owner.main_window)
            self.dialog.ask_requested.connect(self.ask)
            self.dialog.cancel_requested.connect(self.cancel_answer)
            self.dialog.source_changed.connect(self.cancel_answer)
            self.dialog.finished.connect(self.cancel_answer)
        self.refresh_runtime()
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()

    def refresh_runtime(self):
        self.cancel_answer()
        if self.coordinator:
            self.coordinator.refresh()
        if self.dialog is None:
            return
        mode = self.owner.config.ai_execution_mode.replace('_', ' ')
        privacy = ' · Private Mode: cloud processing blocked' if self.owner.client.policy.private_mode else ''
        fallback = ''
        if self.owner.config.ai_execution_mode in ('automatic', 'prefer_local'):
            allowed = (self.owner.config.allow_cloud_fallback and not self.owner.config.private_mode
                       and not self.owner.client.policy.private_mode)
            fallback = ' · cloud fallback allowed' if allowed else ' · cloud fallback blocked'
        self.dialog.runtime_label.setText('AI routing: ' + mode + privacy + fallback + '. AI is contacted only when you press Ask.')
        self.dialog.set_routing(self.owner.config.ai_execution_mode,
            self.owner.client.policy.private_mode or self.owner.config.private_mode,
            self.owner.config.allow_cloud_fallback)

    def stop(self):
        if self.resume_coordinator is not None and self.resume_coordinator.dialog is not None:
            self.resume_coordinator.dialog.reject()
        missions = getattr(getattr(self.owner, 'agent_controller', None), 'service', None)
        if missions:
            missions.release_browser_context()
        self.cancel_answer()
        if self.coordinator:
            self.coordinator.cancel_all()
        if self.service is not None:
            self.service.close()
        if self.dialog is not None:
            self.dialog.close()

    def cancel_answer(self, *args):
        job_id, self.active_request = self.active_request, None
        if job_id and self.coordinator:
            self.coordinator.cancel(job_id)
        if self.dialog:
            self.dialog.set_busy(False)
            if job_id:
                self.dialog.answer.setPlainText('Question cancelled.')
                self.dialog.execution_label.clear()

    def ask(self, question):
        if self.owner.quitting or self.active_request is not None or self.dialog is None:
            return
        try:
            choice = self.dialog.runtime_choice()
            source = self.dialog.selected_source()
            job = self.coordinator.submit(self.dialog.selected_session(), source, question, choice)
            self.active_request = job['job_id']
            self.dialog.set_busy(job['state'] in ('queued', 'answering'))
            self.dialog.answer.setPlainText('Answering from the reviewed browser source…')
            self.dialog.execution_label.clear()
            self.question_changed(job['job_id'])
        except (ValueError, PermissionError):
            self.dialog.set_busy(False)
            self.dialog.answer.setPlainText('Browser context changed. Review the page source and ask again.')

    def question_changed(self, job_id):
        if job_id != self.active_request or self.dialog is None:
            return
        job = self.coordinator.status(job_id)
        if job and job['state'] in ('queued', 'answering'):
            return
        self.active_request = None
        self.dialog.set_busy(False)
        if job and job['state'] == 'completed':
            self.dialog.answer.setPlainText(job['answer'])
            execution = job['execution']
            if execution['route'] in ('local', 'cloud'):
                self.dialog.execution_label.setText('Answered using ' + execution['route'].title() +
                    (' · ' + execution['model'] if execution['model'] else ''))
            else:
                self.dialog.execution_label.setText('AI execution details unavailable.')
        else:
            code = job['code'] if job else 'permission_changed'
            messages = {'stale_context': 'Page context changed. Refresh and review the page again.',
                'permission_changed': 'Browser permission or AI routing changed. Review and ask again.',
                'busy': 'Another question is already running for this page.',
                'timeout': 'This question timed out. You can try again.',
                'cancelled': 'Question cancelled.',
                'response_too_large': 'The answer exceeded the supported size. Try a narrower question.'}
            self.dialog.answer.setPlainText(messages.get(code, 'AI is unavailable. Check your AI model settings and try again.'))
