"""Consistent monochrome line icons rendered from small local SVGs."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_SHAPES = {
    'home': '<path d="M3 11 12 4l9 7v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M9 21v-7h6v7"/>',
    'skills': '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 8h10M7 12h10M7 16h6"/>',
    'workflows': '<circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M7 7.5 11 16M17 7.5 13 16M7 6h10"/>',
    'integrations': '<path d="M9 8H7a4 4 0 0 0 0 8h2M15 8h2a4 4 0 0 1 0 8h-2M8 12h8"/>',
    'evaluation': '<path d="M4 20V11h4v9M10 20V7h4v13M16 20V4h4v16M3 20h18"/>',
    'history': '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    'settings': '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="9"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>',
    'capture': '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/><circle cx="12" cy="12" r="3"/>',
}


def icon(name, color='#68788D'):
    shape = _SHAPES[name]
    source = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
              f'fill="none" stroke="{color}" stroke-width="1.8" '
              f'stroke-linecap="round" stroke-linejoin="round">{shape}</svg>')
    renderer = QSvgRenderer(QByteArray(source.encode('utf-8')))
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
