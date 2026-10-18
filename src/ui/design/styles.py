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
    QPushButton[role="command"] {{ background: {colors.surface}; border: 1px solid {colors.border};
        border-radius: {RADIUS['card']}px; padding: 0; }}
    QPushButton[role="command"]:hover {{ background: {colors.surface_alt}; border-color: {colors.text_muted}; }}
    QPushButton[role="command"]:focus {{ border: 2px solid {colors.focus}; }}
    QWidget[role="command_content"] {{ background: transparent; }}
    QFrame[role="navigation_scrim"] {{ background: rgba(0, 0, 0, 65); }}
    QTextEdit[role="answer"] {{ border: 0; background: transparent; padding: 0; }}
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
    QToolButton[role="navigation"] {{ border: 1px solid transparent; background: transparent;
        text-align: left; min-height: 42px; padding: 0 12px; }}
    QToolButton[role="navigation"]:hover {{ background: {colors.surface_alt}; }}
    QToolButton[role="navigation"]:checked {{ background: {colors.surface_alt};
        color: {colors.accent}; font-weight: 600; border-left: 3px solid {colors.accent}; }}
    QToolButton[role="navigation"]:focus {{ border: 2px solid {colors.focus}; }}
    QWidget[role="session_group"] {{ background: transparent; }}
    QFrame[role="session"] {{ background: {colors.surface_alt}; border-bottom: 1px solid {colors.border}; }}
    QTabBar::tab {{ background: transparent; color: {colors.text_secondary};
        padding: 10px 14px; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {colors.accent}; border-bottom: 2px solid {colors.accent}; }}
    QTabBar::tab:hover {{ background: {colors.surface_alt}; }}
    QTabBar::tab:focus {{ border: 2px solid {colors.focus}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {colors.text_muted}; }}
    QPushButton[variant="primary"]:disabled {{ background: {colors.surface_alt};
        color: {colors.text_muted}; border-color: {colors.border}; }}
    QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus {{ border: 2px solid {colors.focus}; }}
    QLineEdit, QTextEdit, QComboBox, QSpinBox, QListWidget, QTableWidget {{
        background: {colors.surface}; color: {colors.text_primary}; border: 1px solid {colors.border};
        border-radius: {RADIUS['control']}px; padding: 5px 8px; selection-background-color: {colors.accent};
    }}
    QScrollArea {{ border: 0; background: transparent; }}
    QScrollBar:vertical {{ background: {colors.surface_alt}; width: 16px; margin: 0; border-radius: 8px; }}
    QScrollBar::handle:vertical {{ background: {colors.text_muted}; min-height: 40px;
                                   margin: 2px; border-radius: 6px; }}
    QScrollBar::handle:vertical:hover {{ background: {colors.accent}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: {colors.surface_alt}; height: 16px; margin: 0; border-radius: 8px; }}
    QScrollBar::handle:horizontal {{ background: {colors.text_muted}; min-width: 40px;
                                     margin: 2px; border-radius: 6px; }}
    QScrollBar::handle:horizontal:hover {{ background: {colors.accent}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    """
