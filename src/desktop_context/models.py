"""Only immutable normalized values cross the native-provider boundary."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ActiveWindowContext:
    hwnd: int | None = None
    process_id: int | None = None
    process_name: str | None = None
    app_name: str | None = None
    window_title: str | None = None
    bounds: tuple[int, int, int, int] | None = None
    captured_at: str = field(default_factory=now)
    category: str = 'other'
    fullscreen: bool = False


@dataclass(frozen=True)
class DesktopUIElement:
    element_id: str
    control_type: str | None = None
    name: str | None = None
    automation_id: str | None = None
    class_name: str | None = None
    value: str | None = None
    enabled: bool | None = None
    focused: bool | None = None
    bounds: tuple | None = None
    depth: int = 0
    protected: bool = False


@dataclass(frozen=True)
class UIAResult:
    elements: tuple[DesktopUIElement, ...] = ()
    focused_id: str | None = None
    selected_text: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionContext:
    text: str
    source_app: str | None = None
    source_window: str | None = None
    capture_method: str = 'uia_text_pattern'
    captured_at: str = field(default_factory=now)


@dataclass(frozen=True)
class DesktopContextSnapshot:
    active_window: ActiveWindowContext | None = None
    focused_element: DesktopUIElement | None = None
    ui_elements: tuple[DesktopUIElement, ...] = ()
    selection: SelectionContext | None = None
    clipboard_text: str | None = None
    screenshot_reference: str | None = None
    captured_at: str = field(default_factory=now)
    permissions: tuple[tuple[str, bool], ...] = ()
    warnings: tuple[str, ...] = ()
    snapshot_id: str = field(default_factory=lambda: uuid4().hex)
    permission_version: int = 0
