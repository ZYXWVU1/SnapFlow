"""Workflow list and save boundary."""
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout
from src.workflows.context import WorkflowContext
from src.workflows.executor import WorkflowExecutor
from src.workflows.import_export import export_workflow, import_workflow
from src.workflows.validator import validate_workflow
from src.skill_actions import ACTIONS
from .workflow_editor import WorkflowEditor


class WorkflowManager(QDialog):
    def __init__(self, storage, skills, parent=None, allow_auto=False, result_provider=None,
                 history=None, connection_storage=None, open_integrations=None, integration_service=None):
        super().__init__(parent)
        self.storage, self.skills = storage, skills
        self.allow_auto = allow_auto
        self.result_provider = result_provider
        self.history = history
        self.connection_storage, self.open_integrations = connection_storage, open_integrations
        self.integration_service = integration_service
        self.dialogs = []
        self.setWindowTitle('Visual Workflows')
        self.resize(650, 530)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('When a Visual Skill matches, run Actions in this order.'))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self.edit_selected())
        layout.addWidget(self.list)
        row = QHBoxLayout()
        for title, callback in [('+ New Workflow', self.create_workflow), ('Edit', self.edit_selected),
                                ('Enable / Disable', self.toggle_selected), ('Delete', self.delete_selected)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        extras = QHBoxLayout()
        for title, callback in [('Run Test', self.test_selected), ('Duplicate', self.duplicate_selected),
                                ('Export', self.export_selected), ('Import', self.import_file),
                                ('History', self.show_history)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            extras.addWidget(button)
        layout.addLayout(extras)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    def selected(self):
        item = self.list.currentItem()
        return self.storage.get_workflow(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh(self):
        selected = self.selected()
        self.list.clear()
        for workflow in self.storage.list_workflows():
            mode = 'Auto' if workflow.auto_run else 'Suggest'
            label = f'{workflow.name} · {workflow.trigger.skill_id} · {len(workflow.steps)} actions · {mode}'
            if not workflow.enabled:
                label += ' · Disabled'
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, workflow.id)
            self.list.addItem(item)
            if selected and selected.id == workflow.id:
                self.list.setCurrentItem(item)
        self.status.setText(self.storage.warning)

    def show_editor(self, definition=None):
        editor = WorkflowEditor(self.skills, definition, self, allow_auto=self.allow_auto,
                                connection_storage=self.connection_storage,
                                integration_service=self.integration_service)
        if self.open_integrations is not None:
            editor.connect_requested.connect(self.open_integrations)
        def save(new):
            try:
                if new.auto_run and (definition is None or not definition.auto_run):
                    external = [ACTIONS[step.action_id].label for step in new.steps
                        if step.enabled and step.action_id in ACTIONS and
                        ACTIONS[step.action_id].risk_level == 'external_write']
                    detail = ('\n\nExternal Actions that will write automatically:\n' +
                              '\n'.join('• ' + label for label in external)) if external else ''
                    response = QMessageBox.question(self, 'Enable Auto Workflow',
                        'This Workflow will automatically execute its configured Actions whenever the selected Visual Skill matches.' +
                        detail + '\n\nSave and enable Auto?',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if response != QMessageBox.StandardButton.Yes:
                        return
                if definition is None:
                    self.storage.create_workflow(new)
                else:
                    if self.storage.get_workflow(definition.id) != definition:
                        raise ValueError('This Workflow changed while editing. Reopen it.')
                    self.storage.save_workflow(new)
            except (OSError, ValueError) as exc:
                editor.error.setText(str(exc))
                return
            editor.accept()
            self.refresh()
        editor.saved.connect(save)
        self.dialogs.append(editor)
        editor.show()
        return editor

    def create_workflow(self):
        return self.show_editor()

    def edit_selected(self):
        if self.selected():
            return self.show_editor(self.selected())

    def toggle_selected(self):
        workflow = self.selected()
        if workflow:
            try:
                if not workflow.enabled:
                    errors = validate_workflow(workflow, self.skills)
                    if errors:
                        self.status.setText(' '.join(errors))
                        return
                    steps = '\n'.join(f'{i}. {step.action_id}' for i, step in enumerate(workflow.steps, 1))
                    mode = 'Auto' if workflow.auto_run else 'Suggest'
                    answer = QMessageBox.question(self, 'Enable Workflow',
                        f'Enable "{workflow.name}" in {mode} mode?\n\n{steps}',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if answer != QMessageBox.StandardButton.Yes:
                        return
                self.storage.set_enabled(workflow.id, not workflow.enabled)
                self.refresh()
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def delete_selected(self):
        workflow = self.selected()
        if workflow and QMessageBox.question(self, 'Delete Workflow', f'Delete "{workflow.name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.storage.delete_workflow(workflow.id)
                self.refresh()
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def duplicate_selected(self):
        workflow = self.selected()
        if not workflow:
            return
        existing = {w.id for w in self.storage.list_workflows()}
        suffix = 2
        candidate = workflow.id[:123] + '_copy'
        while candidate in existing:
            candidate = workflow.id[:110] + f'_copy_{suffix}'
            suffix += 1
        try:
            self.storage.create_workflow(replace(workflow, id=candidate,
                name=workflow.name[:190] + ' (copy)', enabled=False, auto_run=False))
            self.refresh()
            self.status.setText('Copy created in disabled Suggest mode. Review before enabling.')
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def export_selected(self):
        workflow = self.selected()
        if not workflow:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export Workflow', workflow.id + '.aiworkflow',
                                              'Visual Workflows (*.aiworkflow)')
        if path:
            try:
                Path(path).write_text(export_workflow(workflow), encoding='utf-8')
                self.status.setText('Workflow definition exported.')
            except OSError:
                self.status.setText('Unable to export. Choose a writable destination.')

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import Workflow', '',
                                              'Visual Workflows (*.aiworkflow *.json)')
        if path:
            try:
                if Path(path).stat().st_size > 1_000_000:
                    raise ValueError('Workflow file must be under 1 MB.')
                workflow, warnings = import_workflow(Path(path).read_text(encoding='utf-8'),
                    {w.id for w in self.storage.list_workflows()}, self.skills)
                self.storage.create_workflow(workflow)
                self.refresh()
                self.status.setText('Workflow imported. ' + ' '.join(warnings))
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def test_selected(self):
        workflow = self.selected()
        result = self.result_provider() if self.result_provider else None
        if not workflow:
            return
        if result is None or result.skill_id != workflow.trigger.skill_id:
            self.status.setText('Capture a screenshot matching this Visual Skill, then run the test.')
            return
        execution = WorkflowExecutor(self.skills).execute(workflow,
            WorkflowContext.from_result(result), preview=True)
        lines = [workflow.name + ': ' + execution.status,
                 'Conditions: ' + ('did not match' if execution.status == 'skipped' else 'passed')]
        for step in execution.steps:
            lines.append(step.action_id + ': ' + step.status)
            if step.preview:
                lines.append(step.preview)
            if step.error:
                lines.append(step.error)
        lines.append('No changes were made.')
        QMessageBox.information(self, 'Workflow Dry Run', '\n'.join(lines))

    def show_history(self):
        if self.history is None:
            self.status.setText('Workflow history is unavailable.')
            return
        if self.history.warning:
            self.status.setText(self.history.warning)
            return
        entries = self.history.list_entries()
        if not entries:
            self.status.setText('No workflow executions yet.')
            return
        lines = [f"{entry['started_at']} · {entry['workflow_name']} · {entry['status']}\n" +
                 '\n'.join(f"  {step['action_id']}: {step['status']}" for step in entry['steps'])
                 for entry in reversed(entries)]
        QMessageBox.information(self, 'Workflow History', '\n\n'.join(lines))

    def done(self, result):
        for dialog in self.dialogs:
            dialog.close()
        super().done(result)
