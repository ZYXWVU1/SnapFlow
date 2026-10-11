"""Mission Control: inspect an immutable plan before issuing execution requests."""
import json
import sqlite3
from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSpinBox)

from src.agent.models import thaw
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.pages.common import ScrollPage


def _display(value):
    if hasattr(value, 'to_dict'):
        value = value.to_dict()
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(thaw(value), ensure_ascii=False, indent=2, default=str)
    return str(value)


class AgentPage(ScrollPage):
    create_plan_requested = Signal(object)
    approve_safe_requested = Signal()
    approve_step_requested = Signal(str)
    execute_requested = Signal()
    pause_requested = Signal()
    cancel_requested = Signal()
    resume_requested = Signal()
    replan_requested = Signal()
    edit_plan_requested = Signal(str)
    history_open_requested = Signal(str)
    history_delete_requested = Signal(str)
    history_export_requested = Signal(str)
    version_open_requested = Signal(str)
    enable_requested = Signal()
    desktop_context_requested = Signal()

    def __init__(self, memory_store=None, parent=None):
        super().__init__(parent)
        self.memory_store = memory_store
        self._enabled = False
        self._busy = False
        self._private = False
        self._refreshing = False
        self._draft_dirty = False
        self._session = self._plan = self._validation = None
        self._fingerprint = None
        self._states = {}
        self._selected = set()
        self.content.addWidget(PageHeader('Agent · Mission Control',
            'Describe a goal, review a plan, then choose when to execute.'))
        self.beta_notice = self._label('Agent Missions is a beta feature and is off. Enable it in Settings to begin.')
        self.content.addWidget(self.beta_notice)
        self.enable_button = AppButton('Open Beta Settings', variant='secondary')
        self.enable_button.clicked.connect(self.enable_requested)
        self.content.addWidget(self.enable_button)
        goal_card = Card()
        goal_card.content.addWidget(self._label('What would you like to accomplish?'))
        self.goal = QLineEdit()
        self.goal.setMaxLength(500)
        self.goal.setPlaceholderText('Describe a goal…')
        goal_card.content.addWidget(self.goal)
        self.mode = QComboBox()
        self.mode.addItem('Guided execution', 'guided')
        self.mode.addItem('Plan only', 'plan_only')
        self.mode.currentIndexChanged.connect(self._update_controls)
        goal_card.content.addWidget(self.mode)
        self.include_current = QCheckBox('Include current screenshot analysis')
        self.include_conversation = QCheckBox('Include current Context Assistant conversation')
        goal_card.content.addWidget(self.include_current)
        goal_card.content.addWidget(self.include_conversation)
        self.include_desktop = QCheckBox('Include reviewed Desktop Context (plan/output temporary until Clear/Stop)')
        goal_card.content.addWidget(self.include_desktop)
        desktop_button = AppButton('Manage Desktop Context', variant='secondary')
        desktop_button.clicked.connect(self.desktop_context_requested)
        goal_card.content.addWidget(desktop_button)
        goal_card.content.addWidget(self._label('Select up to eight saved memories. Only checked records are included.'))
        self.memory_query = QLineEdit()
        self.memory_query.setPlaceholderText('Find saved memories to select…')
        self.memory_query.returnPressed.connect(self.refresh_memories)
        goal_card.content.addWidget(self.memory_query)
        self.memory_search_button = AppButton('Find Memories')
        self.memory_search_button.clicked.connect(self.refresh_memories)
        goal_card.content.addWidget(self.memory_search_button)
        self.memory_list = QListWidget()
        self.memory_list.setMinimumHeight(120)
        self.memory_list.setMaximumHeight(180)
        self.memory_list.itemChanged.connect(self._memory_changed)
        goal_card.content.addWidget(self.memory_list)
        self.selected_summary = self._label('No saved memories selected.')
        goal_card.content.addWidget(self.selected_summary)
        self.permissions = {}
        for name, label in (
            ('allow_remote_ai', 'Allow remote AI (selected text may leave this device)'),
            ('allow_memory_search', 'Allow Agent to search Visual Memory'),
            ('allow_remote_mcp', 'Allow remote MCP access'),
            ('allow_local_writes', 'Allow local writes'),
            ('allow_external_writes', 'Allow external writes')):
            checkbox = QCheckBox(label)
            self.permissions[name] = checkbox
            setattr(self, name, checkbox)
            goal_card.content.addWidget(checkbox)
        goal_card.content.addWidget(self._label('Permissions allow proposals; each executable step still requires validated approval. Private Mode overrides remote permissions.'))
        budgets = QFormLayout()
        self.budgets = {}
        for name, label, low, high, default in (
            ('max_steps', 'Maximum steps', 1, 50, 10),
            ('max_external_writes', 'Maximum external writes', 0, 10, 1),
            ('max_ai_calls', 'Maximum AI calls', 0, 50, 5),
            ('max_replans', 'Maximum replans', 0, 5, 2),
            ('max_runtime_minutes', 'Maximum runtime (minutes)', 1, 60, 10)):
            editor = QSpinBox()
            editor.setRange(low, high)
            editor.setValue(default)
            self.budgets[name] = editor
            budgets.addRow(label, editor)
        self.limit_cost = QCheckBox('Limit estimated cost')
        self.max_estimated_cost = QDoubleSpinBox()
        self.max_estimated_cost.setRange(0, 10000)
        self.max_estimated_cost.setDecimals(4)
        self.max_estimated_cost.setEnabled(False)
        self.limit_cost.toggled.connect(self.max_estimated_cost.setEnabled)
        budgets.addRow(self.limit_cost, self.max_estimated_cost)
        goal_card.content.addLayout(budgets)
        self.create_plan_button = AppButton('Create Plan', variant='primary')
        self.create_plan_button.clicked.connect(self._create_plan)
        goal_card.content.addWidget(self.create_plan_button)
        self.content.addWidget(goal_card)

        mission = Card()
        self.status = self._label('No mission yet. Create a plan to begin.')
        mission.content.addWidget(self.status)
        mission.content.addWidget(self._label('Saved plan versions'))
        self.version_selector = QComboBox()
        self.version_selector.currentIndexChanged.connect(self._open_version)
        mission.content.addWidget(self.version_selector)
        self.plan_review = self._text(read_only=True)
        self.plan_review.setPlaceholderText('Plan risks, inputs, effects, costs, and assumptions appear here.')
        mission.content.addWidget(self.plan_review)
        self.steps = QListWidget()
        self.steps.setMinimumHeight(150)
        self.steps.currentItemChanged.connect(self._update_controls)
        mission.content.addWidget(self.steps)
        self.reviewed = QCheckBox('I have reviewed this plan and its risks, inputs, effects, and costs')
        self.reviewed.toggled.connect(self._update_controls)
        mission.content.addWidget(self.reviewed)
        for name, label, signal in (
            ('approve_safe', 'Approve Safe Steps', self.approve_safe_requested),
            ('approve_step', 'Approve Selected Step', None),
            ('execute', 'Execute Plan', self.execute_requested),
            ('pause', 'Pause', self.pause_requested),
            ('cancel', 'Cancel Mission', self.cancel_requested),
            ('resume', 'Resume After Review', self.resume_requested),
            ('replan', 'Replan', self.replan_requested)):
            button = AppButton(label, variant='primary' if name == 'execute' else 'secondary')
            setattr(self, name + '_button', button)
            if signal is not None:
                button.clicked.connect(signal)
            mission.content.addWidget(button)
        self.approve_step_button.clicked.connect(self._approve_step)
        mission.content.addWidget(self._label('Edit plan JSON (saving creates a new version and invalidates approvals)'))
        self.plan_editor = self._text(read_only=False)
        mission.content.addWidget(self.plan_editor)
        self.edit_plan_button = AppButton('Validate Edited Plan', variant='secondary')
        self.edit_plan_button.clicked.connect(lambda: self.edit_plan_requested.emit(self.plan_editor.toPlainText()))
        mission.content.addWidget(self.edit_plan_button)
        mission.content.addWidget(self._label('Evidence and step outputs'))
        self.evidence = self._text(read_only=True)
        mission.content.addWidget(self.evidence)
        mission.content.addWidget(self._label('Final report · verification is separate from tool success'))
        self.report = self._text(read_only=True)
        mission.content.addWidget(self.report)
        self.content.addWidget(mission)

        history_card = Card()
        history_card.content.addWidget(self._label('Recent Missions · local history'))
        self.history = QListWidget()
        self.history.currentItemChanged.connect(self._update_controls)
        self.history.itemDoubleClicked.connect(lambda item: self.history_open_requested.emit(item.data(Qt.ItemDataRole.UserRole)))
        history_card.content.addWidget(self.history)
        for name, label, signal in (
            ('open', 'Open Mission', self.history_open_requested),
            ('delete', 'Delete Selected Mission', self.history_delete_requested),
            ('export', 'Export Selected Mission', self.history_export_requested)):
            button = AppButton(label)
            setattr(self, 'history_' + name + '_button', button)
            button.clicked.connect(lambda checked=False, target=signal: self._history_request(target))
            history_card.content.addWidget(button)
        self.content.addWidget(history_card)
        self.content.addStretch(1)
        self.refresh_memories()
        self.goal.textChanged.connect(self._invalidate_review)
        self.mode.currentIndexChanged.connect(self._invalidate_review)
        self.plan_editor.textChanged.connect(self._invalidate_review)
        for checkbox in (self.include_current, self.include_conversation, self.include_desktop, self.limit_cost, *self.permissions.values()):
            checkbox.toggled.connect(self._invalidate_review)
        for editor in (*self.budgets.values(), self.max_estimated_cost):
            editor.valueChanged.connect(self._invalidate_review)
        self._update_controls()

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    @staticmethod
    def _text(*, read_only):
        editor = QPlainTextEdit()
        editor.setReadOnly(read_only)
        editor.setMinimumHeight(120)
        editor.setMaximumHeight(240)
        return editor

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.beta_notice.setText('Agent Missions beta · review every plan before execution.' if enabled else
            'Agent Missions is a beta feature and is off. Enable it in Settings to begin.')
        self.enable_button.setVisible(not enabled)
        self._update_controls()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_controls()

    def apply_defaults(self, config):
        if self._session is not None or self._plan is not None or self._busy:
            return
        self.mode.setCurrentIndex(self.mode.findData(getattr(config, 'agent_default_mode', 'guided')))
        for name, fallback in (('max_steps', 10), ('max_ai_calls', 5),
                ('max_external_writes', 1), ('max_runtime_minutes', 10)):
            self.budgets[name].setValue(getattr(config, 'agent_' + name, fallback))
        self.allow_memory_search.setChecked(getattr(config, 'agent_allow_memory_search', False))
        self.allow_remote_mcp.setChecked(getattr(config, 'agent_allow_mcp_tools', False) and not self._private)

    def begin_planning(self):
        self._session = self._plan = self._validation = None
        self._fingerprint = None
        self._states = {}
        self._draft_dirty = False
        self.reviewed.setChecked(False)
        for editor in (self.plan_review, self.plan_editor, self.evidence, self.report):
            editor.clear()
        self.steps.clear()
        self.set_versions([], None)
        self.show_status('Planning with the explicitly selected context…')
        self._update_controls()

    def set_privacy_state(self, private):
        changed = self._private != bool(private)
        self._private = bool(private)
        for name in ('allow_remote_ai', 'allow_remote_mcp', 'allow_external_writes'):
            checkbox = self.permissions[name]
            if private:
                checkbox.setChecked(False)
            checkbox.setEnabled(not private)
        if changed:
            self._invalidate_review()

    def _invalidate_review(self, *_):
        if self._refreshing or self._plan is None:
            return
        self._draft_dirty = True
        self.reviewed.setChecked(False)
        self.show_status('Goal, context, permissions, or plan changed. Create or validate a new plan before execution.')
        self._update_controls()

    def show_status(self, text):
        self.status.setText(str(text))

    def show_error(self, text):
        self.show_status(text)

    def selected_memory_ids(self):
        return sorted(self._selected)

    def refresh_memories(self):
        if self.memory_store is None:
            self.memory_list.clear()
            self.memory_search_button.setEnabled(False)
            return
        try:
            original_selection = set(self._selected)
            retained = []
            for memory_id in sorted(self._selected):
                record = self.memory_store.get_record(memory_id)
                if record is None:
                    self._selected.discard(memory_id)
                else:
                    retained.append(record)
            hits = self.memory_store.search(self.memory_query.text(), limit=20)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.show_error(f'Unable to read saved memories: {exc}')
            return
        self.memory_list.blockSignals(True)
        self.memory_list.clear()
        seen = set()
        for hit in (*retained, *hits):
            memory_id = getattr(hit, 'memory_id', getattr(hit, 'id', None))
            if not memory_id or memory_id in seen:
                continue
            seen.add(memory_id)
            item = QListWidgetItem(str(hit.title))
            item.setData(Qt.ItemDataRole.UserRole, memory_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if memory_id in self._selected else Qt.CheckState.Unchecked)
            self.memory_list.addItem(item)
        self.memory_list.blockSignals(False)
        self._selection_summary()
        if original_selection != self._selected:
            self._invalidate_review()

    def select_memory(self, memory_id):
        if memory_id not in self._selected and len(self._selected) >= 8:
            self.show_error('Select at most eight saved memories.')
            return
        if self.memory_store is None or self.memory_store.get_record(memory_id) is None:
            self.show_error('This saved memory is unavailable.')
            return
        self._selected.add(memory_id)
        self._invalidate_review()
        self.refresh_memories()

    def _memory_changed(self, item):
        original_selection = set(self._selected)
        memory_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            if len(self._selected) >= 8 and memory_id not in self._selected:
                self.memory_list.blockSignals(True)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.memory_list.blockSignals(False)
                self.show_error('Select at most eight saved memories.')
            else:
                self._selected.add(memory_id)
        else:
            self._selected.discard(memory_id)
        self._selection_summary()
        if original_selection != self._selected:
            self._invalidate_review()

    def _selection_summary(self):
        self.selected_summary.setText(f'{len(self._selected)} saved memories selected.' if self._selected else 'No saved memories selected.')

    def _create_plan(self):
        goal = self.goal.text().strip()
        if not goal:
            self.show_error('Describe a goal before creating a plan.')
            return
        self.refresh_memories()
        self.create_plan_requested.emit(self.request_payload())

    def request_payload(self):
        """Current visible goal inputs for the controller's stale-input guard."""
        constraints = {name: checkbox.isChecked() for name, checkbox in self.permissions.items()}
        constraints.update({name: editor.value() for name, editor in self.budgets.items()})
        constraints['max_estimated_cost'] = self.max_estimated_cost.value() if self.limit_cost.isChecked() else None
        return {'goal': self.goal.text().strip(), 'mode': self.mode.currentData(),
            'constraints': constraints, 'selected_memory_ids': self.selected_memory_ids(),
            'include_current': self.include_current.isChecked(),
            'include_conversation': self.include_conversation.isChecked(),
            'include_desktop': self.include_desktop.isChecked()}

    def set_versions(self, plans, current_plan_id, selected_plan_id=None):
        self.version_selector.blockSignals(True)
        self.version_selector.clear()
        for plan in plans:
            label = f'Version {plan.version}' + (' · current' if plan.plan_id == current_plan_id else ' · read-only')
            self.version_selector.addItem(label, plan.plan_id)
        selected = selected_plan_id or current_plan_id
        self.version_selector.setCurrentIndex(self.version_selector.findData(selected))
        self.version_selector.blockSignals(False)
        self._update_controls()

    def _open_version(self, *_):
        plan_id = self.version_selector.currentData()
        if plan_id and not self._busy:
            self.version_open_requested.emit(plan_id)

    def set_mission(self, session, plan, states, validation=None, report=None, capability_descriptions=None):
        self._refreshing = True
        self._session, self._plan, self._states, self._validation = session, plan, states or {}, validation
        fingerprint = plan.fingerprint if plan else None
        if self._fingerprint != fingerprint:
            self.reviewed.setChecked(False)
            self._fingerprint = fingerprint
            self._draft_dirty = False
            self.goal.setText(session.goal)
            self.mode.setCurrentIndex(self.mode.findData(session.execution_mode))
            for name, checkbox in self.permissions.items():
                checkbox.setChecked(getattr(session.constraints, name))
            for name, editor in self.budgets.items():
                editor.setValue(getattr(session.constraints, name))
            cost = session.constraints.max_estimated_cost
            self.limit_cost.setChecked(cost is not None)
            self.max_estimated_cost.setValue(cost or 0)
            if self._private:
                for name in ('allow_remote_ai', 'allow_remote_mcp', 'allow_external_writes'):
                    self.permissions[name].setChecked(False)
        self.status.setText(f'Goal: {session.goal}\nStatus: {session.status} · Mode: {session.execution_mode}')
        self.steps.clear()
        lines, evidence = [], []
        lines.append(f'Elapsed runtime: {getattr(session, "elapsed_runtime_seconds", 0):.1f} seconds · Limit: {getattr(session.constraints, "max_runtime_minutes", 10)} minutes')
        if plan:
            lines.append(f'Plan version {plan.version} · {plan.plan_id}\nGoal: {plan.goal_summary}')
            if session.current_plan_id not in (None, plan.plan_id):
                lines.append('Historical plan version · read-only. Select the current version to review execution.')
            lines.extend(f'Assumption: {text}' for text in plan.assumptions)
            lines.extend(f'Warning: {text}' for text in plan.warnings)
            for step in plan.steps:
                state = self._states.get(step.step_id, {})
                status = state.get('status', 'pending')
                item = QListWidgetItem(f'{step.order}. {step.title} · {status} · {step.risk_level}')
                item.setData(Qt.ItemDataRole.UserRole, step.step_id)
                self.steps.addItem(item)
                effects = 'Read-only; no writes.' if step.risk_level == 'read_only' else f'Potential {step.risk_level} effects; review approval at execution.'
                lines.append(f'\n{step.order}. {step.title}\n{step.description}\nRisk: {step.risk_level}\nCapability: {step.capability_id or step.step_type}\nUses: {_display(step.input_bindings)}\nEffects: {effects}\nApproval: {step.approval_mode}\nCost: {step.estimated_cost if step.estimated_cost is not None else "unknown"}\nDependencies: {", ".join(step.dependencies) or "none"}')
                preview = (capability_descriptions or {}).get(step.capability_id)
                if preview:
                    lines.append('Registered capability review: ' + preview)
                if state.get('evidence') is not None or state.get('output') is not None:
                    evidence.append(f'{step.step_id} · {status}\nEvidence: {_display(state.get("evidence"))}\nOutput: {_display(state.get("output"))}')
            if not self._draft_dirty:
                self.plan_editor.setPlainText(_display(plan))
        else:
            self.plan_editor.clear()
        if validation is not None:
            lines.append('\nValidation: ' + ('passed' if validation.valid else 'blocked'))
            lines.extend(f'Error: {error}' for error in validation.errors)
            lines.extend(f'Warning: {warning}' for warning in validation.warnings)
            lines.append(f'Total estimated cost: {validation.estimated_cost if validation.estimated_cost is not None else "unknown"}')
            lines.append(f'Pending AI calls: {validation.ai_calls} · Pending external writes: {validation.external_writes}')
        self.plan_review.setPlainText('\n'.join(lines))
        self.evidence.setPlainText('\n\n'.join(evidence))
        self.report.setPlainText(_display(report) if report is not None else '')
        self._refreshing = False
        self._update_controls()

    def _update_controls(self, *_):
        if not hasattr(self, 'history_export_button'):
            return
        available = self._enabled and not self._busy
        status = self._session.status if self._session else ''
        guided = bool(self._session and self._session.execution_mode == 'guided')
        current = bool(self._plan and (self._session.current_plan_id in (None, self._plan.plan_id)))
        valid = bool(self._validation and self._validation.valid)
        review = available and current and valid and self.reviewed.isChecked() and not self._draft_dirty
        actionable = status in ('awaiting_approval', 'paused', 'blocked', 'interrupted')
        self.create_plan_button.setEnabled(available and status != 'running')
        self.reviewed.setEnabled(available and current and status != 'running')
        self.approve_safe_button.setEnabled(review and guided and actionable)
        item = self.steps.currentItem()
        step = next((step for step in self._plan.steps if item and step.step_id == item.data(Qt.ItemDataRole.UserRole)), None) if self._plan else None
        self.approve_step_button.setEnabled(review and guided and actionable and bool(step and step.approval_mode != 'disabled'))
        self.execute_button.setEnabled(review and guided and status == 'awaiting_approval')
        self.pause_button.setEnabled(self._enabled and status == 'running')
        self.cancel_button.setEnabled(self._enabled and (self._busy or status in ('planning', 'replanning', 'running', 'paused', 'awaiting_approval', 'blocked', 'interrupted')))
        self.resume_button.setEnabled(review and guided and status in ('paused', 'interrupted', 'blocked'))
        self.replan_button.setEnabled(available and current and status != 'running')
        self.edit_plan_button.setEnabled(available and current and status != 'running')
        self.plan_editor.setReadOnly(not current or not self._enabled or self._busy)
        self.version_selector.setEnabled(not self._busy and self.version_selector.count() > 0)
        for button in (self.history_open_button, self.history_delete_button, self.history_export_button):
            button.setEnabled(not self._busy and self.history.currentItem() is not None)

    def _approve_step(self):
        item = self.steps.currentItem()
        if item:
            self.approve_step_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def set_history(self, sessions):
        self.history.clear()
        for session in sessions:
            item = QListWidgetItem(f'{session.goal} · {session.status} · {session.updated_at[:16]}')
            item.setData(Qt.ItemDataRole.UserRole, session.session_id)
            self.history.addItem(item)
        self._update_controls()

    def _history_request(self, signal):
        item = self.history.currentItem()
        if item:
            signal.emit(item.data(Qt.ItemDataRole.UserRole))
