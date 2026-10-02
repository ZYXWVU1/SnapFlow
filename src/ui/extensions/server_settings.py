"""Explicit, disabled-by-default server sharing controls."""
from dataclasses import asdict
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel
from src.ui.design.components import AppButton, Card


class ServerSettings(Card):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        title = QLabel('SnapFlow MCP Server · local external-host access')
        self.content.addWidget(title)
        self.description = QLabel('Enable the local stdio bridge, then choose exactly what hosts may read. '
            'Workflow execution always requires Allow Once inside SnapFlow. Built-in Skill metadata is available when enabled.')
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.content.addWidget(self.description)
        self.flags = {}
        for key, label in (('enabled', 'Enable MCP Server'), ('share_memory', 'Share structured Memory text'),
                ('share_images', 'Share original Memory screenshot images'), ('share_workflows', 'Share Workflow metadata and previews'),
                ('share_history', 'Share safe Workflow execution history'), ('share_custom_skills', 'Share custom Skill metadata'),
                ('allow_workflow_execution', 'Allow Workflow execution requests with fresh approval')):
            checkbox = QCheckBox(label)
            self.flags[key] = checkbox
            self.content.addWidget(checkbox)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.content.addWidget(self.status)
        self.apply_button = AppButton('Apply server sharing settings')
        self.apply_button.clicked.connect(self.apply)
        self.content.addWidget(self.apply_button)
        service.changed.connect(self.reload)
        self.reload()

    def reload(self):
        for key, value in asdict(self.service.policy.snapshot()).items():
            self.flags[key].setChecked(value)
            self.flags[key].setEnabled(not self.service.policy.warning)
        self.apply_button.setEnabled(not self.service.policy.warning)
        self.status.setText(self.service.warning or ('Local bridge running. See docs/extensions/MCP_SERVER.md for host configuration.'
            if self.service.ipc.listener.isListening() else 'Server disabled. No external-host access.'))

    def apply(self):
        try:
            self.service.configure(**{key: checkbox.isChecked() for key, checkbox in self.flags.items()})
        except Exception:
            self.status.setText('Unable to apply server settings. Sharing remains at the last saved permissions.')
