"""Definition management. Only explicit Save publishes editor drafts."""
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QLabel, QPushButton, QMessageBox, QFileDialog
from src.skills.registry import SKILLS
from src.skills.custom.models import unique_id
from src.skills.custom.import_export import export_skill, import_skill
from .skill_editor import SkillEditor


class SkillManager(QDialog):
    def __init__(self, storage, client, parent=None, workflows=None):
        super().__init__(parent)
        self.storage, self.client = storage, client
        self.workflows = workflows
        self.dialogs = []
        self.setWindowTitle('Visual Skills')
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(690, 600)
        layout = QVBoxLayout(self)
        heading = QLabel('Teach the app which screenshots matter to you.')
        layout.addWidget(heading)
        layout.addWidget(QLabel('Built-in · always checked first'))
        self.builtins = QListWidget()
        self.builtins.addItems([s.title for s in SKILLS.values()])
        self.builtins.setMaximumHeight(115)
        layout.addWidget(self.builtins)
        layout.addWidget(QLabel('My skills'))
        self.custom = QListWidget()
        self.custom.itemDoubleClicked.connect(lambda _: self.edit_selected())
        layout.addWidget(self.custom, 1)
        self.selected_buttons = []
        row = QHBoxLayout()
        for title, callback in [('Edit', self.edit_selected), ('Enable / Disable', self.toggle_selected),
                                ('Test', self.test_selected), ('Export', self.export_selected), ('Duplicate', self.duplicate_selected), ('Delete', self.delete_selected)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
            self.selected_buttons.append(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for title, callback in [('+ Create Skill', self.create_skill), ('Teach From Screenshot', self.teach), ('Import Skill', self.import_file)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        layout.addWidget(close)
        self.custom.currentRowChanged.connect(self.update_buttons)
        self.refresh()

    def refresh(self):
        current = self.selected()
        self.custom.clear()
        for skill in self.storage.list_skills():
            item = QListWidgetItem(('Enabled · ' if skill.enabled else 'Disabled · ') + skill.name)
            item.setData(Qt.ItemDataRole.UserRole, skill.id)
            item.setToolTip(skill.description)
            self.custom.addItem(item)
            if current and current.id == skill.id:
                self.custom.setCurrentItem(item)
        self.status.setText(self.storage.warning)
        self.update_buttons()

    def update_buttons(self):
        for button in self.selected_buttons:
            button.setEnabled(self.selected() is not None and (button.text() != 'Test' or self.client is not None))

    def selected(self):
        item = self.custom.currentItem()
        return self.storage.get_skill(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def show_editor(self, definition=None, creating=False):
        editor = SkillEditor(definition, self, self.client, [s.id for s in self.storage.list_skills()])
        def save(skill):
            try:
                if definition is None or creating:
                    self.storage.create_skill(skill)
                else:
                    if self.storage.get_skill(definition.id) != definition:
                        raise ValueError('This skill was changed or deleted while editing. Close this editor and reopen the current skill.')
                    self.storage.save_skill(skill)
            except (OSError, ValueError) as exc:
                editor.error.setText(str(exc))
                return
            editor.accept()
            self.refresh()
        editor.saved.connect(save)
        self.dialogs.append(editor)
        editor.show()
        return editor

    def create_skill(self):
        self.show_editor()

    def edit_selected(self):
        if self.selected():
            self.show_editor(self.selected())

    def toggle_selected(self):
        skill = self.selected()
        if skill:
            try:
                self.storage.set_enabled(skill.id, not skill.enabled)
                self.refresh()
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def delete_selected(self):
        skill = self.selected()
        dependents = ([w for w in self.workflows.list_workflows() if w.trigger.skill_id == skill.id]
                      if skill and self.workflows else [])
        message = f'Delete "{skill.name}"?\nThis removes the skill definition.' if skill else ''
        if dependents:
            message += f'\n{len(dependents)} Workflow(s) use this Skill. They will be disabled.'
        if skill and QMessageBox.question(self, 'Delete Skill', message,
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                for workflow in dependents:
                    self.workflows.set_enabled(workflow.id, False)
                self.storage.delete_skill(skill.id)
                self.refresh()
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def duplicate_selected(self):
        skill = self.selected()
        if skill:
            copy = replace(skill, id=unique_id(skill.id, {s.id for s in self.storage.list_skills()}), name=skill.name[:93] + ' (copy)')
            self.show_editor(copy, creating=True)

    def test_selected(self):
        if self.selected() and self.client:
            from .skill_test_dialog import SkillTestDialog
            dialog = SkillTestDialog(self.selected(), self.client, self)
            self.dialogs.append(dialog)
            dialog.show()

    def teach(self):
        if self.client:
            from .teach_by_example import TeachByExample
            dialog = TeachByExample(self.client, self)
            def review(draft):
                draft = replace(draft, id=unique_id(draft.id, {s.id for s in self.storage.list_skills()}))
                editor = self.show_editor(draft, creating=True)
                editor.error.setText('AI draft: review the detection rule, fields and actions before saving. Enable it when ready.')
                dialog.accept()
            dialog.generated.connect(review)
            self.dialogs.append(dialog)
            dialog.show()

    def export_selected(self):
        skill = self.selected()
        if not skill:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export Skill', skill.id + '.aiskill', 'Visual skills (*.aiskill)')
        if path:
            try:
                Path(path).write_text(export_skill(skill), encoding='utf-8')
                self.status.setText('Skill definition exported.')
            except OSError:
                self.status.setText('Unable to export. Choose a writable location.')

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import Skill', '', 'Visual skills (*.aiskill *.json)')
        if path:
            try:
                if Path(path).stat().st_size > 1_000_000:
                    raise ValueError('Skill file must be under 1 MB.')
                skill, warnings = import_skill(Path(path).read_text(encoding='utf-8'), {s.id for s in self.storage.list_skills()})
                self.storage.create_skill(skill)
                self.refresh()
                self.status.setText('Skill imported. ' + ' '.join(warnings))
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def done(self, result):
        for dialog in self.dialogs:
            dialog.close()
        super().done(result)
