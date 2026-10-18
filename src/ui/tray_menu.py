"""Compact tray commands and presentation of existing session state.

Refreshing this view never creates optional stores, runtimes or observers.
"""
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QMenu
from src.app_version import APP_NAME
from src.prompts import MODES


class TrayMenu:
    def __init__(self, owner):
        self.owner = owner
        self.menu = QMenu()
        self.capture_action = self.menu.addAction('Capture Screenshot', owner.capture)
        self.home_action = self.menu.addAction('Open SnapFlow', owner.open_home)
        self.menu.addSeparator()
        self.mode_menu = self.menu.addMenu('Mode')
        self.mode_group = QActionGroup(self.menu)
        self.mode_actions = {}
        for value, label in MODES.items():
            action = self.mode_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, key=value: owner.change_mode(key))
            self.mode_group.addAction(action)
            self.mode_actions[value] = action
        self.tools_menu = self.menu.addMenu('Tools')
        for label, callback in (
            ('Open Memory', owner.open_memory),
            ('Open Automations', lambda: self.open_route('automations')),
            ('Open Agent', owner.open_agent),
            ('Desktop Context…', owner.desktop_controller.show),
        ):
            self.tools_menu.addAction(label, callback)
        self.teach_action = self.tools_menu.addAction('Teach SnapFlow…', owner.automation_controller.teach)
        self.start_assistance_action = self.tools_menu.addAction(
            'Start Desktop Assistance…', owner.desktop_controller.request_start)
        self.menu.addAction('Settings…', lambda: self.open_route('settings'))
        self.session_separator = self.menu.addSeparator()
        self.controls_action = self.menu.addAction('Teaching Controls…', owner.automation_controller.teach)
        self.pause_action = self.menu.addAction('Pause Teaching', owner.automation_controller.pause_teaching)
        self.finish_action = self.menu.addAction('Finish and Review…', self.finish_teaching)
        self.stop_assistance_action = self.menu.addAction('Stop Desktop Assistance', owner.desktop_controller.stop)
        self.menu.addSeparator()
        self.menu.addAction('Quit SnapFlow', owner.quit)
        self.menu.aboutToShow.connect(self.refresh)
        window = owner.main_window
        window.teaching_controls.clicked.connect(owner.automation_controller.teach)
        window.teaching_pause.clicked.connect(owner.automation_controller.pause_teaching)
        window.teaching_finish.clicked.connect(self.finish_teaching)
        window.assistance_stop.clicked.connect(owner.desktop_controller.stop)

    def open_route(self, route):
        window = self.owner.main_window
        window.open_page(route)
        window.show()
        window.raise_()
        window.activateWindow()

    def teaching_state(self):
        dialog = self.owner.automation_controller.teach_dialog
        session = dialog.recorder.session if dialog is not None else None
        teaching = bool(session and session.status in ('recording', 'paused'))
        return dialog, teaching, bool(teaching and session.status == 'paused')

    def finish_teaching(self):
        # Check current readiness again: a checkpoint can begin after the menu opened.
        dialog, teaching, _ = self.teaching_state()
        if teaching and dialog.finish_button.isEnabled() and dialog.checkpoint_job is None:
            dialog.finish()
        self.refresh()

    def refresh(self):
        owner = self.owner
        dialog, teaching, paused = self.teaching_state()
        assistance = owner.desktop_controller.engine.active
        pause_enabled = bool(teaching and dialog.pause_button.isEnabled())
        finish_enabled = bool(teaching and dialog.finish_button.isEnabled() and dialog.checkpoint_job is None)
        for action in (self.controls_action, self.pause_action, self.finish_action):
            action.setVisible(teaching)
        self.pause_action.setText('Resume Teaching' if paused else 'Pause Teaching')
        self.pause_action.setEnabled(pause_enabled)
        self.finish_action.setEnabled(finish_enabled)
        self.stop_assistance_action.setVisible(assistance)
        self.session_separator.setVisible(teaching or assistance)
        self.teach_action.setVisible(not teaching and owner.automation_controller.enabled())
        self.start_assistance_action.setVisible(not assistance and not owner.safe_mode and not owner.quitting)
        self.mode_menu.setTitle('Mode: ' + MODES[owner.mode])
        self.mode_actions[owner.mode].setChecked(True)
        hotkey = ' + '.join(part.capitalize() for part in owner.config.hotkey.split('+'))
        self.capture_action.setText('Capture Screenshot\t' + hotkey)
        owner.main_window.set_mode(owner.mode)
        owner.main_window.mode_selector.setEnabled(not owner.workers and not owner.quitting)
        owner.main_window.capture_button.setEnabled(self.capture_action.isEnabled() and not owner.quitting)
        owner.main_window.set_session_state(teaching=teaching, paused=paused,
            pause_enabled=pause_enabled, finish_enabled=finish_enabled, assistance=assistance)
        state = [APP_NAME]
        if teaching:
            state.append('Teaching paused' if paused else 'Teaching active')
        if assistance:
            state.append('Desktop Assistance active')
        if owner.config.private_mode:
            state.append('Private Mode')
        owner.tray.setToolTip(' · '.join(state))
