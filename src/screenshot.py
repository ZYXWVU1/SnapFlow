"""Capture before overlay display; keep pixels entirely in memory."""
from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QImage, QScreen


def capture_screen(screen: QScreen) -> QImage:
    image = screen.grabWindow(0).toImage()
    if image.isNull():
        raise RuntimeError("Unable to capture this display. Try an unlocked desktop.")
    return image


def crop_selection(image: QImage, selection: QRect, logical_size: tuple[int, int]) -> QImage:
    width, height = logical_size
    area = selection.normalized().intersected(QRect(0, 0, width, height))
    if area.width() < 10 or area.height() < 10:
        raise ValueError("Selection must be at least 10 by 10 pixels.")
    # Qt overlay coordinates are logical; captured image dimensions are physical.
    sx, sy = image.width() / width, image.height() / height
    left, top = round(area.x() * sx), round(area.y() * sy)
    right, bottom = round((area.x() + area.width()) * sx), round((area.y() + area.height()) * sy)
    result = image.copy(left, top, right - left, bottom - top)
    result.setDevicePixelRatio(1)
    return result


def resize_if_needed(image: QImage, max_width: int = 1920) -> QImage:
    if max_width <= 0:
        raise ValueError("Maximum width must be positive.")
    if image.width() > max_width:
        return image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    return image


def image_to_png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if image.isNull() or not image.save(buffer, "PNG"):
        raise ValueError("Unable to encode screenshot.")
    return bytes(buffer.data())
