"""First-run setup for OpenAI-compatible AI access, privacy, and capture."""
import os
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from src.app_version import APP_NAME
from src.config import Config, DEFAULT_AI_BASE_URL, DEFAULT_AI_MODEL
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING


class OnboardingDialog(QDialog):
    setup_requested = Signal(str, str, str)
    connection_test_requested = Signal(str, str, str)
    capture_test_requested = Signal()
    privacy_settings_requested = Signal()

    def __init__(self, config: Config | None = None, parent=None, *, hotkey_error=''):
        super().__init__(parent)
        config = config or Config()
        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setMinimumSize(500, 520)
        self.resize(540, 620)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.provider_page = QWidget()
        provider_layout = QVBoxLayout(self.provider_page)
        provider_layout.setContentsMargins(0, 0, 0, 0)
        provider_layout.addWidget(PageHeader(
            f"Welcome to {APP_NAME}",
            "Understand anything on your screen. Set up AI access or continue without a key."))

        provider_card = Card()
        provider_card.content.addWidget(QLabel('AI provider setup'))
        self.provider_combo = QComboBox()
        self.provider_combo.addItem('OpenAI', 'openai')
        self.provider_combo.addItem('OpenAI-compatible endpoint', 'openai_compatible')
        current_url = config.ai_base_url or os.getenv('AI_BASE_URL') or DEFAULT_AI_BASE_URL
        try:
            current_host = (urlsplit(current_url).hostname or '').lower()
        except ValueError:
            current_host = ''
        initial_provider = 'openai' if current_host == 'api.openai.com' else 'openai_compatible'
        self.provider_combo.setCurrentIndex(self.provider_combo.findData(initial_provider))
        self.base_url_input = QLineEdit(current_url)
        self.base_url_input.setAccessibleName('AI API base URL')
        self.base_url_input.setPlaceholderText(DEFAULT_AI_BASE_URL)
        self.base_url_input.setEnabled(initial_provider != 'openai')
        self.model_input = QLineEdit(
            config.ai_model or os.getenv('AI_MODEL') or DEFAULT_AI_MODEL)
        self.model_input.setAccessibleName('AI model')
        self.model_input.setPlaceholderText('For example: gpt-4.1-mini')
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setAccessibleName('AI API key')
        self.key_input.setPlaceholderText('Paste AI API key (optional)')
        provider_form = QFormLayout()
        provider_form.addRow('Provider', self.provider_combo)
        provider_form.addRow('API base URL', self.base_url_input)
        provider_form.addRow('Model', self.model_input)
        provider_form.addRow('API key', self.key_input)
        provider_card.content.addLayout(provider_form)
        provider_note = QLabel(
            'SnapFlow uses the OpenAI Chat Completions format. OpenAI is preselected; other services must '
            'provide a compatible HTTPS endpoint and model. The short connection test sends one text request '
            'and may incur a provider charge. Your key is saved only when you finish setup.')
        provider_note.setWordWrap(True)
        provider_note.setProperty('role', 'muted')
        provider_card.content.addWidget(provider_note)
        provider_layout.addWidget(provider_card)

        self.connection_status = QLabel('')
        self.connection_status.setWordWrap(True)
        self.connection_status.setTextFormat(Qt.TextFormat.PlainText)
        provider_layout.addWidget(self.connection_status)
        provider_layout.addStretch(1)
        self.stack.addWidget(self.provider_page)

        self.capture_page = QWidget()
        capture_layout = QVBoxLayout(self.capture_page)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        capture_layout.addWidget(PageHeader(
            'Privacy & Capture',
            'Review where screenshots go, then verify that screen selection works.'))

        privacy = Card()
        privacy_text = QLabel(
            'Screenshots selected for Ask, Smart, extraction, Skills, or Evaluation are sent to your '
            'configured AI service. SnapFlow keeps a capture in memory and does not save it unless you '
            'explicitly choose to keep a verified example. AI keys are stored in Windows Credential Manager '
            'and are not included in backups. SnapFlow has no analytics or automatic crash reporting.')
        privacy_text.setWordWrap(True)
        privacy_text.setProperty('role', 'muted')
        privacy.content.addWidget(privacy_text)
        self.privacy_settings_button = QPushButton('Open Settings to review privacy or change the shortcut')
        self.privacy_settings_button.clicked.connect(self.privacy_settings_requested)
        privacy.content.addWidget(self.privacy_settings_button)
        capture_layout.addWidget(privacy)

        hotkey_card = Card()
        hotkey_card.content.addWidget(QLabel('Capture shortcut'))
        self.hotkey_label = QLabel()
        self.hotkey_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hotkey_label.setText(' + '.join(part.capitalize() for part in config.hotkey.split('+')))
        hotkey_card.content.addWidget(self.hotkey_label)
        self.hotkey_warning = QLabel(hotkey_error)
        self.hotkey_warning.setWordWrap(True)
        self.hotkey_warning.setTextFormat(Qt.TextFormat.PlainText)
        self.hotkey_warning.setVisible(bool(hotkey_error))
        hotkey_card.content.addWidget(self.hotkey_warning)
        self.test_capture_button = AppButton('Test screen capture')
        self.test_capture_button.clicked.connect(self.capture_test_requested)
        hotkey_card.content.addWidget(self.test_capture_button)
        capture_note = QLabel(
            'The test lets you select a region, then discards it locally. It is not sent to an AI service. '
            'If the shortcut is already used by another app, change it in Settings; the Capture button remains available.')
        capture_note.setWordWrap(True)
        capture_note.setProperty('role', 'muted')
        hotkey_card.content.addWidget(capture_note)
        capture_layout.addWidget(hotkey_card)
        self.capture_status = QLabel('')
        self.capture_status.setWordWrap(True)
        self.capture_status.setTextFormat(Qt.TextFormat.PlainText)
        capture_layout.addWidget(self.capture_status)
        capture_layout.addStretch(1)
        self.stack.addWidget(self.capture_page)

        actions = QHBoxLayout()
        self.back_button = AppButton('Back')
        self.back_button.clicked.connect(self.show_provider_step)
        actions.addWidget(self.back_button)
        actions.addStretch(1)
        self.test_connection_button = AppButton('Test Connection')
        self.continue_button = AppButton('Continue to privacy and capture')
        self.save_button = AppButton('Finish Setup', variant='primary')
        self.test_connection_button.clicked.connect(self._test_connection)
        self.continue_button.clicked.connect(self.show_capture_step)
        self.save_button.clicked.connect(self._submit)
        actions.addWidget(self.test_connection_button)
        actions.addWidget(self.continue_button)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self._refresh_step()

    def _provider_changed(self, _index):
        if self.provider_combo.currentData() == 'openai':
            self.base_url_input.setText(DEFAULT_AI_BASE_URL)
            self.base_url_input.setEnabled(False)
        else:
            self.base_url_input.setEnabled(True)
            if not self.base_url_input.text().strip() or self.base_url_input.text().strip() == DEFAULT_AI_BASE_URL:
                self.base_url_input.setText('')
                self.base_url_input.setPlaceholderText('https://your-provider.example/v1')

    def _connection_values(self):
        if self.provider_combo.currentData() == 'openai':
            endpoint = DEFAULT_AI_BASE_URL
        else:
            endpoint = self.base_url_input.text().strip()
        return (self.key_input.text().strip(), endpoint, self.model_input.text().strip())

    def _test_connection(self):
        key, endpoint, model = self._connection_values()
        if not endpoint or not model:
            self.connection_status.setText('Enter an endpoint and model before testing the connection.')
            return
        self.connection_status.setText('Testing the configured AI service…')
        self.test_connection_button.setEnabled(False)
        self.connection_test_requested.emit(key, endpoint, model)

    def set_connection_test_result(self, succeeded, message):
        self.test_connection_button.setEnabled(True)
        self.connection_status.setText(message)

    def set_capture_test_result(self, succeeded, message):
        self.test_capture_button.setEnabled(True)
        self.capture_status.setText(message)

    def show_capture_step(self):
        self.stack.setCurrentWidget(self.capture_page)
        self._refresh_step()

    def show_provider_step(self):
        self.stack.setCurrentWidget(self.provider_page)
        self._refresh_step()

    def _refresh_step(self):
        on_provider_page = self.stack.currentWidget() is self.provider_page
        self.back_button.setVisible(not on_provider_page)
        self.test_connection_button.setVisible(on_provider_page)
        self.continue_button.setVisible(on_provider_page)
        self.save_button.setVisible(not on_provider_page)

    def _submit(self):
        key, endpoint, model = self._connection_values()
        self.setup_requested.emit(key, endpoint, model)
