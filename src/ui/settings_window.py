import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from src.app_version import APP_NAME, APP_VERSION
from src.config import Config, DEFAULT_AI_BASE_URL, DEFAULT_AI_MODEL
from src.prompts import MODES
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING
from src.ui.focus_aware_window import FocusAwareTopmostMixin


class SettingsWindow(FocusAwareTopmostMixin, QDialog):
    submitted = Signal(object, str)
    remove_key_requested = Signal()

    def __init__(self, config: Config, parent=None, *, api_key_configured=None, api_key_stored=False) -> None:
        super().__init__(parent)
        self.configure_focus_topmost(config.always_on_top)
        self.config = config
        self.setWindowTitle(f"{APP_NAME} - Settings")
        self.resize(560, 570)
        self.setMinimumSize(440, 400)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        root.addWidget(PageHeader('Settings', 'Choose how capture, AI, and appearance work.'))
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        content = QWidget()
        self.content_scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, SPACING['sm'], 0)
        root.addWidget(self.content_scroll, 1)
        self.capture_card = Card()
        self.capture_card.content.addWidget(QLabel('Capture'))
        capture_form = QFormLayout()
        self.hotkey = QLineEdit(config.hotkey)
        self.mode = QComboBox()
        for value, label in MODES.items():
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(config.default_mode))
        self.width = QSpinBox()
        self.width.setRange(320, 8192)
        self.width.setValue(config.max_image_width)
        self.on_top = QCheckBox("Keep results on top while active")
        self.on_top.setChecked(config.always_on_top)
        self.theme = QComboBox()
        for value, label in (('system', 'System'), ('light', 'Light'), ('dark', 'Dark')):
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(self.theme.findData(config.theme))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_configured = bool(os.getenv("AI_API_KEY")) if api_key_configured is None else bool(api_key_configured)
        self.api_key_stored = bool(api_key_stored)
        self.key.setPlaceholderText(
            "Configured; blank keeps current key" if self.api_key_configured
            else "Enter a key to save it securely")
        self.ai_base_url = QLineEdit(
            config.ai_base_url or os.getenv('AI_BASE_URL') or DEFAULT_AI_BASE_URL)
        self.ai_base_url.setAccessibleName('AI API base URL')
        self.ai_model = QLineEdit(config.ai_model or os.getenv('AI_MODEL') or DEFAULT_AI_MODEL)
        self.ai_model.setAccessibleName('AI model')
        capture_form.addRow("Hotkey", self.hotkey)
        capture_form.addRow("Maximum image width", self.width)
        capture_form.addRow(self.on_top)
        self.capture_card.content.addLayout(capture_form)
        body.addWidget(self.capture_card)
        self.ai_card = Card()
        self.ai_card.content.addWidget(QLabel('AI'))
        ai_form = QFormLayout()
        ai_form.addRow("Default mode", self.mode)
        ai_form.addRow("API key", self.key)
        ai_form.addRow("OpenAI-compatible API endpoint", self.ai_base_url)
        ai_form.addRow("Model", self.ai_model)
        self.ai_card.content.addLayout(ai_form)
        self.credential_note = QLabel(
            "Keys entered here are saved in Windows Credential Manager and are excluded from backups. "
            "Leave the field blank to keep the current key. Selected screenshots are sent to your configured AI service.")
        self.credential_note.setWordWrap(True)
        self.credential_note.setProperty('role', 'muted')
        self.ai_card.content.addWidget(self.credential_note)
        self.remove_key_button = AppButton('Remove Saved Key', variant='danger')
        self.remove_key_button.setEnabled(self.api_key_stored)
        self.remove_key_button.clicked.connect(lambda _checked=False: self.remove_key_requested.emit())
        self.ai_card.content.addWidget(self.remove_key_button)
        body.addWidget(self.ai_card)
        usage = Card()
        usage.content.addWidget(QLabel('Usage & Costs'))
        usage_form = QFormLayout()
        self.cost_warning = QDoubleSpinBox()
        self.cost_warning.setRange(0, 1000000)
        self.cost_warning.setDecimals(2)
        self.cost_warning.setSingleStep(1.0)
        self.cost_warning.setSuffix(' USD')
        self.cost_warning.setValue(config.monthly_cost_warning_usd)
        usage_form.addRow('Warn at monthly estimated spend', self.cost_warning)
        self.max_evaluation_cases = QSpinBox()
        self.max_evaluation_cases.setRange(1, 10000)
        self.max_evaluation_cases.setValue(config.max_evaluation_cases)
        usage_form.addRow('Maximum Evaluation cases per run', self.max_evaluation_cases)
        usage.content.addLayout(usage_form)
        usage_note = QLabel('Set to 0 to disable the warning. Cost figures are estimates when the model rate is known; provider invoices may differ.')
        usage_note.setWordWrap(True)
        usage_note.setProperty('role', 'muted')
        usage.content.addWidget(usage_note)
        body.addWidget(usage)
        self.appearance_card = Card()
        self.appearance_card.content.addWidget(QLabel('Appearance'))
        appearance_form = QFormLayout()
        appearance_form.addRow('Theme', self.theme)
        self.appearance_card.content.addLayout(appearance_form)
        body.addWidget(self.appearance_card)
        about = Card()
        about.content.addWidget(QLabel('About & Updates'))
        about.content.addWidget(QLabel(f'{APP_NAME} {APP_VERSION}'))
        self.automatic_updates = QCheckBox('Check for updates when SnapFlow starts')
        self.automatic_updates.setChecked(config.automatic_update_checks)
        about.content.addWidget(self.automatic_updates)
        update_note = QLabel(
            'An update check contacts the configured GitHub Releases API and sends no screenshots or account data. '
            'It shows release notes and opens the official release page; SnapFlow never downloads or installs updates automatically.')
        update_note.setWordWrap(True)
        update_note.setProperty('role', 'muted')
        about.content.addWidget(update_note)
        body.addWidget(about)
        body.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def submit(self) -> None:
        try:
            config = Config(
                hotkey=self.hotkey.text().strip().lower(),
                default_mode=self.mode.currentData(),
                max_image_width=self.width.value(),
                always_on_top=self.on_top.isChecked(),
                smart_classification_threshold=self.config.smart_classification_threshold,
                theme=self.theme.currentData(),
                onboarding_complete=self.config.onboarding_complete,
                monthly_cost_warning_usd=self.cost_warning.value(),
                max_evaluation_cases=self.max_evaluation_cases.value(),
                automatic_update_checks=self.automatic_updates.isChecked(),
                ai_base_url=self.ai_base_url.text().strip(),
                ai_model=self.ai_model.text().strip())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        self.submitted.emit(config, self.key.text().strip())

    def refresh_api_key_state(self, *, configured, stored):
        self.api_key_configured = bool(configured)
        self.api_key_stored = bool(stored)
        self.key.setPlaceholderText(
            "Configured; blank keeps current key" if self.api_key_configured
            else "Enter a key to save it securely")
        self.remove_key_button.setEnabled(self.api_key_stored)
