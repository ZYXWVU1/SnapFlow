from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QTextEdit, QLabel, QHBoxLayout, QCheckBox, QScrollArea, QWidget
from src.skills.custom.models import CustomSkillDefinition, safe_id, unique_id
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING
from .field_editor import FieldEditor
from .action_selector import ActionSelector


class SkillEditor(QDialog):
    saved = Signal(object)

    def __init__(self, definition=None, parent=None, client=None, existing=()):
        super().__init__(parent)
        self.original, self.client, self.existing = definition, client, set(existing)
        self.tests = []
        self.setWindowTitle('Edit Visual Skill' if definition else 'Create Visual Skill')
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(780, 730)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        layout.addWidget(PageHeader('Edit Visual Skill' if definition else 'Create Visual Skill',
            'Define what this Skill recognizes and the fields it extracts.'))
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        content = QWidget()
        self.content_scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, SPACING['sm'], 0)
        layout.addWidget(self.content_scroll, 1)
        self.identity_card = Card()
        body.addWidget(self.identity_card)
        form = QFormLayout()
        self.name = QLineEdit(definition.name if definition else '')
        self.name.setMaxLength(100)
        self.description = QLineEdit(definition.description if definition else '')
        self.description.setMaxLength(1000)
        self.detection = QTextEdit()
        self.detection.setAcceptRichText(False)
        self.detection.setPlainText(definition.detection_prompt if definition else '')
        self.detection.setMaximumHeight(85)
        self.detection.setPlaceholderText('For example: a job listing with a company and role. Maximum 1000 characters.')
        form.addRow('Skill name', self.name)
        form.addRow('Description', self.description)
        form.addRow('Detect screenshots containing', self.detection)
        self.identity_card.content.addLayout(form)
        self.fields_card = Card()
        self.fields_card.content.addWidget(QLabel('Extract these fields · up to 20 · order controls the result'))
        self.fields = FieldEditor(definition.fields if definition else ())
        self.fields.setMinimumHeight(270)
        self.fields_card.content.addWidget(self.fields)
        body.addWidget(self.fields_card)
        actions_card = Card()
        actions_card.content.addWidget(QLabel('Available actions'))
        self.actions = ActionSelector(definition.actions if definition else ('copy_json', 'copy_markdown', 'ask_ai'))
        actions_card.content.addWidget(self.actions)
        self.enabled = QCheckBox('Enable this skill in Smart mode')
        self.enabled.setChecked(definition.enabled if definition else True)
        actions_card.content.addWidget(self.enabled)
        body.addWidget(actions_card)
        preview_card = Card()
        preview_card.content.addWidget(QLabel('Result preview'))
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(110)
        preview_card.content.addWidget(self.preview)
        body.addWidget(preview_card)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        test = AppButton('Test Skill')
        test.setEnabled(client is not None)
        test.clicked.connect(self.test_skill)
        buttons.addWidget(test)
        buttons.addStretch()
        save = AppButton('Save Skill', variant='primary')
        save.clicked.connect(self.submit)
        cancel = AppButton('Cancel', variant='ghost')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.fields.changed.connect(self.update_preview)
        self.update_preview()

    def update_preview(self):
        table = self.fields.table
        lines = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item:
                lines.append((item.text() or 'Field name') + '\nExample value')
        self.preview.setPlainText('\n\n'.join(lines))

    def definition(self):
        return CustomSkillDefinition(self.original.id if self.original else unique_id(safe_id(self.name.text()), self.existing),
                                     self.name.text().strip(), self.description.text().strip(), self.detection.toPlainText().strip(),
                                     self.fields.definitions(), self.actions.selected(), self.enabled.isChecked())

    def submit(self):
        try:
            definition = self.definition()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.error.clear()
        self.saved.emit(definition)

    def test_skill(self):
        try:
            definition = self.definition()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        from .skill_test_dialog import SkillTestDialog
        dialog = SkillTestDialog(definition, self.client, self)
        self.tests.append(dialog)
        dialog.show()

    def done(self, result):
        for dialog in self.tests:
            dialog.close()
        super().done(result)
