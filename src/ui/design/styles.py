"""Generate one application stylesheet from semantic tokens."""
from .tokens import CONTROL_HEIGHT, RADIUS, Palette


def stylesheet(colors: Palette) -> str:
    return f"""
    QWidget {{ background: {colors.background}; color: {colors.text_primary}; }}
    QMainWindow, QDialog {{ background: {colors.background}; }}
    QLabel {{ background: transparent; }}
    QLabel[role="muted"] {{ color: {colors.text_secondary}; }}
    QLabel[role="badge"] {{ background: {colors.surface_alt}; border: 1px solid {colors.border};
                             border-radius: {RADIUS['control']}px; padding: 3px 8px; }}
    QLabel[status="success"] {{ color: {colors.success}; }}
    QLabel[status="warning"] {{ color: {colors.warning}; }}
    QLabel[status="error"] {{ color: {colors.error}; }}
    QLabel[role="toast"] {{ background: {colors.surface}; border: 1px solid {colors.border};
                             border-radius: {RADIUS['card']}px; padding: 10px 14px; }}
    QFrame[role="card"] {{ background: {colors.surface}; border: 1px solid {colors.border};
                            border-radius: {RADIUS['card']}px; }}
    QFrame[role="sidebar"] {{ background: {colors.surface}; border-right: 1px solid {colors.border}; }}
    QPushButton, QToolButton {{ min-height: {CONTROL_HEIGHT}px; border-radius: {RADIUS['control']}px;
                               border: 1px solid {colors.border}; background: {colors.surface};
                               padding: 0 12px; color: {colors.text_primary}; }}
    QPushButton:hover, QToolButton:hover {{ background: {colors.surface_alt}; }}
    QPushButton[variant="primary"] {{ background: {colors.accent}; color: {colors.accent_text};
                                      border-color: {colors.accent}; font-weight: 600; }}
    QPushButton[variant="primary"]:hover {{ background: {colors.accent_hover}; }}
    QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
    QPushButton[variant="danger"] {{ color: {colors.error}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {colors.text_muted}; }}
    QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus {{ border: 2px solid {colors.focus}; }}
    QLineEdit, QTextEdit, QComboBox, QSpinBox, QListWidget, QTableWidget {{
        background: {colors.surface}; color: {colors.text_primary}; border: 1px solid {colors.border};
        border-radius: {RADIUS['control']}px; padding: 5px 8px; selection-background-color: {colors.accent};
    }}
    QScrollArea {{ border: 0; background: transparent; }}
    """
