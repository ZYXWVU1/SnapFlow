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
