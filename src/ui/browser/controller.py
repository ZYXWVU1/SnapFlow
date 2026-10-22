"""Application-owned browser service with optional desktop presentation."""
from PySide6.QtCore import QObject


class BrowserCompanionController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.service = self.dialog = self.coordinator = None
        self.active_request = None

    def ensure_service(self):
        if self.service is None:
            from src.browser.service import BrowserCompanionService
            from .question_coordinator import BrowserQuestionCoordinator
            self.service = BrowserCompanionService(self.owner.paths, self.owner.credential_service, self)
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
