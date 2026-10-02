"""Explicit connection, capability review, invocation and context selection."""
from dataclasses import replace
import json
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget)
from src.mcp.client.transports import credential_id
from src.mcp.bridge.tools import register_actions
from src.ui.design.components import AppButton, Card, PageHeader
from .connection_editor import ConnectionEditor
from .schema_form import SchemaForm


class ExtensionsPage(QScrollArea):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        layout = QVBoxLayout(body)
        self.heading = PageHeader('Extensions', 'Connect MCP servers, review tools, and choose external context.')
        layout.addWidget(self.heading)
        if getattr(self.service, 'server_service', None) is not None:
            from .server_settings import ServerSettings
            self.server_settings = ServerSettings(self.service.server_service)
            layout.addWidget(self.server_settings)
        controls = QHBoxLayout()
        for label, callback in (('Add Server', self.add_server), ('Import', self.import_profiles), ('Export', self.export_profiles)):
            button = AppButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.profiles = QListWidget()
        self.profiles.setMaximumHeight(145)
        self.profiles.currentRowChanged.connect(self.show_profile)
        layout.addWidget(self.profiles)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        for label, callback in (('Connect', self.connect_server), ('Disconnect', self.disconnect_server),
                ('Refresh', self.refresh_server), ('Edit', self.edit_server), ('Remove', self.remove_server), ('Set token', self.set_token)):
            button = AppButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        forget = AppButton('Disconnect and forget credentials')
        forget.clicked.connect(self.forget_credentials)
        layout.addWidget(forget)
        environment = AppButton('Set environment secret')
        environment.clicked.connect(self.set_environment_secret)
        layout.addWidget(environment)
        tool_card = Card()
        tool_card.content.addWidget(QLabel('Tools · unreviewed tools are disabled'))
        self.tools = QComboBox()
        self.tools.currentIndexChanged.connect(self.show_tool)
        tool_card.content.addWidget(self.tools)
        self.tool_description = QLabel()
        self.tool_description.setTextFormat(Qt.TextFormat.PlainText)
        self.tool_description.setWordWrap(True)
        tool_card.content.addWidget(self.tool_description)
        self.form_box = QVBoxLayout()
        tool_card.content.addLayout(self.form_box)
        self.form = None
        policy = QHBoxLayout()
        self.risk, self.permission = QComboBox(), QComboBox()
        for risk in ('unknown', 'read_only', 'local_write', 'external_write', 'sensitive', 'destructive'):
            self.risk.addItem(risk.replace('_', ' ').title(), risk)
        for mode in ('disabled', 'ask_every_time', 'allowed'):
            self.permission.addItem(mode.replace('_', ' ').title(), mode)
        policy.addWidget(self.risk)
        policy.addWidget(self.permission)
        review = AppButton('Save permission review')
        review.clicked.connect(self.review_tool)
        policy.addWidget(review)
        tool_card.content.addLayout(policy)
        run = AppButton('Preview and Run Tool', variant='primary')
        run.clicked.connect(self.run_tool)
        tool_card.content.addWidget(run)
        layout.addWidget(tool_card)
        context_card = Card()
        context_card.content.addWidget(QLabel('Resources · read only after selection'))
        self.resources = QComboBox()
        context_card.content.addWidget(self.resources)
        resource = AppButton('Read and Add to Context')
        resource.clicked.connect(self.select_resource)
        context_card.content.addWidget(resource)
        context_card.content.addWidget(QLabel('Resource templates · choose parameters before reading'))
        self.templates = QComboBox()
        context_card.content.addWidget(self.templates)
        expand = AppButton('Expand Template and Add to Context')
        expand.clicked.connect(self.select_template)
        context_card.content.addWidget(expand)
        context_card.content.addWidget(QLabel('Prompts · render and review as external text'))
        self.prompts = QComboBox()
        context_card.content.addWidget(self.prompts)
        prompt = AppButton('Render Prompt')
        prompt.clicked.connect(self.select_prompt)
        context_card.content.addWidget(prompt)
        layout.addWidget(context_card)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(160)
        self.output.setAccessibleName('MCP operation output')
        layout.addWidget(self.output)
        self.service.state_changed.connect(self.state_changed)
        self.service.completed.connect(self.completed)
        self.reload()

    def profile(self):
        profiles = self.service.storage.list_profiles()
        row = self.profiles.currentRow()
        return profiles[row] if 0 <= row < len(profiles) else None

    def tool(self):
        return self.tools.currentData()

    def reload(self):
        current = self.profile()
        self.profiles.blockSignals(True)
        self.profiles.clear()
        profiles = self.service.storage.list_profiles()
        for profile in profiles:
            self.profiles.addItem(profile.name)
        self.profiles.blockSignals(False)
        self.profiles.setCurrentRow(next((i for i, p in enumerate(profiles) if current and p.id == current.id), 0) if profiles else -1)
        self.show_profile()

    @Slot(object)
    def state_changed(self, state):
        if self.profile() and state.connection_id == self.profile().id:
            self.show_profile()

    @Slot(object)
    def completed(self, result):
        callback, value, error = result
        if error:
            self.output.setPlainText(error)
        elif callback:
            callback(value)
        self.show_profile()

    def watch(self, future, callback=None):
        self.service.watch(future, callback)

    def show_profile(self, *_):
        profile = self.profile()
        self.tools.clear()
        self.resources.clear()
        self.templates.clear()
        self.prompts.clear()
        if not profile:
            self.detail.setText(self.service.storage.warning or 'No MCP servers. Add a reviewed server to begin.')
            return
        state = self.service.manager.state(profile.id)
        snapshot = self.service.manager.snapshot(profile.id)
        self.detail.setText(f'{profile.name} · {profile.transport}\nState: {state.status.replace("_", " ")}\n'
            f'Server: {state.server_name or "—"} {state.server_version}\nProtocol: {state.protocol_version or "—"}\n'
            f'Tools: {len(snapshot.tools)} · Resources: {len(snapshot.resources)} · Templates: {len(snapshot.resource_templates)} · Prompts: {len(snapshot.prompts)}\n{state.error}')
        for tool in snapshot.tools:
            self.tools.addItem(tool.title, tool)
        for resource in snapshot.resources:
            self.resources.addItem(resource.get('title') or resource.get('name') or resource['uri'], resource['uri'])
        for template in snapshot.resource_templates:
            self.templates.addItem(template.get('title') or template.get('name') or template['uri_template'], template)
        for prompt in snapshot.prompts:
            self.prompts.addItem(prompt.get('title') or prompt['name'], prompt)

    def show_tool(self, *_):
        while self.form_box.count():
            item = self.form_box.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.form = None
        tool = self.tool()
        if tool is None:
            self.tool_description.clear()
            return
        self.tool_description.setText(tool.description)
        self.form = SchemaForm(tool.input_schema)
        self.form_box.addWidget(self.form)
        policy = self.service.manager.permissions.policy(tool.connection_id, tool.name, tool.fingerprint)
        self.risk.setCurrentIndex(self.risk.findData(policy['risk']))
        self.permission.setCurrentIndex(self.permission.findData(policy['mode']))

    def add_server(self):
        self.edit_server(new=True)

    def edit_server(self, *_args, new=False):
        profile = None if new else self.profile()
        if profile and self.service.manager.state(profile.id).status not in ('disconnected', 'error', 'needs_authentication'):
            self.output.setPlainText('Disconnect before editing this profile.')
            return
        dialog = ConnectionEditor(profile, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.service.storage.save_profile(dialog.result_profile)
                self.reload()
            except ValueError as exc:
                self.output.setPlainText(str(exc))

    def connect_server(self):
        profile = self.profile()
        if not profile:
            return
        box = QMessageBox(self)
        box.setWindowTitle('Connect MCP Server')
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText('Connect to this endpoint?' if profile.transport != 'stdio' else 'Run this local executable with your Windows user permissions?')
        box.setDetailedText(profile.url if profile.url else json.dumps({'executable': profile.executable,
            'args': profile.args, 'cwd': profile.working_directory, 'environment_credential_names': profile.secret_environment},
            ensure_ascii=False, indent=2))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        if not profile.enabled:
            self.service.storage.save_profile(replace(profile, enabled=True))
        self.watch(self.service.manager.connect(profile.id))

    def disconnect_server(self):
        profile = self.profile()
        if profile:
            self.watch(self.service.manager.disconnect(profile.id))

    def refresh_server(self):
        if self.profile():
            self.watch(self.service.manager.refresh(self.profile().id))

    def forget_credentials(self):
        if self.profile():
            self.watch(self.service.manager.forget_credentials(self.profile().id),
                lambda _: self.output.setPlainText('Disconnected. Locally stored credentials removed.'))

    def remove_server(self):
        if self.profile():
            self.watch(self.service.manager.remove_profile(self.profile().id), lambda _: self.reload())

    def set_token(self):
        profile = self.profile()
        if not profile or profile.auth_mode != 'bearer' or self.service.credentials is None:
            self.output.setPlainText('Choose a bearer-authenticated HTTP profile first.')
            return
        token, accepted = QInputDialog.getText(self, 'MCP authentication', 'Bearer token (Windows Credential Manager)', QLineEdit.EchoMode.Password)
        if accepted and token:
            try:
                self.service.credentials.set(credential_id(profile.id), token)
                self.output.setPlainText('Token saved securely. Reconnect to use it.')
            except Exception:
                self.output.setPlainText('Unable to store this credential securely.')

    def set_environment_secret(self):
        profile = self.profile()
        if profile is None or profile.transport != 'stdio' or not profile.secret_environment or self.service.credentials is None:
            self.output.setPlainText('Add environment secret names to a stdio profile first. Values are stored separately.')
            return
        if self.service.manager.state(profile.id).status not in ('disconnected', 'error', 'needs_authentication'):
            self.output.setPlainText('Disconnect before changing environment credentials.')
            return
        name, accepted = QInputDialog.getItem(self, 'Stdio environment credential', 'Configured variable',
            list(profile.secret_environment), editable=False)
        if not accepted:
            return
        value, accepted = QInputDialog.getText(self, 'Secure environment credential',
            'Value (Windows Credential Manager)', QLineEdit.EchoMode.Password)
        if accepted and value:
            try:
                self.service.credentials.set(credential_id(profile.id, name), value)
                self.output.setPlainText('Environment credential saved securely. Connect to use it.')
            except Exception:
                self.output.setPlainText('Unable to store this credential securely.')

    def review_tool(self):
        tool = self.tool()
        if tool:
            try:
                self.service.manager.permissions.review(tool.connection_id, tool.name, tool.fingerprint,
                    risk=self.risk.currentData(), mode=self.permission.currentData())
                register_actions(self.service.manager, tool.connection_id)
                self.output.setPlainText('Permission review saved. Writes still require approval for every call.')
            except ValueError as exc:
                self.output.setPlainText(str(exc))

    def run_tool(self):
        tool = self.tool()
        if tool is None or self.form is None:
            return
        try:
            arguments = self.form.value()
            policy = self.service.manager.permissions.policy(tool.connection_id, tool.name, tool.fingerprint)
            if policy['mode'] == 'disabled' or policy['risk'] in ('unknown', 'destructive'):
                raise ValueError('Review and enable this tool before running it.')
            approved = self.service.approve(tool, arguments, policy)
            if not approved:
                return
            self.watch(self.service.manager.call_tool(tool.connection_id, tool.name, arguments, approved=True,
                expected_fingerprint=tool.fingerprint),
                lambda result: self.output.setPlainText(result.text + ('\n[Display truncated]' if result.truncated else '') + (f'\n{result.category}' if result.category else '')))
        except (ValueError, TypeError) as exc:
            self.output.setPlainText(str(exc)[:300])

    def select_resource(self):
        if self.profile() and self.resources.currentData():
            def selected(source):
                self.output.setPlainText(source.content[:16000])
                self.service.resource_selected.emit(source)
            self.watch(self.service.manager.read_resource(self.profile().id, self.resources.currentData()), selected)

    def select_prompt(self):
        prompt = self.prompts.currentData()
        if self.profile() is None or prompt is None:
            return
        arguments = {}
        for argument in prompt.get('arguments') or ():
            value, accepted = QInputDialog.getText(self, 'Prompt argument', argument['name'])
            if not accepted:
                return
            if value or argument.get('required'):
                arguments[argument['name']] = value
        def selected(text):
            self.output.setPlainText(text)
            self.service.prompt_selected.emit(text)
        self.watch(self.service.manager.get_prompt(self.profile().id, prompt['name'], arguments), selected)

    def select_template(self):
        profile, template = self.profile(), self.templates.currentData()
        if profile is None or template is None:
            return
        try:
            from mcp.shared.uri_template import UriTemplate
            parsed = UriTemplate.parse(template['uri_template'])
            if len(parsed.variable_names) > 16:
                raise ValueError('Template has too many parameters.')
            parameters = {}
            for name in parsed.variable_names:
                value, accepted = QInputDialog.getText(self, 'Resource template parameter', name)
                if not accepted:
                    return
                if value or name not in parsed.query_variable_names:
                    parameters[name] = value
            uri = parsed.expand(parameters)
            box = QMessageBox(self)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setWindowTitle('Read selected resource')
            box.setText(f'Server: {profile.name}\n\nRead this resource and select its text for Context?\n{uri}')
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
            def selected(source):
                self.output.setPlainText(source.content[:16000])
                self.service.resource_selected.emit(source)
            self.watch(self.service.manager.read_resource_template(profile.id, template['uri_template'], parameters), selected)
        except (ValueError, TypeError):
            self.output.setPlainText('Check the selected template and parameters.')

    def import_profiles(self):
        if any(self.service.manager.state(p.id).status not in ('disconnected', 'error', 'needs_authentication')
                for p in self.service.storage.list_profiles()):
            self.output.setPlainText('Disconnect all servers before importing.')
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Import disconnected MCP profiles', '', 'JSON (*.json)')
        if path:
            try:
                from pathlib import Path
                if Path(path).stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('Import exceeds the size limit.')
                self.service.storage.import_profiles(json.loads(Path(path).read_text(encoding='utf-8')))
                self.reload()
            except Exception:
                self.output.setPlainText('Unable to import profiles. No connection or trust was enabled.')

    def export_profiles(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export disconnected MCP profiles', '', 'JSON (*.json)')
        if path:
            try:
                from pathlib import Path
                Path(path).write_text(json.dumps(self.service.storage.export_profiles(), ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError:
                self.output.setPlainText('Unable to export profiles.')
