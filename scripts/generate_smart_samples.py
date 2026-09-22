"""Generate 25 intentionally synthetic PNG fixtures; no desktop capture or API use."""
from pathlib import Path
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

SAMPLES = {
    'assignment': [
        'CS 201 - Homework 4\nImplement binary search.\nDue September 25 at 11:59 PM.\nSubmit your source code to the course portal.',
        'Biology 101 - Lab report\nDescribe your microscope observations.\nSubmission deadline: October 3, 5 PM.',
        'Math 204 - Problem Set 7\nComplete exercises 1 through 12.\nUpload a PDF by Friday at noon.',
        'History 110 - Essay\nCompare two primary sources from class.\n1500 words. Due November 2.',
        'Chemistry 102 - Quiz 3\nChapters 4 and 5.\nComplete the online quiz before Monday 9 AM.',
    ],
    'event': [
        'Meeting invitation\nDesign review\nOctober 8, 2026, 2:00 - 3:00 PM\nConference Room B\nAccept   Tentative   Decline',
        'Appointment confirmation\nBike repair pickup\nSeptember 30 at 4:30 PM\nDowntown bike shop',
        'Community concert\nSaturday October 10 at 7 PM\nCity Park amphitheater\nAdmission is free',
        'Calendar: Team planning\nTuesday 10:00 AM - 11:00 AM\nLocation: Meeting room 2',
        'You are invited!\nBook club gathering\nNovember 5, 6 PM\nPublic library, second floor',
    ],
    'code_error': [
        'Traceback (most recent call last):\n  File "main.py", line 4, in <module>\n    print(items[9])\nIndexError: list index out of range',
        'Exception in thread "main" java.lang.NullPointerException\n  at Demo.main(Demo.java:12)',
        'src/main.cpp:8:12: error: expected semicolon\n    return 0\n            ^',
        'TypeError: Cannot read properties of undefined\n    at render (App.js:21:10)',
        'error[E0308]: mismatched types\n --> src/main.rs:3:18\n expected i32, found &str',
    ],
    'table': [
        'Quarterly totals\nProduct       Q1       Q2       Q3\nPencils       120      140      180\nNotebooks     80       95       110\nFolders       45       50       70',
        'Inventory\nItem          Quantity   Price\nCable         20         8.50\nAdapter       12        15.00\nStand          7        22.00',
        'Weather observations\nDay        Temperature   Rainfall\nMonday     21            0\nTuesday    19            4\nWednesday  23            0',
        'Travel distances\nCity         Distance km   Duration min\nNorthport    42            38\nWesthaven    65            52\nLakeside     18            21',
        'Experiment results\nTrial      Input      Output\n1          10         23\n2          20         41\n3          30         62',
    ],
    'unknown': [
        'A quiet afternoon\nThe sky is blue and the garden is peaceful.',
        'Recipe: Fruit salad\nChop apples and pears.\nAdd grapes and stir gently.',
        'Welcome to the reading room\nPlease keep your voice low.',
        'Store notice\nWe now carry recycled paper bags.\nThank you for shopping locally.',
        'A short story\nThe traveler reached the forest\nand watched the leaves move in the wind.',
    ],
}


def main():
    app = QApplication.instance() or QApplication([])
    root = Path(__file__).resolve().parents[1] / 'tests/manual_samples'
    for category, samples in SAMPLES.items():
        folder = root / category
        folder.mkdir(parents=True, exist_ok=True)
        for index, text in enumerate(samples, 1):
            image = QImage(1000, 500, QImage.Format.Format_RGB32)
            image.fill(QColor('#f5f7fa'))
            painter = QPainter(image)
            painter.setPen(QColor('#182536'))
            painter.setFont(QFont('Consolas', 17))
            painter.drawText(QRect(35, 35, 930, 430), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)
            painter.end()
            if not image.save(str(folder / f'{index:02}.png')):
                raise RuntimeError('Unable to save synthetic fixture')
    print(f'Generated 25 synthetic images in {root}')


if __name__ == '__main__':
    main()
