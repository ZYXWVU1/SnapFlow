"""Non-secret endpoint editor. Saving never starts a subprocess."""
import json
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QSpinBox, QVBoxLayout)
from src.mcp.client.models import MCPConnectionProfile, now
from src.ui.design.components import AppButton


class ConnectionEditor(QDialog):
    def __init__(self, profile=None, parent=None):
        super().__init__(parent)
        self.original = profile
        self.result_profile = None
        self.setWindowTitle('Edit MCP Server' if profile else 'Add MCP Server')
        self.resize(560, 530)
        layout = QVBoxLayout(self)
        note = QLabel('Local executables run with your Windows user permissions. Review the command before connecting. Put credentials in secure storage, never in arguments or URLs.')
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        self.name = QLineEdit(profile.name if profile else '')
        self.transport = QComboBox()
        self.transport.addItem('Local subprocess (stdio)', 'stdio')
        self.transport.addItem('Streamable HTTP', 'streamable_http')
        self.executable = QLineEdit(profile.executable or '' if profile else '')
        self.arguments = QPlainTextEdit(json.dumps(list(profile.args) if profile else [], ensure_ascii=False))
        self.arguments.setMaximumHeight(85)
        self.directory = QLineEdit(profile.working_directory or '' if profile else '')
        self.secret_names = QLineEdit(', '.join(profile.secret_environment) if profile else '')
        self.secret_names.setPlaceholderText('Names only, e.g. API_KEY, ACCESS_TOKEN')
        self.secret_names.setMaxLength(4096)
        self.url = QLineEdit(profile.url or '' if profile else '')
        self.auth = QComboBox()
        self.auth.addItem('None', 'none')
        self.auth.addItem('Bearer token (secure storage)', 'bearer')
        self.auth.addItem('OAuth (browser authorization)', 'oauth')
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setValue(int(profile.timeout_seconds) if profile else 30)
        for label, widget in (('Name', self.name), ('Transport', self.transport), ('Executable', self.executable),
                ('Arguments (JSON array)', self.arguments), ('Working directory', self.directory),
                ('Environment secret names', self.secret_names),
                ('Endpoint URL', self.url), ('Authentication', self.auth), ('Timeout (seconds)', self.timeout)):
            form.addRow(label, widget)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        save = AppButton('Save disconnected profile', variant='primary')
        save.clicked.connect(self.submit)
        layout.addWidget(save)
        self.transport.currentIndexChanged.connect(self.update_transport)
        if profile:
            self.transport.setCurrentIndex(self.transport.findData(profile.transport))
            self.auth.setCurrentIndex(self.auth.findData(profile.auth_mode))
        self.update_transport()

    def update_transport(self):
        stdio = self.transport.currentData() == 'stdio'
        for widget in (self.executable, self.arguments, self.directory, self.secret_names):
            widget.setEnabled(stdio)
        self.url.setEnabled(not stdio)
        self.auth.setEnabled(not stdio)

    def profile(self):
        stdio = self.transport.currentData() == 'stdio'
        if len(self.arguments.toPlainText()) > 65536:
            raise ValueError('Argument list is too large.')
        return MCPConnectionProfile(id=self.original.id if self.original else 'server_' + uuid4().hex[:12],
            name=self.name.text().strip(), transport=self.transport.currentData(), enabled=False,
            executable=self.executable.text().strip() if stdio else None,
            args=json.loads(self.arguments.toPlainText()) if stdio else (),
            working_directory=self.directory.text().strip() or None if stdio else None,
            url=self.url.text().strip() if not stdio else None,
            auth_mode=self.auth.currentData() if not stdio else 'none',
            secret_environment=tuple(value.strip() for value in self.secret_names.text().split(',') if value.strip()) if stdio else (),
            timeout_seconds=self.timeout.value(), created_at=self.original.created_at if self.original else now(), updated_at=now())

    def submit(self):
        try:
            self.result_profile = self.profile()
        except (ValueError, TypeError, RecursionError) as exc:
            self.error.setText(str(exc)[:300])
            return
        self.accept()
