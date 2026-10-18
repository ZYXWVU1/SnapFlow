"""Navigable desktop shell around the existing capture and feature workflows."""
from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QScrollArea,
    QSizePolicy, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget)

from src.app_version import APP_NAME
from src.ui.design.components import ActionRow, AppButton, Card, PageHeader, ToastManager
from src.ui.design.icons import icon
from src.ui.design.tokens import SPACING
from src.prompts import MODES
from src.ui.navigation_drawer import NavigationScrim
from src.ui.navigation import NAVIGATION, AUTOMATION_TABS, CONNECTION_TABS, AUTOMATION_ROUTES, parent_route


PAGES = ('home', 'agent', 'memory', 'skills', 'workflows', 'integrations', 'extensions', 'evaluation', 'history', 'settings', 'tools', 'desktop_skills')
_PAGE_COPY = {
    'memory': ('Visual Memory', 'Search information you explicitly saved.', None),
    'skills': ('Visual Skills', 'Teach AI to recognize the screenshots that matter to you.', 'Open Visual Skills'),
    'workflows': ('Workflows', 'Choose what happens after a Visual Skill matches.', 'Open Visual Workflows'),
    'integrations': ('Integrations', 'Connect your tools and send structured data where it belongs.', None),
    'extensions': ('Extensions', 'Connect MCP servers and review external capabilities.', None),
    'evaluation': ('Evaluation', 'Measure Visual Skills using verified examples.', None),
    'history': ('History', 'Review recent workflow outcomes.', 'Open Workflow History'),
    'settings': ('Settings', 'Control capture, AI, and appearance preferences.', 'Open Settings'),
}


class _PageMap(Mapping):
    """Known keys stay discoverable while indexed access creates a page once."""
    def __init__(self, window):
        self.window = window

    def __getitem__(self, key):
        if key not in PAGES:
            raise KeyError(key)
        return self.window._ensure_page(key)

    def __iter__(self):
        return iter(PAGES)

    def __len__(self):
        return len(PAGES)

    def __contains__(self, key):
        return key in PAGES


class MainWindow(QMainWindow):
    page_created = Signal(str, object)
    capture_requested = Signal()
    feature_requested = Signal(str)
    integration_connect_requested = Signal(str)
    integration_test_requested = Signal(str)
    integration_disconnect_requested = Signal(str)
    skill_edit_requested = Signal(str)
    skill_test_requested = Signal(str)
    workflow_edit_requested = Signal(str)
    workflow_test_requested = Signal(str)
    backup_export_requested = Signal()
    backup_restore_requested = Signal()
    open_data_folder_requested = Signal()
    storage_usage_requested = Signal()
    support_bundle_requested = Signal()
    health_check_requested = Signal()
    usage_summary_requested = Signal()
    update_check_requested = Signal()
    onboarding_requested = Signal()
    memory_open_requested = Signal(str)
    agent_from_memory_requested = Signal(str)
    memory_ask_requested = Signal(str, object)
    memory_rebuild_requested = Signal()
    ai_runtime_requested = Signal()
    mode_requested = Signal(str)

    def __init__(self, hotkey, parent=None, *, skills_storage=None, workflows_storage=None,
                 history=None, config=None, integration_registry=None, connection_storage=None, paths=None,
                 memory_store=None, extension_service=None, page_configurator=None):
        super().__init__(parent)
        import time
        self._constructed_at = time.perf_counter()
        self.setObjectName('AppShell')
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(620, 480)
        self.resize(1020, 700)
        self.current_page = 'home'
        self.skills_storage, self.workflows_storage = skills_storage, workflows_storage
        self.history, self.config = history, config
        self.paths = paths
        self.connection_storage = connection_storage
        self.memory_store = memory_store
        self.integration_registry = integration_registry
        self.extension_service = extension_service
        self._page_configurator = page_configurator
        self._created_pages = {}
        self._private = bool(config and config.private_mode)
        self._hotkey = hotkey
        self._automation_route = 'skills'
        self._ai_error = False
        self._ai_configured = False
        self._navigation_open = False
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
        self.brand = QLabel(APP_NAME)
        brand_font = self.brand.font()
        brand_font.setPointSize(13)
        brand_font.setBold(True)
        self.brand.setFont(brand_font)
        side.addWidget(self.brand)
        side.addSpacing(SPACING['lg'])
        self.nav_buttons = {}
        for page, name, icon_name in NAVIGATION:
            if page == 'settings':
                side.addStretch(1)
            button = QToolButton()
            button.setIcon(icon(icon_name))
            button.setProperty('theme_icon', icon_name)
            button.setIconSize(QSize(20, 20))
            button.setText(name)
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setProperty('role', 'navigation')
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, key=page: self.open_page(key))
            self.nav_buttons[page] = button
            side.addWidget(button)
        row.addWidget(self.sidebar)

        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        self.header = QWidget()
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(24, 12, 24, 8)
        self.menu_toggle = AppButton('Menu', variant='ghost')
        self.menu_toggle.setCheckable(True)
        self.menu_toggle.setAccessibleName('Open navigation menu')
        self.menu_toggle.clicked.connect(self._toggle_navigation)
        header_row.addWidget(self.menu_toggle)
        self.back_to_tools = AppButton('‹ Tools', variant='ghost')
        self.back_to_tools.clicked.connect(lambda: self.open_page('tools'))
        header_row.addWidget(self.back_to_tools)
        header_row.addStretch(1)
        self.privacy_indicator = AppButton('AI & Models', variant='ghost')
        self.privacy_indicator.clicked.connect(self.ai_runtime_requested)
        header_row.addWidget(self.privacy_indicator)
        self.content_layout.addWidget(self.header)

        self.session_strip = QFrame()
        self.session_strip.setProperty('role', 'session')
        self.session_layout = QVBoxLayout(self.session_strip)
        self.session_layout.setContentsMargins(24, 8, 24, 8)
        self.session_layout.setSpacing(8)
        self.teaching_row = QWidget()
        self.teaching_row.setProperty('role', 'session_group')
        teaching_layout = QHBoxLayout(self.teaching_row)
        teaching_layout.setContentsMargins(0, 0, 0, 0)
        self.teaching_controls = AppButton('Teaching controls…')
        self.teaching_pause = AppButton('Pause Teaching')
        self.teaching_finish = AppButton('Finish and Review…')
        for button in (self.teaching_controls, self.teaching_pause, self.teaching_finish):
            teaching_layout.addWidget(button)
        self.session_layout.addWidget(self.teaching_row)
        self.assistance_row = QWidget()
        self.assistance_row.setProperty('role', 'session_group')
        assistance_layout = QHBoxLayout(self.assistance_row)
        assistance_layout.setContentsMargins(0, 0, 0, 0)
        self.assistance_indicator = AppButton('Desktop Assistance · Active')
        self.assistance_stop = AppButton('Stop Assistance')
        assistance_layout.addWidget(self.assistance_indicator)
        assistance_layout.addWidget(self.assistance_stop)
        assistance_layout.addStretch(1)
        self.session_layout.addWidget(self.assistance_row)
        self.session_strip.hide()
        self.content_layout.addWidget(self.session_strip)
        self.section_tabs = QTabBar()
        self.section_tabs.setExpanding(False)
        self.section_tabs.setUsesScrollButtons(True)
        self.section_tabs.setAccessibleName('Section navigation')
        self.section_tabs.currentChanged.connect(self._section_changed)
        self.content_layout.addWidget(self.section_tabs)
        self.stack = QStackedWidget()
        self.pages = _PageMap(self)
        self.launch_buttons = {}
        self.content_layout.addWidget(self.stack, 1)
        row.addWidget(content, 1)
        self.escape_navigation = QShortcut(QKeySequence('Escape'), self)
        self.escape_navigation.activated.connect(self._close_navigation)
        self.navigation_scrim = NavigationScrim(root)
        self.navigation_scrim.dismissed.connect(self._close_navigation)
        self.toast = ToastManager(root)
        self.open_page('home')
        self._update_navigation()
        self.set_privacy_state(bool(config and config.private_mode))

    def get_created_page(self, key):
        """Inspect lifecycle state without activating an optional feature."""
        return self._created_pages.get(key)

    def refresh_created_page(self, key):
        page = self.get_created_page(key)
        if page is not None and hasattr(page, 'refresh'):
            page.refresh()

    def _ensure_page(self, page):
        existing = self.get_created_page(page)
        if existing is not None:
            return existing
        widget = self._build_page(page)
        try:
            if page == 'agent':
                widget.set_privacy_state(self._private)
            if self._page_configurator is not None:
                self._page_configurator(page, widget)
        except Exception:
            self.launch_buttons.pop(page, None)
            widget.deleteLater()
            raise
        self._created_pages[page] = widget
        self.stack.addWidget(widget)
        self.page_created.emit(page, widget)
        return widget

    def _build_page(self, page):
        if page == 'home':
            widget = self._build_home(self._hotkey)
        elif page == 'tools':
            widget = self._build_tools()
        elif page == 'desktop_skills':
            widget = self._build_desktop_skills()
        elif page == 'agent':
            from src.ui.agent.page import AgentPage
            widget = AgentPage(self.memory_store)
        elif page == 'memory' and self.memory_store is not None:
            from src.ui.pages.memory import MemoryPage
            widget = MemoryPage(self.memory_store)
            widget.memory_open_requested.connect(self.memory_open_requested)
            widget.agent_requested.connect(self.agent_from_memory_requested)
            widget.ask_requested.connect(self.memory_ask_requested)
            widget.capture_requested.connect(self.capture_requested)
        elif page == 'skills':
            from src.ui.pages.skills import SkillsPage
            widget = SkillsPage(self.skills_storage)
            widget.manage_requested.connect(lambda: self.feature_requested.emit('skills'))
            widget.edit_requested.connect(self.skill_edit_requested)
            widget.test_requested.connect(self.skill_test_requested)
            self.launch_buttons[page] = widget.manage_button
        elif page == 'workflows':
            from src.ui.pages.workflows import WorkflowsPage
            widget = WorkflowsPage(self.workflows_storage)
            widget.manage_requested.connect(lambda: self.feature_requested.emit('workflows'))
            widget.edit_requested.connect(self.workflow_edit_requested)
            widget.test_requested.connect(self.workflow_test_requested)
            self.launch_buttons[page] = widget.manage_button
        elif page == 'history':
            from src.ui.pages.history import HistoryPage
            widget = HistoryPage(self.history)
        elif page == 'evaluation':
            from src.ui.evaluation.page import EvaluationPage
            widget = EvaluationPage(self.skills_storage, self.workflows_storage)
            widget.open_skills_requested.connect(lambda: self.feature_requested.emit('skills'))
        elif page == 'integrations' and self.integration_registry is not None:
            from src.ui.integrations.integration_page import IntegrationPage
            widget = IntegrationPage(self.integration_registry, self.connection_storage)
            widget.connection_requested.connect(self.integration_connect_requested)
            widget.test_requested.connect(self.integration_test_requested)
            widget.disconnect_requested.connect(self.integration_disconnect_requested)
        elif page == 'settings':
            from src.ui.pages.settings import SettingsPage
            widget = SettingsPage(self.config, paths=self.paths)
            widget.edit_requested.connect(lambda: self.feature_requested.emit('settings'))
            widget.backup_export_requested.connect(self.backup_export_requested)
            widget.backup_restore_requested.connect(self.backup_restore_requested)
            widget.open_data_folder_requested.connect(self.open_data_folder_requested)
            widget.storage_usage_requested.connect(self.storage_usage_requested)
            widget.support_bundle_requested.connect(self.support_bundle_requested)
            widget.health_check_requested.connect(self.health_check_requested)
            widget.usage_summary_requested.connect(self.usage_summary_requested)
            widget.update_check_requested.connect(self.update_check_requested)
            widget.onboarding_requested.connect(self.onboarding_requested)
            widget.memory_rebuild_requested.connect(self.memory_rebuild_requested)
            widget.ai_runtime_requested.connect(self.ai_runtime_requested)
            self.launch_buttons[page] = widget.edit_button
        elif page == 'extensions' and self.extension_service is not None:
            from src.ui.extensions.extensions_page import ExtensionsPage
            widget = ExtensionsPage(self.extension_service)
        else:
            widget = self._build_feature_page(page)
        return widget

    def set_privacy_state(self, private):
        self._private = bool(private)
        agent = self.get_created_page('agent')
        if agent is not None:
            agent.set_privacy_state(self._private)
        self.set_ai_status(self._ai_configured, error=self._ai_error)

    def set_ai_status(self, configured, *, error=False):
        self._ai_configured, self._ai_error = bool(configured), bool(error)
        local = self._private or bool(self.config and self.config.ai_execution_mode == 'local_only')
        label = 'Private Mode' if self._private else 'Local AI' if local else 'AI & Models'
        if error:
            label += ' · Review error'
        elif not configured:
            label += ' · Setup needed'
        self.privacy_indicator.setText(label)
        self.privacy_indicator.setAccessibleName(label)
        self.privacy_indicator.setToolTip(
            'Private Mode blocks application-owned external requests. Review AI and local model settings.'
            if self._private else 'Configure AI execution and privacy. A configured model has not necessarily been tested.')
        if hasattr(self, 'setup_note'):
            self.setup_note.setText('The last analysis failed. Review the result or check AI settings.' if error else
                'Configure a local vision model to analyze screenshots.' if local else
                'Choose an AI provider or local vision model to analyze screenshots.')
            self.setup_section.setVisible(error or not configured)

    def set_mode(self, mode):
        self.mode_selector.blockSignals(True)
        self.mode_selector.setCurrentIndex(self.mode_selector.findData(mode))
        self.mode_selector.blockSignals(False)

    def set_session_state(self, *, teaching=False, paused=False, pause_enabled=False,
                          finish_enabled=False, assistance=False):
        self.teaching_controls.setText('Teaching paused · Controls…' if paused else 'Teaching active · Controls…')
        self.teaching_pause.setText('Resume Teaching' if paused else 'Pause Teaching')
        self.teaching_controls.setAccessibleName(self.teaching_controls.text())
        self.teaching_pause.setAccessibleName(self.teaching_pause.text())
        self.teaching_pause.setEnabled(pause_enabled)
        self.teaching_finish.setEnabled(finish_enabled)
        self.teaching_row.setVisible(teaching)
        self.assistance_row.setVisible(assistance)
        self.assistance_indicator.setText('Desktop Assistance · Active')
        self.session_strip.setVisible(teaching or assistance)

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
        layout.addWidget(PageHeader('Home', 'Capture a moment. Make sense of it.'))
        capture = Card()
        title = QLabel('Capture your screen')
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        capture.content.addWidget(title)
        description = QLabel('Select an area, then ask a question or choose an analysis mode.')
        description.setWordWrap(True)
        description.setProperty('role', 'muted')
        capture.content.addWidget(description)
        self.capture_controls = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.capture_button = AppButton('Capture Screenshot', variant='primary')
        self.capture_button.setIcon(icon('capture', primary=True))
        self.capture_button.setProperty('theme_icon', 'capture')
        self.capture_button.clicked.connect(self.capture_requested)
        self.capture_controls.addWidget(self.capture_button)
        self.hotkey_label = QLabel(' + '.join(part.capitalize() for part in hotkey.split('+')))
        self.hotkey_label.setProperty('role', 'muted')
        self.capture_controls.addWidget(self.hotkey_label)
        self.capture_controls.addStretch(1)
        capture.content.addLayout(self.capture_controls)
        mode_row = QHBoxLayout()
        mode_label = QLabel('Analysis mode')
        self.mode_selector = QComboBox()
        self.mode_selector.setAccessibleName('Screenshot analysis mode')
        for value, label in MODES.items():
            self.mode_selector.addItem(label, value)
        mode_label.setBuddy(self.mode_selector)
        self.set_mode(self.config.default_mode if self.config else 'smart')
        self.mode_selector.currentIndexChanged.connect(
            lambda _: self.mode_requested.emit(self.mode_selector.currentData()))
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.mode_selector)
        mode_row.addStretch(1)
        capture.content.addLayout(mode_row)
        layout.addWidget(capture)
        self.setup_section = QWidget()
        setup_layout = QVBoxLayout(self.setup_section)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_note = QLabel()
        self.setup_note.setWordWrap(True)
        self.setup_note.setProperty('role', 'muted')
        setup_layout.addWidget(self.setup_note)
        configure = AppButton('Configure AI', variant='ghost')
        configure.clicked.connect(self.ai_runtime_requested)
        setup_layout.addWidget(configure, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.setup_section)
        self.recent_section = QWidget()
        recent_layout = QVBoxLayout(self.recent_section)
        recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_header = QHBoxLayout()
        recent_header.addWidget(QLabel('Recent workflow runs'), 1)
        view_all = AppButton('View all', variant='ghost')
        view_all.clicked.connect(lambda: self.open_page('history'))
        recent_header.addWidget(view_all)
        recent_layout.addLayout(recent_header)
        self.recent_workflows = QLabel()
        self.recent_workflows.setTextFormat(Qt.TextFormat.PlainText)
        self.recent_workflows.setWordWrap(True)
        self.recent_workflows.setProperty('role', 'muted')
        recent_layout.addWidget(self.recent_workflows)
        layout.addWidget(self.recent_section)
        layout.addStretch(1)
        self._refresh_home()
        return page

    def _refresh_home(self):
        entries = self.history.list_entries()[-3:] if self.history else []
        self.recent_workflows.setText('\n'.join(
            f"{entry['workflow_name']} · {entry['status'].replace('_', ' ').title()}"
            for entry in reversed(entries)))
        self.recent_section.setVisible(bool(entries))

    def _build_tools(self):
        page, layout = self._scroll_page()
        layout.addWidget(PageHeader('Tools', 'Choose what you want to work on.'))
        self.tool_commands = {}
        for key, title, description, icon_name, route, feature in (
            ('agent', 'Agent', 'Plan a task, review the steps, then choose what to run.', 'agent', 'agent', None),
            ('desktop_context', 'Desktop Context', 'Review an application or start an assistance session.', 'tools', None, 'desktop_context'),
            ('connections', 'Connections', 'Manage connected services and extensions.', 'integrations', 'integrations', None),
        ):
            button = ActionRow(title, description, icon_name)
            if route:
                button.clicked.connect(lambda checked=False, target=route: self.open_page(target))
            else:
                button.clicked.connect(lambda checked=False, target=feature: self.feature_requested.emit(target))
            self.tool_commands[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        return page

    def _build_desktop_skills(self):
        page, layout = self._scroll_page()
        layout.addWidget(PageHeader('Desktop Skills', 'Beta · Teach a task, then review every step.'))
        note = QLabel('Enable Demonstration Automation in Settings → Help & Diagnostics → Beta Features. '
                      'Visual Skills recognize screenshot content; Desktop Skills perform reviewed application actions.')
        note.setWordWrap(True)
        note.setProperty('role', 'muted')
        layout.addWidget(note)
        for label, detail, icon_name, feature in (
            ('Open Desktop Skills', 'Browse saved tasks, review steps and run a published version.', 'skills', 'desktop_skills'),
            ('Teach a task', 'Choose an application and demonstrate the task explicitly.', 'workflows', 'teach'),
        ):
            button = ActionRow(label, detail, icon_name)
            button.clicked.connect(lambda checked=False, target=feature: self.feature_requested.emit(target))
            layout.addWidget(button)
        layout.addStretch(1)
        return page

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
        if page == 'automations':
            page = self._automation_route
        elif page == 'connections':
            page = 'integrations'
        if page not in self.pages:
            raise ValueError('Unknown page.')
        widget = self.pages[page]
        self.current_page = page
        from src.observability.performance import emit
        emit('feature_visited', component='ui', properties={'feature': page})
        if page == 'home':
            self._refresh_home()
        elif page == 'settings':
            widget.refresh(self.config)
        elif page in ('memory', 'skills', 'workflows', 'evaluation', 'history', 'integrations') and hasattr(widget, 'refresh'):
            widget.refresh()
        self.stack.setCurrentWidget(widget)
        if page in AUTOMATION_ROUTES:
            self._automation_route = page
        for key, button in self.nav_buttons.items():
            button.setChecked(key == parent_route(page))
        self._update_section_tabs(page)
        self._close_navigation()

    def _update_section_tabs(self, page):
        tabs = AUTOMATION_TABS if page in AUTOMATION_ROUTES else (
            CONNECTION_TABS if page in ('integrations', 'extensions') else ())
        self.section_tabs.blockSignals(True)
        while self.section_tabs.count():
            self.section_tabs.removeTab(0)
        for key, label in tabs:
            index = self.section_tabs.addTab(label)
            self.section_tabs.setTabData(index, key)
            if key == page:
                self.section_tabs.setCurrentIndex(index)
        self.section_tabs.blockSignals(False)
        self.section_tabs.setVisible(bool(tabs))
        self.back_to_tools.setVisible(page in ('agent', 'integrations', 'extensions'))

    def _section_changed(self, index):
        if index >= 0:
            self.open_page(self.section_tabs.tabData(index))

    def _toggle_navigation(self):
        self._navigation_open = self.menu_toggle.isChecked()
        self._update_navigation()
        if self._navigation_open:
            self.nav_buttons[parent_route(self.current_page)].setFocus()

    def _close_navigation(self):
        was_open = self._navigation_open
        self._navigation_open = False
        self.menu_toggle.setChecked(False)
        self._update_navigation()
        if was_open and self.width() < 760:
            self.menu_toggle.setFocus()

    def _update_navigation(self):
        compact = self.width() < 760
        if not compact:
            self._navigation_open = False
            self.menu_toggle.setChecked(False)
        self.menu_toggle.setVisible(compact)
        self.menu_toggle.setAccessibleName('Close navigation menu' if self._navigation_open else 'Open navigation menu')
        self.sidebar.setFixedWidth(196)
        root = self.centralWidget()
        if compact and self._navigation_open:
            root.layout().removeWidget(self.sidebar)
            if self.sidebar.parentWidget() is not self.navigation_scrim:
                self.sidebar.setParent(self.navigation_scrim)
            top = self.header.height()
            self.navigation_scrim.setGeometry(0, top, root.width(), max(0, root.height() - top))
            self.sidebar.setGeometry(0, 0, 196, self.navigation_scrim.height())
            self.navigation_scrim.show()
            self.sidebar.show()
            self.navigation_scrim.raise_()
        else:
            self.navigation_scrim.hide()
            if compact:
                self.sidebar.hide()
            else:
                if self.sidebar.parentWidget() is not root:
                    self.sidebar.setParent(root)
                if root.layout().indexOf(self.sidebar) < 0:
                    root.layout().insertWidget(0, self.sidebar)
                self.sidebar.show()
        if hasattr(self, 'capture_controls'):
            self.capture_controls.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)

    def focusNextPrevChild(self, next):
        if self._navigation_open and self.width() < 760:
            controls = [self.menu_toggle, *self.nav_buttons.values()]
            controls = [button for button in controls if button.isVisible() and button.isEnabled()]
            current = self.focusWidget()
            index = controls.index(current) if current in controls else -1
            controls[(index + (1 if next else -1)) % len(controls)].setFocus(
                Qt.FocusReason.TabFocusReason if next else Qt.FocusReason.BacktabFocusReason)
            return True
        return super().focusNextPrevChild(next)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_navigation()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_visibility_recorded', False):
            self._visibility_recorded = True
            from src.observability.performance import emit
            import time
            emit('operation_completed', component='ui', success=True,
                 duration_ms=(time.perf_counter() - self._constructed_at) * 1000,
                 properties={'operation': 'window_visible'})
