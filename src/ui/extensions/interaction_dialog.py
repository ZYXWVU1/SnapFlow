"""Explicit non-sensitive input or browser authorization; no persisted values."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
from src.ui.design.components import AppButton
from src.mcp.client.interactions import validate_response
from .schema_form import SchemaForm


class InteractionDialog(QDialog):
    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self.response = {'action': 'cancel'}
        self.setWindowTitle('MCP request · input required')
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        self.message = QLabel(f"Server: {request['connection_id']}\n"
            f"{request['operation'].title()}: {request['target']}\n\n{request['message']}")
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.form = None
        if request['mode'] == 'form':
            note = QLabel('Send only the information you choose. Do not enter passwords or credentials.')
            note.setWordWrap(True)
            layout.addWidget(note)
            self.form = SchemaForm(request['schema'])
            layout.addWidget(self.form)
        else:
            from urllib.parse import urlsplit
            endpoint = urlsplit(request['url'])
            note = QLabel(f'Open your browser for authorization:\n{endpoint.scheme}://{endpoint.netloc}{endpoint.path}')
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setWordWrap(True)
            layout.addWidget(note)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        cancel = AppButton('Cancel')
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        proceed = AppButton('Continue' if self.form else 'Open browser', variant='primary')
        proceed.clicked.connect(self.submit)
        layout.addWidget(proceed)

    def submit(self):
        try:
            response = {'action': 'accept'}
            if self.form:
                response['content'] = self.form.value()
                if validate_response(self.request, response).action != 'accept':
                    raise ValueError('Values do not match the requested fields.')
            elif not QDesktopServices.openUrl(QUrl(self.request['url'])):
                raise ValueError('Unable to open authorization browser.')
            self.response = response
            self.accept()
        except (ValueError, TypeError):
            self.error.setText('Check the requested fields and try again.')
