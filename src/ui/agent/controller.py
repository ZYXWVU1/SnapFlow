"""Qt worker ownership and human review around the pure Agent mission service."""
import json
from concurrent.futures import Future
from threading import Event
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from src.agent.models import digest
from src.agent.executor import AgentExecutor
from src.agent.mission import AgentMissionService
from src.agent.services import AgentServices
from src.agent.ephemeral import DesktopAwareAgentStore as AgentStore
from src.agent.validation import AgentPlanValidator


class _Signals(QObject):
    finished = Signal(object, object, object)


class _Job(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.cancelled = Event()
        self.signals = _Signals()

    def run(self):
        result = error = None
        try:
            result = self.operation(self.cancelled.is_set)
        except Exception:
            error = 'Mission stopped. Review the selected sources, AI configuration, permissions and plan before retrying.'
        self.signals.finished.emit(self, result, error)


class AgentController(QObject):
    progress_changed = Signal(object)
    input_requested = Signal(object)

    def __init__(self, app_controller):
        super().__init__(app_controller)
        self.owner = app_controller
        self.page = app_controller.main_window.pages['agent']
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.worker = None
        self.service = self.store = None
        self.session_id = None
        self.view_plan_id = None
        self.stopped = False
        self.input_revision = digest(self.page.request_payload())
        self.mission_revisions = {}
        self.pending_revision = None
        self.input_dialog = None
        self.progress_changed.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.input_requested.connect(self._input, Qt.ConnectionType.QueuedConnection)
        for widget in (self.page.goal,):
            widget.textChanged.connect(self._inputs_changed)
        self.page.mode.currentIndexChanged.connect(self._inputs_changed)
        self.page.include_current.toggled.connect(self._inputs_changed)
        self.page.include_conversation.toggled.connect(self._inputs_changed)
        self.page.include_desktop.toggled.connect(self._inputs_changed)
        self.page.desktop_context_requested.connect(app_controller.desktop_controller.show)
        self.page.memory_list.itemChanged.connect(self._inputs_changed)
        for widget in self.page.permissions.values():
            widget.toggled.connect(self._inputs_changed)
        for widget in self.page.budgets.values():
            widget.valueChanged.connect(self._inputs_changed)
        self.page.limit_cost.toggled.connect(self._inputs_changed)
        self.page.max_estimated_cost.valueChanged.connect(self._inputs_changed)
        self.page.create_plan_requested.connect(self.create)
        self.page.approve_safe_requested.connect(self.approve_safe)
        self.page.approve_step_requested.connect(self.approve_step)
        self.page.execute_requested.connect(self.execute)
        self.page.pause_requested.connect(self.pause)
        self.page.cancel_requested.connect(self.cancel)
        self.page.resume_requested.connect(self.resume)
        self.page.replan_requested.connect(self.replan)
        self.page.edit_plan_requested.connect(self.edit)
        self.page.history_open_requested.connect(self.open_history)
        self.page.history_delete_requested.connect(self.delete_history)
        self.page.history_export_requested.connect(self.export_history)
        self.page.version_open_requested.connect(self.open_version)
        self.page.enable_requested.connect(app_controller.open_beta_features)
        self.apply_defaults()
        self.refresh_enabled()

    @property
    def busy(self):
        return self.worker is not None

    def _inputs_changed(self, *_):
        self.input_revision = digest(self.page.request_payload())

    def apply_defaults(self):
        if self.session_id is None and not self.busy:
            self.page.apply_defaults(self.owner.config)
            self._inputs_changed()

    def _enabled(self):
        return not self.stopped and not self.owner.quitting and self.owner.beta_features.enabled('agent_missions_v1')

    def _ensure(self):
        if self.service is not None:
            return
        path = getattr(self.owner.paths, 'agent_database', self.owner.paths.data_dir / 'agent_missions.sqlite3')
        if not path.exists() and not self._enabled():
            return
        self.store = AgentStore(path)
        self.store.recover_interrupted()
        # No persisted review grant from another app run is an execution authority.
        for mission in self.store.sessions():
            if mission.current_plan_id:
                self.store.revoke(mission.current_plan_id)
        self.services = AgentServices(self.owner.memory_store, self.owner.client,
            current_result=self.owner._current_context_data,
            screenshot_reference=lambda: self.owner.capture_created_at or 'agent-no-capture',
            config_provider=lambda: self.owner.config, skill_registry=self.owner.skill_registry,
            workflow_storage=self.owner.workflow_storage, integration_service=self.owner.integration_service,
            extension_service=self.owner.extension_service, workflow_bridge=self.owner.workflow_bridge,
            current_skill_result=lambda: self.owner.last_result, user_input=self.ask_user,
            current_image=lambda: self.owner.image_bytes,
            desktop_validator=self.owner.desktop_controller.service.validate_sources)
        self.service = AgentMissionService(self.store, self.services, self.owner.execution_journal,
            progress=self.progress_changed.emit)
        self.service.additional_guard = self._guard

    def _guard(self, session_id):
        if not self._enabled():
            raise ValueError('Agent beta is disabled or app is stopping.')
        expected = self.mission_revisions.get(session_id, self.pending_revision)
        if expected is not None and expected != self.input_revision:
            raise ValueError('Mission inputs changed after review.')
        session = self.store.get_session(session_id)
        if self.view_plan_id and self.view_plan_id != session.current_plan_id:
            raise ValueError('Historical plan versions are read-only.')
        if session.context_scope != 'current_only' and not self.owner.config.visual_memory_enabled:
            raise ValueError('Visual Memory permission was disabled.')

    def refresh_enabled(self):
        self.page.set_enabled(self._enabled())
        try:
            self._ensure()
            if self.store:
                self.page.set_history(self.store.sessions())
        except Exception:
            self.page.set_enabled(False)
            self.page.show_error('Mission history is unavailable. Existing files are preserved.')

    def _start(self, operation):
        if self.busy or not self._enabled():
            return
        self._ensure()
        worker = _Job(operation)
        self.worker = worker
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.page.set_busy(True)
        self.pool.start(worker)

    def create(self, payload):
        if self.busy or not self._enabled():
            return
        self._ensure()
        payload = dict(payload)
        context = self.owner.context_session if payload.get('include_conversation') else None
        conversation = tuple(context.history) if context else ()
        try:
            desktop = self.owner.desktop_controller.sources() if payload.get('include_desktop') else ()
        except ValueError as error:
            self.page.show_error(str(error))
            return
        self.pending_revision = self.input_revision
        self.session_id = None
        self.view_plan_id = None
        self.page.begin_planning()
        self._start(lambda cancelled: self.service.create(payload, cancelled=cancelled,
            conversation=conversation, desktop_sources=desktop))

    @Slot(object, object, object)
    def _finished(self, worker, result, error):
        if worker is not self.worker:
            return
        self.worker = None
        self.pending_revision = None
        self.page.set_busy(False)
        if hasattr(result, 'session_id'):
            self.session_id = result.session_id
            self.view_plan_id = None
        if self.session_id:
            self.refresh(result if isinstance(result, dict) else None)
        elif self.store:
            self.page.set_history(self.store.sessions())
        if error:
            self.page.show_error(error)
        elif self.session_id:
            self._inputs_changed()
            self.mission_revisions[self.session_id] = self.input_revision

    @Slot(object)
    def _progress(self, report):
        if isinstance(report, dict) and report.get('session_id'):
            self.session_id = report['session_id']
            self.view_plan_id = None
            if report.get('status') == 'planning' and self.worker and self.worker.cancelled.is_set():
                self.service.cancel(self.session_id)
            self.refresh(report)

    def refresh(self, report=None):
        if not self.store or not self.session_id:
            return
        session = self.store.get_session(self.session_id)
        plan_id = self.view_plan_id or session.current_plan_id
        plan = self.store.get_plan(plan_id) if plan_id else None
        states = self.store.step_states(plan_id) if plan else {}
        validation = None
        descriptions = {}
        if plan:
            try:
                if plan.plan_id != session.current_plan_id:
                    raise ValueError('Historical version')
                _, _, registry, _ = self.service.runtime(self.session_id)
                validation = AgentPlanValidator(registry).validate(plan, session, private=self.service._private(),
                    **self.store.validation_context(plan))
                descriptions = {step.capability_id: capability.description for step in plan.steps
                    if (capability := registry.get(step.capability_id)) is not None}
            except Exception:
                from src.agent.validation import PlanValidationResult
                message = ('Historical plan version is read-only.' if plan.plan_id != session.current_plan_id else
                    'Sources or permissions changed; create a new plan.')
                validation = PlanValidationResult(False, (message,))
        if report is None:
            if plan_id == session.current_plan_id:
                report = AgentExecutor(self.store, None, None).report(self.session_id)
            else:
                report = {'session_id': self.session_id, 'plan_id': plan_id,
                    'version': plan.version if plan else None, 'status': 'historical_read_only', 'steps': states}
        report = dict(report, elapsed_runtime_seconds=session.elapsed_runtime_seconds,
            max_runtime_minutes=session.constraints.max_runtime_minutes)
        self.page.set_mission(session, plan, states, validation, report, capability_descriptions=descriptions)
        self.page.set_versions(self.store.plans(self.session_id), session.current_plan_id, plan_id)
        self.page.set_history(self.store.sessions())

    def approve_safe(self):
        try:
            self._guard(self.session_id)
            self.service.approve_safe(self.session_id)
            self.refresh()
            self.page.show_status('Safe steps approved for this plan. Select Execute Plan when ready.')
        except Exception:
            self.page.show_error('Approval blocked. Recheck sources, permissions and plan validation.')

    def approve_step(self, step_id):
        try:
            self._guard(self.session_id)
            step, inputs = self.service.resolved_inputs(self.session_id, step_id)
            _, _, registry, _ = self.service.runtime(self.session_id)
            capability = registry.get(step.capability_id)
            if capability is None or not capability.availability:
                raise ValueError('The reviewed capability is unavailable.')
            box = QMessageBox(self.page)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setWindowTitle('Review one Agent step')
            box.setText(f'{step.title}\nRisk: {capability.risk_level}\nCapability: {step.capability_id}\n\n{capability.description}\n\nApprove these exact inputs once?')
            box.setDetailedText(json.dumps(inputs, ensure_ascii=False, indent=2))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() == QMessageBox.StandardButton.Yes:
                self._guard(self.session_id)
                _, _, fresh_registry, _ = self.service.runtime(self.session_id)
                fresh = fresh_registry.get(step.capability_id)
                if fresh is None or fresh.fingerprint != capability.fingerprint:
                    raise ValueError('The capability changed while its effects were being reviewed.')
                self.service.approve_step(self.session_id, step_id, inputs)
                self.refresh()
        except Exception:
            self.page.show_error('Step cannot be approved yet. Execute its approved dependencies and review the resolved inputs.')

    def execute(self):
        if self.session_id:
            self._start(lambda _: self.service.execute(self.session_id))

    def pause(self):
        if self.session_id and self.service:
            self.service.pause(self.session_id)
            self.page.show_status('Pause requested. Paused after the current operation finishes.')

    def cancel(self):
        if self.worker:
            self.worker.cancelled.set()
        if self.session_id and self.service:
            self.service.cancel(self.session_id)
            if not self.busy:
                self.refresh()

    def resume(self):
        if self.session_id:
            self._start(lambda _: self.service.execute(self.session_id, resume=True))

    def replan(self):
        if self.session_id:
            self._start(lambda cancelled: self.service.replan(self.session_id, cancelled=cancelled))

    def edit(self, text):
        if self.session_id:
            # Edited plan validation is a new review operation, never an execution.
            self._start(lambda _: self.service.edit(self.session_id, text))

    def open_history(self, session_id):
        if self.busy:
            return
        self.session_id = session_id
        self.view_plan_id = None
        self.mission_revisions.pop(session_id, None)
        try:
            metadata = self.store.context(session_id)
            self.page._selected = set(metadata['selected_memory_ids'])
            self.page.refresh_memories()
            self.page.include_current.setChecked(metadata['include_current'])
            self.page.include_conversation.setChecked(metadata['conversation_included'])
            self.page.include_desktop.setChecked(False)
        except (ValueError, KeyError):
            # Saved evidence remains readable when live context cannot be restored.
            self.page._selected = set()
            self.page.refresh_memories()
        self.refresh()
        self._inputs_changed()
        self.mission_revisions[session_id] = self.input_revision

    def open_version(self, plan_id):
        if self.busy or not self.store or not self.session_id:
            return
        plan = self.store.get_plan(plan_id)
        if plan.session_id != self.session_id:
            self.page.show_error('Select a plan version from this mission.')
            return
        session = self.store.get_session(self.session_id)
        self.view_plan_id = plan_id if plan_id != session.current_plan_id else None
        self.refresh()

    def delete_history(self, session_id):
        if self.busy:
            return
        try:
            self.store.delete_session(session_id)
            self.service.contexts.pop(session_id, None)
            if self.session_id == session_id:
                self.session_id = None
                self.view_plan_id = None
                self.page.begin_planning()
                self.page.show_status('Mission history deleted.')
            self.page.set_history(self.store.sessions())
        except Exception:
            self.page.show_error('Stop the mission before deleting its history.')

    def export_history(self, session_id):
        if self.busy:
            return
        destination, _ = QFileDialog.getSaveFileName(self.page, 'Export Agent mission', '', 'JSON (*.json)')
        if destination:
            from pathlib import Path
            from src.beta.flags import require_plain_path
            try:
                target = require_plain_path(Path(destination))
                target.write_text(json.dumps(self.store.export_session(session_id), ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                self.page.show_error('Unable to export mission history to the selected file.')

    def ask_user(self, question, *, cancelled=None, guard=None):
        request = {'question': question, 'future': Future(), 'cancelled': cancelled, 'guard': guard}
        self.input_requested.emit(request)
        try:
            while not request['future'].done():
                if self.stopped or cancelled and cancelled():
                    raise ValueError('Question cancelled.')
                if guard:
                    guard()
                try:
                    return request['future'].result(.1)
                except TimeoutError:
                    pass
            return request['future'].result()
        except Exception:
            request['future'].cancel()
            raise

    @Slot(object)
    def _input(self, request):
        if request['future'].done() or self.stopped:
            return
        dialog = QInputDialog(self.page)
        self.input_dialog = dialog
        dialog.setWindowTitle('Agent needs your input')
        dialog.setLabelText(request['question'])
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        timer = QTimer(dialog)
        timer.setInterval(100)
        timer.timeout.connect(lambda: dialog.reject() if request['future'].done() or self.stopped else None)
        timer.start()
        accepted = bool(dialog.exec())
        timer.stop()
        self.input_dialog = None
        if not request['future'].done():
            request['future'].set_result({'answer': dialog.textValue()[:2000], 'confirmed': accepted})
        dialog.deleteLater()

    def stop(self):
        self.stopped = True
        if self.worker:
            self.worker.cancelled.set()
        if self.service:
            for cancel, pause in self.service.controls.values():
                cancel.set()
        if self.input_dialog:
            self.input_dialog.reject()
