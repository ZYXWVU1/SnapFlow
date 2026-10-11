"""Native settings for source policies and per-application overrides."""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt
from src.ui.design.components import AppButton


def policy_combo(policy='disabled'):
    widget = QComboBox()
    for label, value in (('Disabled', 'disabled'), ('Ask each capture', 'ask'), ('Allowed after session consent', 'session')):
        widget.addItem(label, value)
    widget.setCurrentIndex(widget.findData(policy))
    return widget


class DesktopPolicyWidget(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        note = QLabel('Desktop Context is temporary. Clipboard is disabled by default. Session permissions require confirmation and are cleared when stopped. Private Mode keeps context local.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.metadata = QCheckBox('Allow active app and window metadata')
        self.metadata.setChecked(config.desktop_metadata_enabled)
        layout.addWidget(self.metadata)
        form = QFormLayout()
        self.policies = {}
        for key, label in (('ui', 'UI structure'), ('selection', 'Selected text'), ('clipboard', 'Clipboard text')):
            combo = policy_combo(getattr(config, 'desktop_' + key + '_policy'))
            self.policies[key] = combo
            form.addRow(label, combo)
        layout.addLayout(form)
        layout.addWidget(QLabel('Additional blocked apps (one process name per line):'))
        self.blocked = QPlainTextEdit('\n'.join(config.desktop_blocked_apps))
        self.blocked.setMaximumHeight(75)
        layout.addWidget(self.blocked)
        layout.addWidget(QLabel('Per-app overrides (password/security apps remain blocked):'))
        self.apps = QTableWidget(0, 5)
        self.apps.setHorizontalHeaderLabels(('Process', 'Metadata', 'UI', 'Selection', 'Clipboard'))
        self.apps.setMinimumHeight(125)
        layout.addWidget(self.apps)
        row = QHBoxLayout()
        self.app_name = QLineEdit()
        self.app_name.setMaxLength(128)
        self.app_name.setPlaceholderText('Code.exe')
        row.addWidget(self.app_name)
        add = AppButton('Add App')
        add.clicked.connect(lambda: self.add_app(self.app_name.text().strip(), {}))
        row.addWidget(add)
        remove = AppButton('Remove Row')
        remove.clicked.connect(lambda: self.apps.removeRow(self.apps.currentRow()) if self.apps.currentRow() >= 0 else None)
        row.addWidget(remove)
        layout.addLayout(row)
        for name, policies in config.desktop_app_policies.items():
            self.add_app(name, policies)

    def add_app(self, name, policies):
        if not name or self.apps.rowCount() >= 100:
            return
        row = self.apps.rowCount()
        self.apps.insertRow(row)
        self.apps.setItem(row, 0, QTableWidgetItem(name))
        metadata = QCheckBox()
        metadata.setChecked(policies.get('metadata', self.metadata.isChecked()))
        self.apps.setCellWidget(row, 1, metadata)
        for col, source in enumerate(('ui', 'selection', 'clipboard'), 2):
            self.apps.setCellWidget(row, col, policy_combo(policies.get(source, self.policies[source].currentData())))
        self.app_name.clear()

    def values(self):
        apps = {}
        for row in range(self.apps.rowCount()):
            name = self.apps.item(row, 0).text().strip().casefold()
            if name in apps:
                raise ValueError('Duplicate per-app process name.')
            apps[name] = {'metadata': self.apps.cellWidget(row, 1).isChecked(), **{
                source: self.apps.cellWidget(row, col).currentData()
                for col, source in enumerate(('ui', 'selection', 'clipboard'), 2)}}
        return {'desktop_metadata_enabled': self.metadata.isChecked(),
            **{'desktop_' + source + '_policy': combo.currentData() for source, combo in self.policies.items()},
            'desktop_blocked_apps': tuple(x.strip().casefold() for x in self.blocked.toPlainText().splitlines() if x.strip()),
            'desktop_app_policies': apps}


class ContextPermissionDialog(QDialog):
    """Capture or session consent. No checkbox grants writes or AI transmission."""
    def __init__(self, permissions, parent=None, *, assistance=False):
        super().__init__(parent)
        self.setWindowTitle('Start Desktop Assistance' if assistance else 'Capture Desktop Context')
        layout = QVBoxLayout(self)
        note = QLabel('Choose sources for this capture. Disabled and blocked-app sources are never read. Context is shown for review before AI use.' if not assistance else
            'Assistance is visible and stoppable. Only confirmed session sources may be read. No screenshots or clipboard are collected in the background. Suggestions never execute actions.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.sources = {}
        for source in ('ui', 'selection', 'clipboard'):
            if assistance and source == 'clipboard':
                continue
            policy = permissions.policies[source]
            overrides = [policies.get(source, policy) for policies in permissions.apps.values()]
            control = QCheckBox(f'{source.title()} · default {policy}' + (' · app overrides apply' if overrides else ''))
            choices = (policy, *overrides)
            control.setEnabled('session' in choices if assistance else any(x != 'disabled' for x in choices))
            layout.addWidget(control)
            self.sources[source] = control
        layout.addWidget(QLabel('App metadata follows your saved global and per-app settings.'))
        self.duration = QComboBox()
        for text, minutes in (('15 minutes', 15), ('30 minutes', 30), ('1 hour', 60), ('Until stopped', None)):
            self.duration.addItem(text, minutes)
        self.level = QComboBox()
        for level in ('off', 'minimal', 'normal', 'frequent'):
            self.level.addItem(level.title(), level)
        self.level.setCurrentIndex(1)
        self.allowed_apps = QLineEdit()
        self.allowed_apps.setPlaceholderText('Optional allowed apps, e.g. Code.exe, chrome.exe')
        self.fullscreen = QCheckBox('Allow suggestions in full-screen apps')
        if assistance:
            layout.addWidget(self.duration)
            layout.addWidget(self.level)
            layout.addWidget(self.allowed_apps)
            layout.addWidget(self.fullscreen)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self):
        return tuple(source for source, control in self.sources.items() if control.isChecked() and control.isEnabled())
