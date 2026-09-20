from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QScreen
from PySide6.QtWidgets import QWidget

from src.screenshot import crop_selection


class SelectionOverlay(QWidget):
    selected = Signal(QImage)
    cancelled = Signal()

    def __init__(self, screen: QScreen, image: QImage) -> None:
        super().__init__()
        self.image = image
        self.start: QPoint | None = None
        self.end = QPoint()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.image)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 95))
        if self.start is not None:
            rect = QRect(self.start, self.end).normalized().intersected(self.rect())
            painter.save()
            painter.setClipRect(rect)
            painter.drawImage(self.rect(), self.image)
            painter.restore()
            painter.setPen(QPen(QColor("#60c8ff"), 2))
            painter.drawRect(rect)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(24, 34, "Drag to select  |  Esc to cancel  |  Select within one display")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.end = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        rect = QRect(self.start, event.position().toPoint()).normalized()
        try:
            image = crop_selection(self.image, rect, (self.width(), self.height()))
        except ValueError:
            self.cancelled.emit()
        else:
            self.selected.emit(image)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
