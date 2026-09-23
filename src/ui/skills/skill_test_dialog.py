from dataclasses import replace
from PySide6.QtWidgets import QPushButton, QLabel
from src.skills.custom.matcher import CustomSkillMatcher
from src.skills.custom.runtime_skill import RuntimeCustomSkill
from src.ui.structured_result import StructuredResult
from src.ui.skill_view import render_skill
from .image_task import ImageTaskDialog


class SkillTestDialog(ImageTaskDialog):
    def __init__(self, definition, client, parent=None):
        super().__init__(client, parent)
        self.definition = definition
        self.setWindowTitle('Test Skill · ' + definition.name)
        self.layout.addWidget(QLabel('Test the detection rule, then preview extracted fields.\nThe selected image is sent to your configured AI provider when you test.'))
        self.result = StructuredResult()
        self.result.hide()
        self.layout.addWidget(self.result, 1)
        self.run_button = QPushButton('Test Skill')
        self.run_button.clicked.connect(self.run_test)
        self.layout.addWidget(self.run_button)
        close = QPushButton('Back to Skills / Edit')
        close.clicked.connect(self.accept)
        self.layout.addWidget(close)

    def image_changed(self):
        self.result.hide()

    def run_test(self):
        self.result.hide()
        definition, client = replace(self.definition, enabled=True), self.client
        def operation(image, cancelled):
            match = CustomSkillMatcher(client).match(image, [definition])
            if cancelled.is_set():
                return None
            result = RuntimeCustomSkill(definition).extract(client, image, match.confidence) if match.skill_id else None
            return match, result
        self.start_job(operation, 'Testing Skill...')

    def completed(self, payload):
        match, result = payload
        if match.failed:
            self.status.setText('Matching could not be completed. Check your connection and provider settings, then retry.')
        elif result is None:
            self.status.setText(f'Match confidence: {match.confidence:.0%}\nThis screenshot probably does not match this Skill. Adjust detection or try another screenshot.')
        else:
            self.status.setText(f'Match confidence: {match.confidence:.0%}\nReview the extracted fields below.')
            render_skill(self.result, result)
            self.result.show()
