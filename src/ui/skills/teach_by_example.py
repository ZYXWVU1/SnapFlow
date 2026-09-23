from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QTextEdit, QPushButton
from src.skills.custom.schema_generator import generate_schema
from .image_task import ImageTaskDialog


class TeachByExample(ImageTaskDialog):
    generated = Signal(object)

    def __init__(self, client, parent=None):
        super().__init__(client, parent)
        self.setWindowTitle('Teach AI a New Skill')
        self.layout.addWidget(QLabel('What should this skill do?'))
        self.purpose = QTextEdit()
        self.purpose.setAcceptRichText(False)
        self.purpose.setPlaceholderText('Track receipts and extract merchant, total and date.')
        self.layout.addWidget(self.purpose)
        self.layout.addWidget(QLabel('The selected image is sent to your configured AI provider when you generate.\nYou will review and edit the draft before saving.'))
        self.run_button = QPushButton('Generate Skill')
        self.run_button.clicked.connect(self.generate)
        self.layout.addWidget(self.run_button)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        self.layout.addWidget(cancel)

    def generate(self):
        purpose = self.purpose.toPlainText().strip()
        if not purpose or len(purpose) > 1000:
            self.status.setText('Describe the purpose in 1–1000 characters.')
            return
        client = self.client
        self.start_job(lambda image, cancelled: generate_schema(client, image, purpose), 'Generating Skill definition...')

    def completed(self, result):
        self.status.setText('Draft ready for review.')
        self.generated.emit(result)
