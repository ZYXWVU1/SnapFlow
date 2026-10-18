"""Bounded capture encoding. Workers own QImage values, never widgets or pixmaps."""
from threading import Event
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage
from src.screenshot import image_to_png_bytes


class CaptureSignals(QObject):
    finished = Signal(int, object, str)


class CaptureWorker(QRunnable):
    def __init__(self, generation, image):
        super().__init__()
        self.generation = generation
        # Copy once on the GUI thread so even externally backed QImages become
        # independently owned pixels before crossing the worker boundary.
        self.image = image.copy()
        self.cancelled = Event()
        self.signals = CaptureSignals()

    def cancel(self):
        self.cancelled.set()
        # A running encoder holds a local QImage value; queued pixels can go now.
        self.image = QImage()

    def run(self):
        payload, error = b'', ''
        try:
            image = QImage(self.image)
            if not self.cancelled.is_set():
                if image.width() * image.height() > 64000000:
                    raise ValueError('The screenshot is unsupported or too large. Capture a smaller region.')
                payload = image_to_png_bytes(image)
        except Exception as exc:
            error = str(exc) or 'Unable to encode screenshot.'
        finally:
            self.image = QImage()
        if self.cancelled.is_set():
            payload, error = b'', ''
        self.signals.finished.emit(self.generation, payload, error)
