"""Render Smart result states for visual inspection, without API calls."""
from pathlib import Path
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from src.modes import parse_result
from src.smart.models import ClassificationResult
from src.smart.presentation import detection_notice, placeholder_message
from src.ui.result_window import ResultWindow


def main():
    app = QApplication.instance() or QApplication([])
    canvas = QImage(2040, 600, QImage.Format.Format_RGB32)
    painter = QPainter(canvas)
    windows = []
    for index, (kind, route) in enumerate([('table', 'extract'), ('assignment', 'assignment'), ('unknown', 'ask')]):
        window = ResultWindow('smart', False)
        window.set_notice(detection_notice(ClassificationResult(kind, .96 if kind != 'unknown' else .4), route))
        if route == 'assignment':
            window.set_placeholder(placeholder_message(route))
        elif route == 'extract':
            window.set_result(parse_result(route, '{"content_type":"table","headers":["Product","Q1","Q2"],"rows":[["Pencils",120,140],["Notebooks",80,95]]}'))
        else:
            window.set_result(parse_result('ask', 'This screenshot contains a short description of a peaceful garden.'))
        window.show()
        app.processEvents()
        painter.drawPixmap(index * 680, 0, window.grab())
        windows.append(window)
    painter.end()
    canvas.save('smart-ui-check.png')
    for window in windows:
        window.close()
    print(Path('smart-ui-check.png').resolve())


if __name__ == '__main__':
    main()
