"""Lazy application wiring, owned workers and trusted per-action review UI."""
from concurrent.futures import Future
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QInputDialog


class AutomationController(QObject):
    approval_requested = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.store = self.runtime = None
        self.library = self.teach_dialog = self.run_dialog = self.approval_dialog = None
        self.pending_approvals = set()
        self.approval_requested.connect(self._review, Qt.ConnectionType.QueuedConnection)
        self.teach_action = None

    def enabled(self):
        return not self.owner.quitting and self.owner.beta_features.enabled('demonstration_automation_v1')

    def ensure(self):
        if self.store is None:
            from src.automation.storage import AutomationSkillStore
            from src.automation.runtime import AutomationRuntime
            self.store = AutomationSkillStore(self.owner.paths.automation_skills_file)
            self.runtime = AutomationRuntime(self.owner.desktop_actions_controller.service, enabled=self.enabled)

    def show(self):
        try:
            self.ensure()
            from .library import AutomationLibrary
            if self.library is None:
                self.library = AutomationLibrary(self)
            self.library.refresh()
            self.library.show()
            self.library.raise_()
        except Exception:
            QMessageBox.warning(self.owner.main_window, 'Automation Skills', 'The automation library could not be read. The original file was preserved. Use diagnostics or restore a backup.')

    def teach(self):
        if self.teach_dialog:
            self.teach_dialog.show()
            self.teach_dialog.raise_()
            return
        if not self.enabled():
            QMessageBox.information(self.owner.main_window, 'Teach SnapFlow', 'Enable Demonstration Automation in Settings → Beta Features, outside Safe Mode.')
            return
        self.ensure()
        from .teach import TeachDialog
        self.teach_dialog = TeachDialog(self.owner, enabled=self.enabled)
        self.teach_dialog.draft_ready.connect(self._new_draft)
        self.teach_dialog.active_changed.connect(self._teaching_changed)
        self.teach_dialog.finished.connect(lambda _: setattr(self, 'teach_dialog', None))
        self.teach_dialog.show()

    def _teaching_changed(self, active):
        if self.teach_action:
            self.teach_action.setText('Teaching Active · Show Controls…' if active else 'Teach SnapFlow…')
        self.owner.tray.setToolTip('SnapFlow · Teaching Active' if active else 'SnapFlow')

    def pause_teaching(self):
        if self.teach_dialog:
            self.teach_dialog.pause()

    def _new_draft(self, skill):
        try:
            self.store.save_draft(skill)
            # Let the teaching dialog finish cleanup before opening the editor.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.edit(skill))
        except Exception:
            QMessageBox.warning(self.owner.main_window, 'Draft Not Saved', 'The draft failed validation or could not be saved. Temporary evidence was discarded.')

    def edit(self, skill):
        from .editor import AutomationEditor
        dialog = AutomationEditor(self, skill)
        dialog.exec()
        if self.library:
            self.library.refresh()

    def run_skill(self, skill, *, testing=False):
        self.ensure()
        self.runtime._guard()
        if self.run_dialog or self.owner.agent_controller.busy:
            raise ValueError('Stop the current procedure or Agent mission before starting another run.')
        if testing and self.store.records[skill.version_id]['reviewed_hash'] is None:
            raise ValueError('Confirm review of this exact version before testing.')
        from .run import AutomationRunDialog
        self.run_dialog = AutomationRunDialog(self, skill, testing=testing)
        try:
            self.run_dialog.exec()
        finally:
            self.run_dialog = None

    def pick_target(self, parent, callback, *, include_read_only=False):
        service = self.owner.desktop_actions_controller.service
        def ready(state, error):
            if error or state is None:
                QMessageBox.warning(parent, 'Target Unavailable', 'Review a Desktop Actions window fence first.')
                return
            elements = [e for e in state.elements if not e.protected and (include_read_only or e.capabilities.actions) and (e.name or e.automation_id)]
            labels = [str(i + 1) + '. ' + e.name + ' · ' + e.control_type + ' · ' + e.automation_id for i, e in enumerate(elements)]
            if not labels:
                return
            label, accepted = QInputDialog.getItem(parent, 'Select Semantic Control', 'Current fenced application:', labels, editable=False)
            if accepted:
                try:
                    callback(elements[labels.index(label)])
                except Exception as exc:
                    QMessageBox.warning(parent, 'Target Selection', str(exc))
        self.owner.desktop_actions_controller._start(lambda cancelled: service._state(service._boundary(), cancelled=cancelled), ready)

    def register(self, registry, session, bundle):
        if not self.enabled():
            return
        try:
            self.ensure()
            from src.automation.agent import AutomationSkillCapabilityProvider
            AutomationSkillCapabilityProvider(self.store, self.runtime, self.request_review).register(registry, session, bundle)
        except Exception:
            return  # A corrupt library cannot enable an Agent capability.

    def request_review(self, preview, *, cancelled, guard):
        future = Future()
        self.pending_approvals.add(future)
        self.approval_requested.emit((preview, future, cancelled, guard))
        try:
            while not future.done():
                guard()
                if cancelled() or not self.enabled():
                    return None
                time.sleep(.05)
            return future.result()
        finally:
            self.pending_approvals.discard(future)
            if not future.done():
                future.set_result(None)

    def _review(self, request):
        preview, future, cancelled, guard = request
        if future.done():
            return
        token = None
        try:
            guard()
            if cancelled():
                return
            box = self.approval_dialog = QMessageBox(self.owner.main_window)
            box.setWindowTitle('Automation Skill · Approve One Desktop Action')
            box.setTextFormat(Qt.TextFormat.PlainText)
            element = preview.resolution.resolved_element
            box.setText('Action: ' + preview.request.action_type + '\nTarget: ' + (element.name if element else 'Unresolved') +
                '\nRisk: ' + preview.risk_level + '\n\nApprove this exact action once? Switch to the target window after approving.')
            box.setDetailedText('Current value: ' + str(element.state.get('value', '') if element else '')[:4096] + '\nDesired value: ' + str(preview.request.value)[:4096] +
                '\nOutcome: ' + str([c.to_dict() for c in preview.request.expected_postconditions]))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            from PySide6.QtCore import QTimer
            timer = QTimer(box)
            def check():
                try:
                    guard()
                    if future.done() or cancelled() or not self.enabled():
                        box.reject()
                except Exception:
                    box.reject()
            timer.timeout.connect(check)
            timer.start(100)
            self.owner.desktop_actions_controller.highlight(element)
            if box.exec() == QMessageBox.StandardButton.Yes:
                guard()
                if not cancelled() and not future.done():
                    token = self.runtime.service.approve(preview)
            timer.stop()
        except Exception:
            token = None
        finally:
            self.approval_dialog = None
            self.owner.desktop_actions_controller.clear_outlines()
            if not future.done():
                future.set_result(token)

    def invalidate(self):
        if self.teach_dialog and self.teach_dialog.recorder.session:
            self.teach_dialog.reject()
        if self.run_dialog:
            self.run_dialog.reject()
        if self.approval_dialog:
            self.approval_dialog.reject()

    def stop(self):
        if self.teach_dialog:
            self.teach_dialog.reject()
        if self.run_dialog:
            self.run_dialog.reject()
        if self.approval_dialog:
            self.approval_dialog.reject()
        for future in tuple(self.pending_approvals):
            if not future.done():
                future.set_result(None)
        if self.library:
            self.library.close()
