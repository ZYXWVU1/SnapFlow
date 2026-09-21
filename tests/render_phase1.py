"""Generate synthetic UI snapshots for manual layout inspection; no API calls."""
import json
from pathlib import Path
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from src.modes import parse_result
from src.ui.result_window import ResultWindow


def main():
    app = QApplication.instance() or QApplication([])
    examples = [
        ('ask', 'This loop accesses an index beyond the end of the array. Ask a follow-up to explore why.'),
        ('debug', json.dumps(dict(error_type='ArrayIndexOutOfBoundsException', language='Java',
            summary='The loop accesses one index beyond the array.', evidence=['i <= array.length'],
            root_cause='Java arrays end at index length - 1.',
            fixes=[dict(before='i <= array.length', after='i < array.length', explanation='Stop before the invalid final index.')], confidence='high'))),
        ('extract', json.dumps(dict(content_type='table', headers=['Name', 'Score'], rows=[['Alice', 92], ['Bob', 85]]))),
    ]
    canvas = QImage(2040, 600, QImage.Format.Format_RGB32)
    painter = QPainter(canvas)
    windows = []
    for index, (mode, response) in enumerate(examples):
        window = ResultWindow(mode, False)
        window.set_result(parse_result(mode, response))
        window.show()
        app.processEvents()
        painter.drawPixmap(index * 680, 0, window.grab())
        windows.append(window)
    painter.end()
    path = Path('result-ui-check.png')
    canvas.save(str(path))
    for window in windows:
        window.close()
    print(path.resolve())


if __name__ == '__main__':
    main()
