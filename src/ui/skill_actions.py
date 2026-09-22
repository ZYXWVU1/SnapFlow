"""Qt boundary for user-triggered local actions."""
from pathlib import Path
from PySide6.QtWidgets import QApplication, QFileDialog
from src.skill_actions import execute_action


def run_action(window, action_id, result):
    action = execute_action(action_id, result)
    if not action.success:
        window.action_status.setText(action.message)
        return
    if action.kind == 'ask':
        window.ask_ai.emit(action.payload)
    elif action.kind == 'copy':
        QApplication.clipboard().setText(action.payload)
        window.action_status.setText('Copied to clipboard')
    elif action.kind == 'save_ics':
        path, _ = QFileDialog.getSaveFileName(window, 'Create Calendar File', result.skill_id + '.ics', 'Calendar files (*.ics)')
        if not path:
            return
        target = Path(path)
        try:
            target.write_bytes(action.payload.encode('utf-8'))
        except OSError:
            window.action_status.setText('Unable to save calendar file. Choose a writable location.')
        else:
            window.action_status.setText('Calendar file created')
