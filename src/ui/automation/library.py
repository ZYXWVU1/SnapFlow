"""Searchable local library with explicit lifecycle and safe interchange."""
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QPushButton, QLabel, QFileDialog, QMessageBox, QInputDialog


class AutomationLibrary(QDialog):
    def __init__(self, controller):
        super().__init__(controller.owner.main_window)
        self.controller = controller
        self.setWindowTitle('Automation Skills')
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Reusable desktop procedures · Personal, Productivity, Development, Forms, Settings, Custom\nPublished skills require fresh desktop approval when used by you or Agent.'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search names, descriptions, applications, inputs and tags')
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self.edit())
        layout.addWidget(self.list)
        self.status = QLabel('Teach a task, review the draft, test, then publish.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for actions in ((('Teach SnapFlow', controller.teach), ('Run', self.run), ('Edit', self.edit), ('Repair Target', self.repair),
                ('Create Draft / Restore', self.restore), ('Compare Versions', self.compare)),
                (('Archive', lambda: self.set_status('archived')), ('Disable', lambda: self.set_status('disabled')),
                ('Export .snapauto', self.export), ('Import .snapauto', self.import_skill), ('Delete Skill', self.delete))):
            row = QHBoxLayout()
            for label, callback in actions:
                button = QPushButton(label)
                button.clicked.connect(lambda checked=False, callback=callback: self.act(callback))
                row.addWidget(button)
            layout.addLayout(row)
        self.refresh()

    def act(self, callback):
        try:
            callback()
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def refresh(self, *_):
        self.skills = self.controller.store.search(self.search.text())
        self.list.clear()
        for skill in reversed(self.skills):
            history = [r for r in self.controller.store.usage if r['version_id'] == skill.version_id]
            last = history[-1]['timestamp'] + ' · ' + history[-1]['status'] if history else 'Never'
            self.list.addItem(skill.name + ' · ' + str(len(skill.steps)) + ' steps · Inputs: ' + ', '.join(i.label for i in skill.inputs) +
                '\n' + skill.status + ' · ' + skill.trust + ' · ' + ', '.join(skill.risk_summary) + ' · Last run: ' + last + '\n' + skill.version_id)

    def selected(self):
        index = self.list.currentRow()
        if index < 0:
            raise ValueError('Select an Automation Skill version.')
        return tuple(reversed(self.skills))[index]

    def run(self):
        skill = self.selected()
        if skill.status != 'published':
            raise ValueError('Open the editor to review and test a draft. Manual reuse requires a published version.')
        self.controller.run_skill(skill)

    def edit(self):
        self.controller.edit(self.selected())

    def restore(self):
        self.controller.edit(self.controller.store.create_draft(self.selected().version_id))

    def set_status(self, status):
        self.controller.store.set_status(self.selected().version_id, status)

    def compare(self):
        skill = self.selected()
        others = [s for s in self.controller.store.versions(skill.skill_id) if s.version_id != skill.version_id]
        if not others:
            raise ValueError('Create a second version to compare changes.')
        key, accepted = QInputDialog.getItem(self, 'Compare Versions', 'Compare selected version with:', [s.version_id for s in others], editable=False)
        if accepted:
            changes = self.controller.store.compare(skill.version_id, key)
            QMessageBox.information(self, 'Semantic Version Changes', '\n'.join(label.title() + ': ' + value for label, value in changes.items()) or 'No procedure changes.')

    def repair(self):
        skill = self.selected()
        labels = [str(i + 1) + '. ' + s.title for i, s in enumerate(skill.steps)]
        label, accepted = QInputDialog.getItem(self, 'Repair Target', 'Choose the broken step:', labels, editable=False)
        if not accepted:
            return
        step = skill.steps[labels.index(label)]
        def chosen(element):
            from src.automation.models import TargetTemplate
            draft = self.controller.store.repair(skill.version_id, step.step_id, TargetTemplate.from_element(element, step.action_type))
            self.controller.edit(draft)
            self.refresh()
        self.controller.pick_target(self, chosen)

    def export(self):
        skill = self.selected()
        destination, _ = QFileDialog.getSaveFileName(self, 'Export Automation Skill', '', 'Automation Skill (*.snapauto)')
        if destination:
            Path(destination).write_text(json.dumps(self.controller.store.export(skill.version_id), ensure_ascii=False, indent=2), encoding='utf-8')
            self.status.setText('Exported without screenshots, traces, test inputs or literal text-entry values. Input defaults were removed.')

    def import_skill(self):
        source, _ = QFileDialog.getOpenFileName(self, 'Import Automation Skill', '', 'Automation Skill (*.snapauto)')
        if source:
            if Path(source).stat().st_size > 262144:
                raise ValueError('Imported definition exceeds the size limit.')
            self.controller.store.import_definition(json.loads(Path(source).read_text(encoding='utf-8')))
            self.status.setText('Imported disabled and unreviewed. Create a draft, review, test locally, then publish.')

    def delete(self):
        skill = self.selected()
        if QMessageBox.question(self, 'Delete Automation Skill', 'Delete all local versions and usage history for this skill?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.controller.store.delete(skill.skill_id)
