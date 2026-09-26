"""Navigable desktop shell around the existing capture and feature workflows."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QScrollArea,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from src.ui.design.components import AppButton, Badge, Card, PageHeader, ToastManager
from src.ui.design.icons import icon
from src.ui.design.tokens import SPACING
from src.ui.pages.skills import SkillsPage
from src.ui.pages.workflows import WorkflowsPage
from src.ui.pages.history import HistoryPage
from src.ui.pages.settings import SettingsPage
from src.ui.integrations.integration_page import IntegrationPage


PAGES = ('home', 'skills', 'workflows', 'integrations', 'history', 'settings')
_PAGE_COPY = {
    'skills': ('Visual Skills', 'Teach AI to recognize the screenshots that matter to you.', 'Open Visual Skills'),
    'workflows': ('Workflows', 'Choose what happens after a Visual Skill matches.', 'Open Visual Workflows'),
    'integrations': ('Integrations', 'Connect your tools and send structured data where it belongs.', None),
    'history': ('History', 'Review recent workflow outcomes.', 'Open Workflow History'),
    'settings': ('Settings', 'Control capture, AI, and appearance preferences.', 'Open Settings'),
}


class MainWindow(QMainWindow):
    capture_requested = Signal()
    feature_requested = Signal(str)
    integration_connect_requested = Signal(str)
    integration_test_requested = Signal(str)
    integration_disconnect_requested = Signal(str)
    skill_edit_requested = Signal(str)
    skill_test_requested = Signal(str)
    workflow_edit_requested = Signal(str)
    workflow_test_requested = Signal(str)

    def __init__(self, hotkey, parent=None, *, skills_storage=None, workflows_storage=None,
                 history=None, config=None, integration_registry=None, connection_storage=None):
        super().__init__(parent)
        self.setObjectName('AppShell')
        self.setWindowTitle('Visual Workflow AI')
        self.setMinimumSize(620, 480)
        self.resize(1020, 700)
        self.current_page = 'home'
        self.skills_storage, self.workflows_storage = skills_storage, workflows_storage
        self.history, self.config = history, config
        self.connection_storage = connection_storage
        root = QWidget()
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setProperty('role', 'sidebar')
        self.sidebar.setFixedWidth(212)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(SPACING['md'], SPACING['xl'], SPACING['md'], SPACING['lg'])
        side.setSpacing(SPACING['sm'])
        self.brand = QLabel('Visual Workflow AI')
        brand_font = self.brand.font()
        brand_font.setPointSize(13)
        brand_font.setBold(True)
        self.brand.setFont(brand_font)
        side.addWidget(self.brand)
        side.addSpacing(SPACING['xl'])
        self.nav_buttons = {}
        for page in PAGES:
            name = page.title()
            button = QToolButton()
            button.setIcon(icon(page))
            button.setText(name)
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, key=page: self.open_page(key))
            self.nav_buttons[page] = button
            side.addWidget(button)
            if page == 'history':
                side.addStretch(1)
        row.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.pages = {}
        self.launch_buttons = {}
        for page in PAGES:
            if page == 'home':
                widget = self._build_home(hotkey)
            elif page == 'skills':
                widget = SkillsPage(skills_storage)
                widget.manage_requested.connect(lambda: self.feature_requested.emit('skills'))
                widget.edit_requested.connect(self.skill_edit_requested)
                widget.test_requested.connect(self.skill_test_requested)
                self.launch_buttons[page] = widget.manage_button
            elif page == 'workflows':
                widget = WorkflowsPage(workflows_storage)
                widget.manage_requested.connect(lambda: self.feature_requested.emit('workflows'))
                widget.edit_requested.connect(self.workflow_edit_requested)
                widget.test_requested.connect(self.workflow_test_requested)
                self.launch_buttons[page] = widget.manage_button
            elif page == 'history':
                widget = HistoryPage(history)
            elif page == 'integrations' and integration_registry is not None:
                widget = IntegrationPage(integration_registry, connection_storage)
                widget.connection_requested.connect(self.integration_connect_requested)
                widget.test_requested.connect(self.integration_test_requested)
                widget.disconnect_requested.connect(self.integration_disconnect_requested)
            elif page == 'settings':
                widget = SettingsPage(config)
                widget.edit_requested.connect(lambda: self.feature_requested.emit('settings'))
                self.launch_buttons[page] = widget.edit_button
            else:
                widget = self._build_feature_page(page)
            self.pages[page] = widget
            self.stack.addWidget(widget)
        row.addWidget(self.stack, 1)
        self.toast = ToastManager(root)
        self.open_page('home')
        self._update_navigation()

    def _scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(SPACING['2xl'], SPACING['2xl'], SPACING['2xl'], SPACING['2xl'])
        layout.setSpacing(SPACING['xl'])
        scroll.setWidget(body)
        return scroll, layout

    def _build_home(self, hotkey):
        page, layout = self._scroll_page()
        layout.addWidget(PageHeader('Home', 'Turn what you see into useful workflows.'))
        capture = Card()
        title = QLabel('Capture anything on your screen')
        title_font = title.font()
        title_font.setPointSize(15)
        title_font.setBold(True)
        title.setFont(title_font)
        capture.content.addWidget(title)
        capture.content.addWidget(QLabel('Select an area. Smart Mode can identify a Visual Skill and suggest the next step.'))
        self.hotkey_label = QLabel(' + '.join(part.capitalize() for part in hotkey.split('+')))
        self.hotkey_label.setProperty('role', 'muted')
        capture.content.addWidget(self.hotkey_label)
        self.capture_button = AppButton('Capture Screenshot', variant='primary')
        self.capture_button.setIcon(icon('capture'))
        self.capture_button.clicked.connect(self.capture_requested)
        capture.content.addWidget(self.capture_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(capture)
        status = Card()
        status.content.addWidget(QLabel('Smart Mode'))
        status.content.addWidget(Badge('Ready', status='success'), alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(status)
        recent = Card()
        recent.content.addWidget(QLabel('Recent Workflows'))
        self.recent_workflows = QLabel()
        self.recent_workflows.setWordWrap(True)
        self.recent_workflows.setProperty('role', 'muted')
        recent.content.addWidget(self.recent_workflows)
        layout.addWidget(recent)
        integrations = Card()
        integrations.content.addWidget(QLabel('Integrations'))
        self.integration_summary = QLabel()
        self.integration_summary.setWordWrap(True)
        self.integration_summary.setProperty('role', 'muted')
        integrations.content.addWidget(self.integration_summary)
        layout.addWidget(integrations)
        layout.addStretch(1)
        self._refresh_home()
        return page

    def _refresh_home(self):
        entries = self.history.list_entries()[-3:] if self.history else []
        self.recent_workflows.setText('\n'.join(
            f"{entry['workflow_name']} · {entry['status'].replace('_', ' ').title()}"
            for entry in reversed(entries)) if entries else 'No workflow runs yet.')
        google = self.connection_storage.get('google') if self.connection_storage else None
        todoist = self.connection_storage.get('todoist') if self.connection_storage else None
        lines = []
        for name, capability in (('Google Calendar', 'google_calendar'), ('Google Sheets', 'google_sheets')):
            active = google and google.status == 'connected' and capability in google.granted_capabilities
            lines.append(f"{name}: {'Connected' if active else 'Not connected'}")
        lines.append(f"Todoist: {'Connected' if todoist and todoist.status == 'connected' else 'Not connected'}")
        self.integration_summary.setText('\n'.join(lines))

    def _build_feature_page(self, key):
        title, subtitle, launch_label = _PAGE_COPY[key]
        page, layout = self._scroll_page()
        layout.addWidget(PageHeader(title, subtitle))
        card = Card()
        card.content.addWidget(QLabel(subtitle))
        if launch_label:
            button = AppButton(launch_label, variant='secondary')
            button.clicked.connect(lambda checked=False, target=key: self.feature_requested.emit(target))
            card.content.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
            self.launch_buttons[key] = button
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def open_page(self, page):
        if page not in self.pages:
            raise ValueError('Unknown page.')
        self.current_page = page
        widget = self.pages[page]
        if page == 'home':
            self._refresh_home()
        elif page == 'settings':
            widget.refresh(self.config)
        elif page in ('skills', 'workflows', 'history', 'integrations') and hasattr(widget, 'refresh'):
            widget.refresh()
        self.stack.setCurrentWidget(self.pages[page])
        for key, button in self.nav_buttons.items():
            button.setChecked(key == page)

    def _update_navigation(self):
        collapsed = self.width() < 760
        self.sidebar.setFixedWidth(64 if collapsed else 212)
        self.brand.setVisible(not collapsed)
        for key, button in self.nav_buttons.items():
            button.setText('' if collapsed else key.title())
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if collapsed else
                                      Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_navigation()
