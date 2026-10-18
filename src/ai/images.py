"""Describe the captured image without replacing the original or losing small text."""
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtCore import QBuffer, QIODevice, Qt


def prepare_image(payload, max_width=None):
    buffer = QBuffer()
    buffer.setData(payload)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    size = QImageReader(buffer).size()
    if not size.isValid() or size.width() * size.height() > 64000000:
        raise ValueError('The screenshot is unsupported or too large. Capture a smaller region.')
    image = QImage.fromData(payload)
    if image.isNull():
        raise ValueError('The screenshot is not a supported image. Capture it again.')
    encoding = ('png' if payload.startswith(b'\x89PNG') else 'jpeg' if payload.startswith(b'\xff\xd8')
                else 'webp' if payload.startswith(b'RIFF') and payload[8:12] == b'WEBP' else None)
    original_width, original_height = image.width(), image.height()
    resize = max_width is not None and original_width > max_width
    if resize:
        image = image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    if resize or encoding is None:
        from src.screenshot import image_to_png_bytes
        payload = image_to_png_bytes(image)
        encoding, compression = 'png', 'lossless'
    else:
        compression = 'preserve'
    return payload, dict(original_width=original_width, original_height=original_height,
                sent_width=image.width(), sent_height=image.height(), encoding=encoding,
                resize_factor=image.width() / original_width, compression=compression)


def image_metadata(payload):
    return prepare_image(payload)[1]

class PreparedImageScope:
    """Single-entry preparation reuse owned by one capture/request, never global.

    Keep this scope only while its capture is active; clear on replacement/close.
    Only immutable bytes are cached, and every new value receives full validation.
    """
    def __init__(self):
        from threading import Lock
        self._lock = Lock()
        self._generation = 0
        self._closed = False
        self.clear()

    def clear(self):
        with self._lock:
            self._generation += 1
            self._payload = None
            self._width = None
            self._prepared = None

    def close(self):
        """Retire this capture permanently, including requests entering later."""
        with self._lock:
            self._closed = True
            self._generation += 1
            self._payload = self._prepared = None
            self._width = None

    def prepare(self, payload, max_width=None):
        with self._lock:
            if (not self._closed and isinstance(payload, bytes) and payload is self._payload
                    and max_width == self._width and self._prepared is not None):
                result, metadata = self._prepared
                return result, dict(metadata)
            self._generation += 1
            generation = self._generation
            self._payload = self._prepared = None
            self._width = None
        result, metadata = prepare_image(payload, max_width)
        with self._lock:
            if not self._closed and isinstance(payload, bytes) and generation == self._generation:
                self._payload, self._width = payload, max_width
                self._prepared = result, dict(metadata)
        return result, dict(metadata)
