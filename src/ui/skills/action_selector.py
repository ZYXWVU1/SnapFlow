from PySide6.QtWidgets import QWidget, QHBoxLayout, QCheckBox
from src.skill_actions import ACTIONS
from src.skills.custom.models import CUSTOM_ACTIONS


class ActionSelector(QWidget):
    def __init__(self, selected=(), parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.checks = {}
        for key in CUSTOM_ACTIONS:
            if key in ACTIONS:
                check = QCheckBox(ACTIONS[key].label)
                check.setChecked(key in selected)
                self.checks[key] = check
                layout.addWidget(check)

    def selected(self):
        return [key for key, check in self.checks.items() if check.isChecked()]
