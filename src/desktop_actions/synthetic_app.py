"""Owned Windows acceptance app. All values are fabricated; no third-party app writes."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLineEdit, QCheckBox, QListWidget, QLabel, QSlider
from PySide6.QtCore import Qt


class SyntheticActionWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('SnapFlow Synthetic Desktop Action Harness')
        self.setObjectName('snapflow_synthetic')
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        self.editor = QLineEdit()
        self.editor.setObjectName('document_editor')
        self.editor.setAccessibleName('Document')
        self.setting = QCheckBox('Dark Mode')
        self.setting.setObjectName('dark_setting')
        self.selection = QListWidget()
        self.selection.addItems(('Light Appearance', 'Dark Appearance'))
        self.selection.setObjectName('dark_appearance')
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName('volume_range')
        self.slider.setAccessibleName('Volume')
        self.status = QLabel('Unsaved')
        self.status.setObjectName('save_status')
        self.save = QPushButton('Save')
        self.save.setObjectName('save_document')
        self.save.clicked.connect(lambda: self.status.setText('Saved'))
        self.submit = QPushButton('Submit')
        self.submit.setObjectName('submit_form')
        self.submission = QLabel('No Submission')
        self.submission.setObjectName('submission_status')
        self.submit.clicked.connect(lambda: self.submission.setText('Submitted SF-0001'))
        self.password = QLineEdit('FABRICATED_PASSWORD_DO_NOT_READ')
        self.password.setObjectName('password')
        self.password.setAccessibleName('Password')
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        for widget in (self.editor, self.setting, self.selection, self.slider, self.save,
                       self.status, self.submit, self.submission, self.password):
            layout.addWidget(widget)
