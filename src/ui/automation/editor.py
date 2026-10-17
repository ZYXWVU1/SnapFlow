"""Form-based semantic procedure review. No executable source editor."""
from dataclasses import replace
import json
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QTabWidget, QWidget, QListWidget, QPushButton, QCheckBox, QComboBox, QDialogButtonBox,
    QInputDialog, QMessageBox)

from src.automation.models import AutomationStepTemplate, ConditionTemplate, TargetTemplate
from src.automation.parameters import parameterize
from src.desktop_actions.models import ACTIONS, PROPERTIES


def scalar(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class AutomationEditor(QDialog):
    def __init__(self, controller, skill):
        super().__init__(controller.owner.main_window)
        self.controller, self.skill = controller, skill
        self.setWindowTitle('Automation Skill · Review and Edit')
        self.resize(800, 620)
        layout = QVBoxLayout(self)
        self.status = QLabel('Drafts must be reviewed and tested before publication.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        overview = QWidget()
        form = QFormLayout(overview)
        self.name = QLineEdit(skill.name)
        self.description = QPlainTextEdit(skill.description)
        self.tags = QLineEdit(', '.join(skill.tags))
        form.addRow('Name', self.name)
        form.addRow('Description', self.description)
        form.addRow('Tags / Category', self.tags)
        self.tabs.addTab(overview, 'Overview')
        self.inputs = QListWidget()
        self.tabs.addTab(self.inputs, 'Inputs')
        steps = QWidget()
        step_layout = QVBoxLayout(steps)
        self.steps = QListWidget()
        step_layout.addWidget(self.steps)
        row = QHBoxLayout()
        for label, callback in (('Edit / Annotate', self.edit), ('Add Semantic Step', self.add),
                ('Move Up', lambda: self.move(-1)), ('Move Down', lambda: self.move(1)), ('Delete Step', self.remove)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        step_layout.addLayout(row)
        self.tabs.addTab(steps, 'Steps')
        self.targets = QPlainTextEdit()
        self.targets.setReadOnly(True)
        self.tabs.addTab(self.targets, 'Targets')
        permissions = QLabel('Every action: Ask Each Action.\nExternal commits: fresh exact approval on every run.\nProtected/destructive actions: unsupported.\nFallback: structured UI Automation only.\nPrivate Mode and Safe Mode remain authoritative.\nNo cloud demonstration processing or automatic publishing.')
        permissions.setWordWrap(True)
        self.tabs.addTab(permissions, 'Permissions')
        self.tests = QPlainTextEdit()
        self.tests.setReadOnly(True)
        self.tabs.addTab(self.tests, 'Tests')
        self.versions = QListWidget()
        self.tabs.addTab(self.versions, 'Versions')
        row = QHBoxLayout()
        for label, callback in (('Save Draft', self.save), ('Confirm Review', self.review),
                ('Dry Run / Test', self.test), ('Publish Tested Version', self.publish), ('Close', self.accept)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.steps.clear()
        self.inputs.clear()
        for step in self.skill.steps:
            self.steps.addItem(f'{step.title} · {step.action_type} · {step.risk_level} · {step.confidence:.0%} · ' + ('Reviewed' if step.reviewed else 'Needs Review'))
        for spec in self.skill.inputs:
            self.inputs.addItem(spec.label + ' · ' + spec.input_id + ' · ' + spec.value_type + ' · Ask at run time')
        self.targets.setPlainText('\n\n'.join(s.title + '\n' + s.target_template.process_name + '\n' +
            str(s.target_template.name_template or '') + '\nAutomation ID: ' + str(s.target_template.automation_id or 'None') +
            '\nAncestor path: ' + ' / '.join(s.target_template.ancestor_templates) + '\nExpected actions: ' + ', '.join(s.target_template.expected_actions) for s in self.skill.steps))
        record = self.controller.store.records.get(self.skill.version_id)
        self.tests.setPlainText('Status: ' + self.skill.status + '\n' +
            ('Verified test: ' + str(record['test']) if record and record['test'] else 'No verified test of this version. Dry run never executes controls. Testing requires approval of each action.'))
        self.versions.clear()
        for version in self.controller.store.versions(self.skill.skill_id):
            self.versions.addItem(version.version_id + ' · ' + version.status + ' · ' + version.updated_at)

    def _draft(self):
        if self.skill.status not in ('draft', 'tested'):
            self.skill = self.controller.store.create_draft(self.skill.version_id)
        else:
            self.skill = replace(self.skill, status='draft')

    def _set_steps(self, steps):
        self._draft()
        self.skill = replace(self.skill, steps=tuple(steps))
        self.refresh()

    def save(self):
        try:
            self._draft()
            self.skill = self.controller.store.save_draft(replace(self.skill, name=self.name.text().strip(),
                description=self.description.toPlainText(), tags=tuple(t.strip() for t in self.tags.text().split(',') if t.strip())))
            self.status.setText('Draft saved. Review and test evidence must be renewed after edits.')
            self.refresh()
            return True
        except Exception as exc:
            self.status.setText(str(exc))
            return False

    def review(self):
        if self.save():
            try:
                self.controller.store.review(self.skill.version_id)
                self.status.setText('Version review recorded. Next: dry run, then approve each test action.')
            except Exception as exc:
                self.status.setText(str(exc))

    def test(self):
        try:
            self._require_saved()
            self.controller.run_skill(self.controller.store.get(self.skill.version_id), testing=True)
            self.skill = self.controller.store.get(self.skill.version_id)
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def publish(self):
        try:
            self._require_saved()
            self.skill = self.controller.store.publish(self.skill.version_id)
            self.status.setText('Published immutable version. Edits and repairs create another draft.')
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def _require_saved(self):
        from src.automation.storage import content_hash
        current = replace(self.skill, name=self.name.text().strip(), description=self.description.toPlainText(),
            tags=tuple(t.strip() for t in self.tags.text().split(',') if t.strip()))
        if current.version_id not in self.controller.store.records or content_hash(current) != content_hash(self.controller.store.get(current.version_id)):
            raise ValueError('Save these edits, confirm review, and test the changed version before publishing.')

    def edit(self):
        index = self.steps.currentRow()
        if index < 0:
            return
        self._edit_step(self.skill.steps[index], index)

    def add(self):
        self.controller.pick_target(self, lambda element: self._edit_step(AutomationStepTemplate('step_' + uuid4().hex,
            'New semantic step', element.capabilities.actions[0], TargetTemplate.from_element(element, element.capabilities.actions[0])), None))

    def _edit_step(self, step, index):
        dialog = QDialog(self)
        dialog.setWindowTitle('Review Semantic Step')
        form = QFormLayout(dialog)
        title = QLineEdit(step.title)
        action = QComboBox()
        action.addItems(ACTIONS)
        action.setCurrentText(step.action_type)
        value = QLineEdit(json.dumps(step.to_dict()['value_template'], ensure_ascii=False))
        variable = QLineEdit()
        variable.setPlaceholderText('Leave empty to keep this value fixed')
        reviewed = QCheckBox('I confirm this semantic action and target, including any inference warnings')
        reviewed.setChecked(step.reviewed)
        dependencies = QListWidget()
        for other in self.skill.steps:
            if other.step_id == step.step_id:
                continue
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(other.title)
            item.setData(Qt.ItemDataRole.UserRole, other.step_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if other.step_id in step.dependencies else Qt.CheckState.Unchecked)
            dependencies.addItem(item)
        optional = QCheckBox('Optional step (may be removed in a new draft)')
        optional.setChecked(step.optional)
        outcome_property = QComboBox()
        outcome_property.addItems(('', *PROPERTIES))
        outcome_value = QLineEdit()
        outcome = [step.postconditions[0] if step.postconditions else None]
        if outcome[0]:
            outcome_property.setCurrentText(outcome[0].property)
            outcome_value.setText(json.dumps(outcome[0].to_dict()['equals']))
        target = [step.target_template]
        target_label = QLabel(str(target[0].name_template or target[0].automation_id))
        replace_target = QPushButton('Select Current Semantic Target')
        def selected(element):
            target[0] = TargetTemplate.from_element(element, action.currentText())
            target_label.setText(element.name or element.automation_id)
        replace_target.clicked.connect(lambda: self.controller.pick_target(dialog, selected))
        select_outcome = QPushButton('Select Outcome Control')
        def selected_outcome(element):
            template = replace(TargetTemplate.from_element(element, action.currentText()), name_template=None if element.automation_id else element.name,
                expected_actions=(), expected_patterns=())
            outcome[0] = ConditionTemplate(template, outcome_property.currentText() or 'name', scalar(outcome_value.text()))
        select_outcome.clicked.connect(lambda: self.controller.pick_target(dialog, selected_outcome, include_read_only=True))
        for label, widget in (('Title', title), ('Action', action), ('Target', target_label), ('', replace_target),
                ('Desired value (text or scalar)', value), ('Variable identifier', variable), ('Outcome property', outcome_property),
                ('Outcome value', outcome_value), ('', select_outcome), ('Requires earlier steps', dependencies), ('', optional), ('', reviewed)):
            form.addRow(label, widget)
        warning = QLabel('\n'.join(step.warnings) or 'Review risk and expected outcome before testing.')
        warning.setWordWrap(True)
        form.addRow(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._draft()
            postconditions = ()
            if outcome_property.currentText():
                condition_target = outcome[0].target if outcome[0] else target[0]
                postconditions = (ConditionTemplate(condition_target, outcome_property.currentText(), scalar(outcome_value.text())),)
            edited = replace(step, title=title.text().strip(), action_type=action.currentText(),
                target_template=replace(target[0], expected_actions=(action.currentText(),)), value_template=scalar(value.text()),
                postconditions=postconditions, reviewed=reviewed.isChecked(), optional=optional.isChecked(),
                dependencies=tuple(dependencies.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dependencies.count()) if dependencies.item(i).checkState() == Qt.CheckState.Checked))
            steps = list(self.skill.steps)
            if index is None:
                edited = replace(edited, dependencies=(steps[-1].step_id,) if steps else ())
                steps.append(edited)
            else:
                steps[index] = edited
            self.skill = replace(self.skill, steps=tuple(steps))
            if variable.text().strip():
                self.skill = parameterize(self.skill, edited.step_id, variable.text().strip(), title.text().strip())
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def move(self, delta):
        index = self.steps.currentRow()
        if not 0 <= index + delta < len(self.skill.steps) or index < 0:
            return
        steps = list(self.skill.steps)
        steps[index], steps[index + delta] = steps[index + delta], steps[index]
        try:
            self._set_steps(steps)
            self.steps.setCurrentRow(index + delta)
        except ValueError:
            self.status.setText('This order violates step dependencies. Edit the procedure dependencies in a new draft first.')

    def remove(self):
        index = self.steps.currentRow()
        if index < 0:
            return
        selected = self.skill.steps[index]
        if any(selected.step_id in s.dependencies for s in self.skill.steps):
            self.status.setText('Another step depends on this step. Remove dependent steps first.')
            return
        try:
            self._set_steps([s for i, s in enumerate(self.skill.steps) if i != index])
        except Exception as exc:
            self.status.setText(str(exc))
