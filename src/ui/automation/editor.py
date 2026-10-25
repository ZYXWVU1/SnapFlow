"""Form-based semantic procedure review. No executable source editor."""
from dataclasses import replace
import json
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QTabWidget, QWidget, QListWidget, QPushButton, QCheckBox, QComboBox, QDialogButtonBox,
    QInputDialog, QMessageBox)

from src.automation.models import AutomationStepTemplate, ConditionTemplate, TargetTemplate, BROWSER_ACTIONS
from src.automation.parameters import parameterize, references
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
        if skill.domain == 'browser':
            manual = QPushButton('Add Manual Checkpoint')
            manual.clicked.connect(self.add_manual)
            step_layout.addWidget(manual)
        self.tabs.addTab(steps, 'Steps')
        self.targets = QPlainTextEdit()
        self.targets.setReadOnly(True)
        self.tabs.addTab(self.targets, 'Targets')
        self.permissions = QLabel('Every browser effect: fresh exact approval in the browser sidebar.\n'
            'Final submissions, sends, purchases and destructive operations remain manual checkpoints.\n'
            'Scope: ' + ', '.join(skill.supported_origins) + '\n'
            'Targets use roles and exact accessible names. Private Mode and Safe Mode remain authoritative.\n'
            'Navigation requires fresh Assist and interaction consent for the destination.' if skill.domain == 'browser' else
            'Every action: Ask Each Action.\nExternal commits: fresh exact approval on every run.\nProtected/destructive actions: unsupported.\nFallback: structured UI Automation only.\nPrivate Mode and Safe Mode remain authoritative.\nNo cloud demonstration processing or automatic publishing.')
        self.permissions.setWordWrap(True)
        self.permissions.setTextFormat(Qt.TextFormat.PlainText)
        self.tabs.addTab(self.permissions, 'Permissions')
        self.tests = QPlainTextEdit()
        self.tests.setReadOnly(True)
        self.tabs.addTab(self.tests, 'Tests')
        self.versions = QListWidget()
        self.tabs.addTab(self.versions, 'Versions')
        row = QHBoxLayout()
        for label, callback in (('Save Draft', self.save), ('Confirm Review', self.review),
                ('Dry Run / Test', self.test), ('Publish Tested Version', self.publish), ('Close', self.accept)):
            if skill.domain == 'browser' and label == 'Publish Tested Version':
                layout.addLayout(row)
                row = QHBoxLayout()
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
        def target_description(step):
            target = step.target_template
            if step.domain == 'browser':
                return (step.title + '\nWebsite: ' + target.origin + '\nRole: ' + target.role +
                        '\nAccessible name: ' + target.name_template + '\n' +
                        ('Manual checkpoint' if not step.executable else 'Action: ' + step.action_type))
            return (step.title + '\n' + target.process_name + '\n' + str(target.name_template or '') +
                '\nAutomation ID: ' + str(target.automation_id or 'None') + '\nAncestor path: ' +
                ' / '.join(target.ancestor_templates) + '\nExpected actions: ' + ', '.join(target.expected_actions))
        self.targets.setPlainText('\n\n'.join(target_description(step) for step in self.skill.steps))
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
        if self.skill.domain == 'browser':
            def chosen(target):
                action = {'textbox': 'browser_set_text', 'combobox': 'browser_select_option',
                    'checkbox': 'browser_set_checked', 'document': 'browser_scroll',
                    'link': 'browser_controlled_navigation'}.get(target.role)
                if action is None:
                    raise ValueError('Choose a supported non-sensitive control, or add a manual checkpoint.')
                value = False if action == 'browser_set_checked' else 0 if action == 'browser_scroll' else target.origin if action == 'browser_controlled_navigation' else ''
                self._edit_step(AutomationStepTemplate('step_' + uuid4().hex, 'New website step',
                    action, target, value, risk_level='ui_navigation' if action in ('browser_scroll', 'browser_controlled_navigation') else 'ui_external_edit'), None)
            self.controller.owner.browser_controller.pick_target(self, chosen)
            return
        self.controller.pick_target(self, lambda element: self._edit_step(AutomationStepTemplate('step_' + uuid4().hex,
            'New semantic step', element.capabilities.actions[0], TargetTemplate.from_element(element, element.capabilities.actions[0])), None))

    def add_manual(self):
        if self.skill.domain != 'browser':
            return
        self.controller.owner.browser_controller.pick_target(self, lambda target: self._edit_step(
            AutomationStepTemplate('step_' + uuid4().hex, 'Complete this step manually', 'manual_checkpoint',
                                   target, risk_level='ui_external_commit'), None), manual=True)

    def _edit_step(self, step, index):
        if step.domain == 'browser':
            return self._edit_browser_step(step, index)
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

    def _edit_browser_step(self, step, index):
        dialog = QDialog(self)
        dialog.setWindowTitle('Review Website Step')
        form = QFormLayout(dialog)
        title = QLineEdit(step.title)
        title.setObjectName('browser_step_title')
        action = QComboBox()
        action.setObjectName('browser_action_type')
        action.addItems((*sorted(BROWSER_ACTIONS), 'manual_checkpoint'))
        action.setCurrentText(step.action_type)
        target = [step.target_template]
        name = QLineEdit(target[0].name_template)
        name.setObjectName('browser_target_name')
        scope = QLabel(target[0].origin + ' · ' + target[0].role)
        scope.setTextFormat(Qt.TextFormat.PlainText)
        value = QLineEdit(json.dumps(step.to_dict()['value_template'], ensure_ascii=False))
        value.setObjectName('browser_step_value')
        value.setMaxLength(4096)
        variable = QLineEdit()
        variable.setObjectName('browser_input_id')
        variable.setPlaceholderText('Optional value input, such as query')
        reviewed = QCheckBox('I reviewed this exact website, target and step')
        reviewed.setObjectName('browser_step_reviewed')
        reviewed.setChecked(step.reviewed)
        optional = QCheckBox('Optional step')
        optional.setChecked(step.optional)
        requested_only = QCheckBox('Verify requested control state only; remove additional state conditions')
        requested_only.setChecked(not step.preconditions and not step.postconditions)
        dependencies = QListWidget()
        from PySide6.QtWidgets import QListWidgetItem
        for other in self.skill.steps:
            if other.step_id == step.step_id:
                continue
            item = QListWidgetItem(other.title)
            item.setData(Qt.ItemDataRole.UserRole, other.step_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if other.step_id in step.dependencies else Qt.CheckState.Unchecked)
            dependencies.addItem(item)
        select = QPushButton('Choose from reviewed website')
        def chosen(replacement):
            if replacement.origin not in self.skill.supported_origins:
                raise ValueError('A repair cannot expand the declared website scope.')
            target[0] = replacement
            name.setText(replacement.name_template)
            scope.setText(replacement.origin + ' · ' + replacement.role)
        select.clicked.connect(lambda: self.controller.owner.browser_controller.pick_target(
            dialog, chosen, manual=action.currentText() == 'manual_checkpoint'))
        def change_action():
            manual = action.currentText() == 'manual_checkpoint'
            value.setEnabled(not manual)
            variable.setEnabled(action.currentText() in ('browser_set_text', 'browser_select_option', 'browser_set_checked'))
        action.currentTextChanged.connect(change_action)
        change_action()
        for label, widget in (('Title', title), ('Action', action), ('Website / role', scope),
                ('Exact accessible name', name), ('', select), ('Proposed value (text or JSON scalar)', value),
                ('Value input identifier', variable), ('Requires earlier steps', dependencies),
                ('', requested_only), ('', optional), ('', reviewed)):
            form.addRow(label, widget)
        warning = QLabel('\n'.join(step.warnings) + '\nFinal submissions remain manual. '
            'Dry run makes no changes. Additional state conditions pause this browser adapter.')
        warning.setTextFormat(Qt.TextFormat.PlainText)
        warning.setWordWrap(True)
        form.addRow(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if references(name.text()):
                raise ValueError('Website targets require a fixed exact accessible name; parameterize the value instead.')
            self._draft()
            operation = action.currentText()
            manual = operation == 'manual_checkpoint'
            edited = replace(step, title=title.text().strip(), action_type=operation,
                target_template=replace(target[0], name_template=name.text().strip()),
                value_template=None if manual else scalar(value.text()),
                risk_level='ui_external_commit' if manual else 'ui_navigation' if operation in
                    ('browser_scroll', 'browser_controlled_navigation') else 'ui_external_edit',
                reviewed=reviewed.isChecked(), optional=optional.isChecked(),
                preconditions=() if requested_only.isChecked() else step.preconditions,
                postconditions=() if requested_only.isChecked() else step.postconditions,
                dependencies=tuple(dependencies.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(dependencies.count()) if dependencies.item(i).checkState() == Qt.CheckState.Checked))
            steps = list(self.skill.steps)
            if index is None:
                edited = replace(edited, dependencies=(steps[-1].step_id,) if steps else ())
                steps.append(edited)
            else:
                steps[index] = edited
            candidate = replace(self.skill, steps=tuple(steps))
            if variable.isEnabled() and variable.text().strip():
                candidate = parameterize(candidate, edited.step_id, variable.text().strip(), title.text().strip())
            from src.automation.validation import validate_definition
            validate_definition(candidate)
            self.skill = candidate
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
