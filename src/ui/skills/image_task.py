"""Image selection and cancellable background jobs shared by teaching/testing."""
import threading
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QLabel, QFileDialog
from src.screenshot import image_to_png_bytes, resize_if_needed

# Hold runnable signal objects until delivery, including after their dialog closes.
_JOBS = set()


class JobSignals(QObject):
    finished = Signal(int, object, str)


class ImageJob(QRunnable):
    def __init__(self, generation, data, operation, cancelled):
        super().__init__()
        self.signals = JobSignals()
        self.generation, self.data = generation, data
        self.operation, self.cancelled = operation, cancelled

    def run(self):
        result, error = None, ''
        try:
            if not self.cancelled.is_set():
                result = self.operation(self.data, self.cancelled)
        except ValueError as exc:
            # Schema validators contain application-authored messages only.
            error = str(exc)
        except Exception:
            error = 'The AI request could not be completed. Check your connection and provider settings, then retry.'
        finally:
            self.data = b''
            self.operation = None
        self.signals.finished.emit(self.generation, result, error)


class ImageTaskDialog(QDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.image_bytes = b''
        self.generation = 0
        self.busy = False
        self.job = None
        self.cancelled = threading.Event()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(650, 680)
        self.layout = QVBoxLayout(self)
        self.choose = QPushButton('Select Screenshot')
        self.choose.clicked.connect(self.select_image)
        self.layout.addWidget(self.choose)
        self.image_preview = QLabel('Choose a PNG, JPEG or WebP screenshot.')
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMinimumHeight(110)
        self.layout.addWidget(self.image_preview)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)

    def select_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Screenshot', '', 'Images (*.png *.jpg *.jpeg *.webp)')
        if not path:
            return
        reader = QImageReader(path)
        size = reader.size()
        if not size.isValid() or size.width() * size.height() > 40_000_000:
            self.status.setText('Choose a valid image under 40 megapixels.')
            return
        image = reader.read()
        if image.isNull():
            self.status.setText('Unable to read this screenshot.')
            return
        image = resize_if_needed(image, 1920)
        self.image_bytes = image_to_png_bytes(image)
        self.image_preview.setPixmap(QPixmap.fromImage(image).scaled(550, 200, Qt.AspectRatioMode.KeepAspectRatio,
                                                                   Qt.TransformationMode.SmoothTransformation))
        self.status.clear()
        self.image_changed()

    def image_changed(self):
        pass

    def start_job(self, operation, message):
        if self.busy:
            return
        if not self.image_bytes:
            self.status.setText('Select a screenshot first.')
            return
        self.generation += 1
        self.cancelled = threading.Event()
        self.busy = True
        self.choose.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status.setText(message)
        self.job = ImageJob(self.generation, self.image_bytes, operation, self.cancelled)
        _JOBS.add(self.job)
        self.job.signals.finished.connect(self.job_finished)
        QThreadPool.globalInstance().start(self.job)

    @Slot(int, object, str)
    def job_finished(self, generation, result, error):
        _JOBS.discard(self.job)
        self.job = None
        self.busy = False
        self.choose.setEnabled(True)
        self.run_button.setEnabled(True)
        if generation != self.generation or self.cancelled.is_set():
            return
        if error:
            self.status.setText(error)
        elif result is not None:
            self.completed(result)

    def completed(self, result):
        raise NotImplementedError

    def invalidate(self):
        self.generation += 1
        self.cancelled.set()
        self.image_bytes = b''
        self.image_preview.clear()

    def close(self):
        # QWidget.close() on an already hidden dialog does not invoke done().
        self.invalidate()
        return super().close()

    def done(self, result):
        self.invalidate()
        super().done(result)
