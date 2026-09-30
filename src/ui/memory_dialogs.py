"""Explicit Memory save preview and local record details."""
import json

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout)

from src.modes import ModeResult


class MemorySaveDialog(QDialog):
    def __init__(self, result, *, has_screenshot=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Save to Visual Memory')
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        note = QLabel('This result stays temporary until you select Save. Saving does not send another AI request.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        proposed = result.title if not isinstance(result, ModeResult) else (
            str(result.data.get('content_type', 'Extract')).title() + ' result'
            if result.data is not None else 'Screenshot note')
        self.title_field = QLineEdit(proposed)
        self.description_field = QLineEdit()
        self.tags_field = QLineEdit()
        self.tags_field.setPlaceholderText('Comma-separated tags')
        self.image_checkbox = QCheckBox('Save original screenshot locally')
        self.image_checkbox.setEnabled(has_screenshot)
        self.image_checkbox.setChecked(False)
        form.addRow('Title', self.title_field)
        form.addRow('Description', self.description_field)
        form.addRow('Tags', self.tags_field)
        self.note_field = None
        if isinstance(result, ModeResult) and result.data is None:
            self.note_field = QTextEdit()
            self.note_field.setPlaceholderText('Write the note you want to remember.')
            self.note_field.setMinimumHeight(100)
            form.addRow('Note', self.note_field)
        form.addRow('', self.image_checkbox)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        values = {'title': self.title_field.text().strip(),
                'description': self.description_field.text().strip(),
                'tags': [part.strip() for part in self.tags_field.text().split(',') if part.strip()],
                'save_screenshot': self.image_checkbox.isChecked()}
        if self.note_field is not None:
            values['note'] = self.note_field.toPlainText().strip()
        return values


class MemoryDetailDialog(QDialog):
    def __init__(self, store, memory_id, parent=None):
        super().__init__(parent)
        self.store = store
        self.record = store.get_record(memory_id)
        if self.record is None:
            raise ValueError('Memory record was not found.')
        self.changed = False
        self.setWindowTitle('Memory Details')
        self.resize(650, 700)
        layout = QVBoxLayout(self)
        image_path = store.image_path(self.record)
        if image_path and not QPixmap(str(image_path)).isNull():
            preview = QLabel()
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setPixmap(QPixmap(str(image_path)).scaled(560, 260,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            layout.addWidget(preview)
            open_image = QPushButton('Open Original Screenshot')
            open_image.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(image_path))))
            layout.addWidget(open_image)
        elif self.record.screenshot_reference:
            layout.addWidget(QLabel('The saved screenshot is missing. The extracted record is still available.'))
        form = QFormLayout()
        self.title_field = QLineEdit(self.record.title)
        self.description_field = QLineEdit(self.record.description)
        self.tags_field = QLineEdit(', '.join(self.record.tags))
        self.data_field = QTextEdit(json.dumps(self.record.structured_data, ensure_ascii=False, indent=2))
        self.data_field.setMinimumHeight(220)
        form.addRow('Title', self.title_field)
        form.addRow('Description', self.description_field)
        form.addRow('Tags', self.tags_field)
        form.addRow('Structured data (JSON)', self.data_field)
        layout.addLayout(form)
        layout.addWidget(QLabel(f'Saved: {self.record.saved_at}  ·  Source: {self.record.skill_id or self.record.source_type}'))
        actions = QHBoxLayout()
        for label, callback in [('Copy JSON', self.copy_json), ('Copy Markdown', self.copy_markdown),
                                ('Save Changes', self.save_changes), ('Delete', self.delete_record)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def copy_json(self):
        QApplication.clipboard().setText(json.dumps(self.record.structured_data, ensure_ascii=False, indent=2))

    def copy_markdown(self):
        lines = [f'# {self.record.title}', '']
        lines.extend(f'- **{key}:** {value}' for key, value in self.record.structured_data.items())
        QApplication.clipboard().setText('\n'.join(lines))

    def save_changes(self):
        try:
            data = json.loads(self.data_field.toPlainText())
            self.record = self.store.update_record(self.record.id,
                title=self.title_field.text(), description=self.description_field.text(),
                tags=[part.strip() for part in self.tags_field.text().split(',') if part.strip()],
                structured_data=data)
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, 'Unable to save Memory', str(exc))
            return
        self.changed = True
        QMessageBox.information(self, 'Visual Memory', 'Changes saved locally.')

    def delete_record(self):
        answer = QMessageBox.question(self, 'Delete Memory',
            'Delete this saved Memory and its owned screenshot?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_record(self.record.id)
        self.changed = True
        self.accept()
