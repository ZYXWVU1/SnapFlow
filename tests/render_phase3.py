"""Synthetic acceptance cards; no provider requests or private screenshot data."""
import json
from pathlib import Path
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from src.skills.registry import SKILLS
from src.ui.result_window import ResultWindow

SAMPLES = {
    'assignment': {'course': 'COMPSCI 220', 'title': 'Homework 4', 'assignment_type': 'homework',
                   'due_date': '2026-09-28', 'due_time': '23:59', 'points': 100,
                   'instructions_summary': 'Implement array iteration and test boundary conditions.'},
    'event': {'title': 'AI Club Meeting', 'date': '2026-09-24', 'start_time': '15:00',
              'end_time': '16:00', 'meeting_platform': 'zoom', 'meeting_url': 'https://example.com/meeting',
              'organizer': 'AI Club', 'location': 'Zoom'},
    'code_error': {'language': 'Python', 'error_type': 'IndexError', 'error_message': 'list index out of range',
                   'evidence': ['items[i] with i equal to len(items)'], 'likely_cause': 'The index reaches the length of the list.',
                   'suggested_fixes': [{'title': 'Keep the index in range', 'before': 'while i <= len(items):',
                                        'after': 'while i < len(items):', 'explanation': 'The final valid index is length minus one.'}],
                   'confidence': 'high'},
    'table': {'title': 'Exam Scores', 'headers': ['Name', 'Score'], 'rows': [['Alice', '92'], ['Bob', '85']]},
}


def main():
    app = QApplication.instance() or QApplication([])
    canvas = QImage(1360, 1200, QImage.Format.Format_RGB32)
    painter = QPainter(canvas)
    windows = []
    for index, (kind, data) in enumerate(SAMPLES.items()):
        window = ResultWindow('smart', False)
        window.set_skill_result(SKILLS[kind].parse(json.dumps(data), .94))
        window.show()
        app.processEvents()
        painter.drawPixmap(index % 2 * 680, index // 2 * 600, window.grab())
        windows.append(window)
    painter.end()
    canvas.save('phase3-ui-check.png')
    for window in windows:
        window.close()
    print(Path('phase3-ui-check.png').resolve())


if __name__ == '__main__':
    main()
