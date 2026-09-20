import unittest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from src.screenshot import crop_selection, image_to_png_bytes, resize_if_needed


class ScreenshotTests(unittest.TestCase):
    def test_scaled_crop_and_png(self):
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        image.setPixelColor(40, 20, QColor("blue"))
        cropped = crop_selection(image, QRect(20, 10, 50, 30), (200, 100))
        self.assertEqual((cropped.width(), cropped.height()), (100, 60))
        self.assertEqual(cropped.pixelColor(0, 0), QColor("blue"))
        encoded = image_to_png_bytes(cropped)
        decoded = QImage.fromData(encoded, "PNG")
        self.assertEqual(decoded, cropped)

    def test_resize_and_empty_selection(self):
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        resized = resize_if_needed(image, 200)
        self.assertEqual((resized.width(), resized.height()), (200, 100))
        with self.assertRaises(ValueError):
            crop_selection(image, QRect(0, 0, 3, 4), (400, 200))
        with self.assertRaises(ValueError):
            image_to_png_bytes(QImage())
