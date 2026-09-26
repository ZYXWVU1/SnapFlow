"""A linear When / If / Then editor driven by Skill and Action metadata."""
from dataclasses import replace
import uuid
from PySide6.QtCore import Qt, Signal, QThreadPool
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from src.skill_actions import ACTIONS
from src.skills.registry import SKILLS
from src.workflows.models import WorkflowCondition, WorkflowDefinition, WorkflowStep, WorkflowTrigger
from src.workflows.validator import WORKFLOW_ACTIONS, field_kind, skill_fields, validate_workflow
from src.integrations.actions import FIELD_KEYS
from .column_mapping import ColumnMappingEditor
from .resource_picker import ResourcePicker
from .resource_worker import ResourceWorker
from src.ui.design.components import AppButton, Card, PageHeader

OPERATORS = ('exists', 'not_exists', 'equals', 'not_equals', 'contains', 'not_contains',
             'greater_than', 'less_than', 'is_empty', 'is_not_empty')
NO_VALUE = {'exists', 'not_exists', 'is_empty', 'is_not_empty'}


class WorkflowEditor(QDialog):
    saved = Signal(object)
    connect_requested = Signal(str)

    def __init__(self, skills, definition=None, parent=None, allow_auto=False, connection_storage=None,
                 integration_service=None):
        super().__init__(parent)
        self.skills, self.original = skills, definition
        self.allow_auto = allow_auto
        self.connection_storage = connection_storage
        self.integration_service = integration_service
        self.resource_jobs = {}
        self.steps = list(definition.steps) if definition else []
        self.condition_rows = []
        self.config_inputs = {}
        self.setWindowTitle('Edit Workflow' if definition else 'Create Workflow')
        self.resize(700, 740)
        root = QVBoxLayout(self)
        self.page_header = PageHeader('Edit Workflow' if definition else 'Create Workflow',
            'When a Skill matches, check conditions and run registered Actions in order.')
        root.addWidget(self.page_header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        form = QFormLayout()
        self.name = QLineEdit(definition.name if definition else '')
        self.description = QLineEdit(definition.description if definition else '')
        self.trigger = QComboBox()
        self.refresh_skills()
        if definition:
            self.trigger.setCurrentIndex(self.trigger.findData(definition.trigger.skill_id))
        form.addRow('Workflow name', self.name)
        form.addRow('Description', self.description)
        form.addRow('When this Visual Skill matches', self.trigger)
        identity_card = Card()
        identity_card.content.addLayout(form)
        layout.addWidget(identity_card)
        conditions_card = Card()
        conditions_card.content.addWidget(QLabel('Only run if (all conditions must pass)'))
        self.conditions_layout = QVBoxLayout()
        conditions_card.content.addLayout(self.conditions_layout)
        add_condition = AppButton('+ Add Condition')
        add_condition.clicked.connect(self.add_condition)
        conditions_card.content.addWidget(add_condition)
        layout.addWidget(conditions_card)
        steps_card = Card()
        steps_card.content.addWidget(QLabel('Then perform these Actions in order'))
        self.step_list = QListWidget()
        self.step_list.currentRowChanged.connect(self.show_config)
        steps_card.content.addWidget(self.step_list)
        action_row = QHBoxLayout()
        self.action_choice = QComboBox()
        action_row.addWidget(self.action_choice)
        for title, callback in [('+ Add Action', self.add_step), ('Up', lambda: self.move_step(-1)),
                                ('Down', lambda: self.move_step(1)), ('Remove', self.remove_step)]:
            button = AppButton(title)
            button.clicked.connect(callback)
            action_row.addWidget(button)
        steps_card.content.addLayout(action_row)
        layout.addWidget(steps_card)
        config_card = Card()
        config_card.content.addWidget(QLabel('Action settings'))
        self.config_box = QWidget()
        self.config_layout = QFormLayout(self.config_box)
        config_card.content.addWidget(self.config_box)
        self.integration_status = QLabel()
        self.integration_status.setWordWrap(True)
        self.integration_status.setProperty('role', 'muted')
        config_card.content.addWidget(self.integration_status)
        self.integration_connect = AppButton('Connect integration')
        self.integration_id_for_connect = None
        self.integration_connect.clicked.connect(lambda: self.connect_requested.emit(self.integration_id_for_connect)
            if self.integration_id_for_connect else None)
        config_card.content.addWidget(self.integration_connect)
        self.integration_connect.hide()
        layout.addWidget(config_card)
        behavior_card = Card()
        behavior_card.content.addWidget(QLabel('Run behavior'))
        self.failure = QComboBox()
        self.failure.addItem('Stop on failure', 'stop')
        self.failure.addItem('Continue after failure', 'continue')
        self.failure.setCurrentIndex(self.failure.findData(definition.failure_policy if definition else 'stop'))
        behavior_card.content.addWidget(self.failure)
        self.enabled = QCheckBox('Enable this Workflow')
        self.enabled.setChecked(definition.enabled if definition else True)
        behavior_card.content.addWidget(self.enabled)
        self.mode = QComboBox()
        self.mode.addItem('Suggest · click Run after a Skill match', False)
        if allow_auto:
            self.mode.addItem('Auto · run after a Skill match', True)
        self.mode.setCurrentIndex(self.mode.findData(definition.auto_run if definition and allow_auto else False))
        behavior_card.content.addWidget(self.mode)
        layout.addWidget(behavior_card)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        bottom = QHBoxLayout()
        bottom.addStretch()
        save = AppButton('Save Workflow', variant='primary')
        save.clicked.connect(self.submit)
        cancel = AppButton('Cancel', variant='ghost')
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)
        self.trigger.currentIndexChanged.connect(self.trigger_changed)
        if definition:
            for condition in definition.conditions:
                self.add_condition(condition)
        self.trigger_changed()
        self.refresh_steps()

    def refresh_skills(self):
        self.trigger.clear()
        for skill in SKILLS.values():
            self.trigger.addItem('Built-in · ' + skill.title, skill.id)
        for skill in self.skills.enabled_definitions():
            self.trigger.addItem('My Skill · ' + skill.name, skill.id)

    def available_actions(self):
        skill = self.skills.get(self.trigger.currentData())
        keys = list(skill.action_ids) + sorted(WORKFLOW_ACTIONS) if skill else []
        return [ACTIONS[key] for key in keys if key in ACTIONS]

    def trigger_changed(self):
        self.action_choice.clear()
        for action in self.available_actions():
            self.action_choice.addItem(action.label, action.id)
        for fields, operators, value, _ in self.condition_rows:
            old = fields.currentData()
            fields.clear()
            skill = self.skills.get(self.trigger.currentData())
            for key in skill_fields(skill) if skill else ():
                fields.addItem(key.replace('_', ' ').title(), key)
            fields.setCurrentIndex(max(0, fields.findData(old)))
            self.update_operators(fields, operators, value)

    def update_operators(self, fields, operators, value):
        old = operators.currentData()
        skill = self.skills.get(self.trigger.currentData())
        kind = field_kind(skill_fields(skill).get(fields.currentData(), 'string')) if skill else 'string'
        operators.clear()
        for operator in OPERATORS:
            if operator in ('greater_than', 'less_than') and kind != 'number':
                continue
            if operator in ('contains', 'not_contains') and kind != 'string':
                continue
            if operator in ('equals', 'not_equals') and kind in ('list', 'object'):
                continue
            operators.addItem(operator.replace('_', ' ').title(), operator)
        operators.setCurrentIndex(max(0, operators.findData(old)))
        value.setPlaceholderText('true or false' if kind == 'boolean' else 'Number' if kind == 'number' else 'Value')
        value.setVisible(operators.currentData() not in NO_VALUE)

    def add_condition(self, condition=None):
        row = QWidget()
        line = QHBoxLayout(row)
        fields, operators, value = QComboBox(), QComboBox(), QLineEdit()
        remove = QPushButton('Remove')
        for widget in (fields, operators, value, remove):
            line.addWidget(widget)
        self.conditions_layout.addWidget(row)
        self.condition_rows.append((fields, operators, value, row))
        skill = self.skills.get(self.trigger.currentData())
        for key in skill_fields(skill) if skill else ():
            fields.addItem(key.replace('_', ' ').title(), key)
        fields.currentIndexChanged.connect(lambda _=None: self.update_operators(fields, operators, value))
        operators.currentIndexChanged.connect(lambda: value.setVisible(operators.currentData() not in NO_VALUE))
        self.update_operators(fields, operators, value)
        if condition:
            fields.setCurrentIndex(fields.findData(condition.field))
            operators.setCurrentIndex(operators.findData(condition.operator))
            value.setText('' if condition.value is None else str(condition.value))
        remove.clicked.connect(lambda: self.remove_condition(row))

    def remove_condition(self, row):
        self.condition_rows = [entry for entry in self.condition_rows if entry[3] is not row]
        self.conditions_layout.removeWidget(row)
        row.deleteLater()

    def add_step(self, action_id=None):
        if not isinstance(action_id, str):
            action_id = self.action_choice.currentData()
        if action_id:
            action = ACTIONS[action_id]
            if action_id == 'google_calendar_create_event':
                config = {'calendar_id': 'primary', 'title': '{title}', 'date_field':
                    'due_date' if 'due_date' in skill_fields(self.skills.get(self.trigger.currentData())) else 'date'}
            elif action_id == 'google_sheets_append_row':
                config = {'spreadsheet_id': '', 'tab': '', 'columns': [], 'create_header': False}
            elif action_id == 'todoist_create_task':
                config = {'title': '{title}', 'priority': 1}
            else:
                config = {key: '' for key in action.config_schema}
            self.steps.append(WorkflowStep('step_' + uuid.uuid4().hex[:12], action_id, config))
            self.refresh_steps()
            self.step_list.setCurrentRow(len(self.steps) - 1)

    def refresh_steps(self):
        selected = self.step_list.currentRow()
        self.step_list.clear()
        for index, step in enumerate(self.steps, 1):
            self.step_list.addItem(f'{index}. {ACTIONS[step.action_id].label if step.action_id in ACTIONS else step.action_id}')
        if self.steps:
            self.step_list.setCurrentRow(min(max(selected, 0), len(self.steps) - 1))

    def save_config(self):
        index = self.step_list.currentRow()
        if index >= 0 and index < len(self.steps) and self.config_inputs:
            self.update_step_config(index)

    def update_step_config(self, index):
        if 0 <= index < len(self.steps):
            old = self.steps[index]
            config = {}
            for key, widget in self.config_inputs.items():
                if isinstance(widget, ColumnMappingEditor):
                    config[key] = widget.value()
                elif isinstance(widget, ResourcePicker):
                    config[key] = widget.value()
                elif isinstance(widget, QComboBox):
                    config[key] = widget.currentData() or ''
                elif isinstance(widget, QCheckBox):
                    config[key] = widget.isChecked()
                elif isinstance(widget, QSpinBox):
                    config[key] = widget.value()
                else:
                    config[key] = widget.text()
            self.steps[index] = replace(old, config=config)

    def show_config(self, index):
        while self.config_layout.rowCount():
            self.config_layout.removeRow(0)
        self.config_inputs = {}
        self.integration_status.hide()
        self.integration_connect.hide()
        self.integration_id_for_connect = None
        if index < 0 or index >= len(self.steps):
            return
        step = self.steps[index]
        action = ACTIONS.get(step.action_id)
        if action:
            if action.integration_id and self.connection_storage is not None:
                connection = self.connection_storage.get(action.integration_id)
                capability = {'google_calendar_create_event': 'google_calendar',
                    'google_sheets_append_row': 'google_sheets',
                    'todoist_create_task': 'todoist_tasks'}[action.id]
                connected = (connection is not None and connection.status == 'connected' and
                             capability in connection.granted_capabilities)
                self.integration_status.setText('Connected' if connected else
                    'Integration is not connected for this Action. Connect it before running the Workflow.')
                self.integration_status.show()
                self.integration_connect.setVisible(not connected)
                self.integration_id_for_connect = action.integration_id
            for key in action.config_schema:
                value = step.config.get(key, '')
                if action.integration_id and key in FIELD_KEYS:
                    field = QComboBox()
                    field.addItem('None', '')
                    skill = self.skills.get(self.trigger.currentData())
                    for name in skill_fields(skill) if skill else ():
                        field.addItem(name.replace('_', ' ').title(), name)
                    field.setCurrentIndex(max(0, field.findData(value)))
                    field.currentIndexChanged.connect(lambda _=None, row=index: self.update_step_config(row))
                elif action.integration_id and key in ('calendar_id', 'project_id', 'tab'):
                    field = ResourcePicker(value if isinstance(value, str) else '')
                    field.changed.connect(lambda row=index: self.update_step_config(row))
                    field.load_requested.connect(lambda row=index, action_id=action.id, resource_key=key:
                        self.load_resources(row, action_id, resource_key))
                elif action.integration_id and key == 'columns':
                    skill = self.skills.get(self.trigger.currentData())
                    field = ColumnMappingEditor(skill_fields(skill) if skill else (), value if isinstance(value, list) else [])
                    field.changed.connect(lambda row=index: self.update_step_config(row))
                elif action.integration_id and key == 'create_header':
                    field = QCheckBox('Create headers if the tab is empty')
                    field.setChecked(value is True)
                    field.toggled.connect(lambda _=None, row=index: self.update_step_config(row))
                elif action.integration_id and key in ('priority', 'reminder_minutes'):
                    field = QSpinBox()
                    field.setRange(1 if key == 'priority' else 0, 4 if key == 'priority' else 40320)
                    field.setValue(value if type(value) is int else 1 if key == 'priority' else 0)
                    field.valueChanged.connect(lambda _=None, row=index: self.update_step_config(row))
                else:
                    field = QLineEdit(str(value))
                    field.textChanged.connect(lambda _text, row=index: self.update_step_config(row))
                self.config_layout.addRow(key.replace('_', ' ').title(), field)
                self.config_inputs[key] = field

    def load_resources(self, index, action_id, key):
        if self.integration_service is None:
            self.integration_status.setText('Connect the integration to load resources.')
            self.integration_status.show()
            return
        if index in self.resource_jobs:
            return
        self.save_config()
        picker = self.config_inputs.get(key)
        if not isinstance(picker, ResourcePicker):
            return
        worker = ResourceWorker(self.integration_service, action_id, dict(self.steps[index].config))
        worker.signals.finished.connect(lambda current, options, error:
            self.resources_finished(index, key, current, options, error))
        self.resource_jobs[index] = worker
        picker.set_busy(True)
        QThreadPool.globalInstance().start(worker)

    def resources_finished(self, index, key, worker, options, error):
        if self.resource_jobs.get(index) is not worker:
            return
        self.resource_jobs.pop(index, None)
        if self.step_list.currentRow() != index:
            return
        picker = self.config_inputs.get(key)
        if isinstance(picker, ResourcePicker):
            picker.set_busy(False)
            if error:
                self.integration_status.setText(error)
                self.integration_status.show()
            else:
                picker.set_options(options)
                self.integration_status.setText(f'{len(options)} resources available.' if options else
                    'No resources found. Enter a resource ID manually or check the account.')
                self.integration_status.show()

    def move_step(self, direction):
        self.save_config()
        index = self.step_list.currentRow()
        target = index + direction
        if index < 0 or target < 0 or target >= len(self.steps):
            return
        self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
        self.refresh_steps()
        self.step_list.setCurrentRow(target)

    def remove_step(self):
        index = self.step_list.currentRow()
        if index >= 0:
            self.steps.pop(index)
            self.refresh_steps()

    def definition(self):
        self.save_config()
        skill = self.skills.get(self.trigger.currentData())
        if skill is None:
            raise ValueError('Choose an enabled Visual Skill.')
        conditions = []
        for fields, operators, value, _ in self.condition_rows:
            operator = operators.currentData()
            raw = value.text()
            if fields.currentData() not in skill_fields(skill) or operator is None:
                raise ValueError('Choose a valid condition field and operator.')
            kind = field_kind(skill_fields(skill)[fields.currentData()])
            if operator in NO_VALUE:
                operand = None
            elif kind == 'number':
                try:
                    operand = float(raw)
                except ValueError as exc:
                    raise ValueError('Enter a number for the numeric condition.') from exc
            elif kind == 'boolean':
                if raw.strip().casefold() not in ('true', 'false'):
                    raise ValueError('Enter true or false for the boolean condition.')
                operand = raw.strip().casefold() == 'true'
            else:
                operand = raw
            conditions.append(WorkflowCondition(fields.currentData(), operator, operand))
        definition = WorkflowDefinition(
            id=self.original.id if self.original else 'workflow_' + uuid.uuid4().hex[:16],
            name=self.name.text().strip(), description=self.description.text().strip(),
            trigger=WorkflowTrigger('skill_match', skill.id), conditions=tuple(conditions),
            steps=tuple(self.steps), enabled=self.enabled.isChecked(),
            failure_policy=self.failure.currentData(), auto_run=self.mode.currentData())
        errors = validate_workflow(definition, self.skills)
        if errors:
            raise ValueError(' '.join(errors))
        return definition

    def submit(self):
        try:
            definition = self.definition()
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))
            return
        self.error.clear()
        self.saved.emit(definition)
