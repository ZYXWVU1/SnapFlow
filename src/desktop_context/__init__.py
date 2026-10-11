"""Ephemeral, permission-controlled read-only desktop context."""
from .models import ActiveWindowContext, DesktopContextSnapshot, DesktopUIElement, SelectionContext
from .service import DesktopContextService

__all__ = ['ActiveWindowContext', 'DesktopContextSnapshot', 'DesktopUIElement',
           'SelectionContext', 'DesktopContextService']
