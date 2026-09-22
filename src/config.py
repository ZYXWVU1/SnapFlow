"""Validated, non-secret settings stored beside the application."""
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from src.prompts import MODES, ALIASES

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
SMART_CLASSIFICATION_THRESHOLD = 0.75


def parse_hotkey(value: str) -> tuple[int, int]:
    parts = value.lower().replace(" ", "").split("+")
    modifiers = {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}
    if len(parts) < 2 or len(set(parts)) != len(parts):
        raise ValueError("Use a hotkey such as ctrl+shift+s.")
    if any(part not in modifiers for part in parts[:-1]):
        raise ValueError("Hotkey modifiers: ctrl, shift, alt, win.")
    key = parts[-1]
    if len(key) != 1 or not key.isascii() or not key.isalnum():
        raise ValueError("The hotkey must end in a letter or number.")
    return sum(modifiers[part] for part in parts[:-1]), ord(key.upper())


@dataclass(frozen=True)
class Config:
    hotkey: str = "ctrl+shift+s"
    default_mode: str = "ask"
    max_image_width: int = 1920
    always_on_top: bool = True
    smart_classification_threshold: float = SMART_CLASSIFICATION_THRESHOLD

    def __post_init__(self) -> None:
        if isinstance(self.default_mode, str):
            object.__setattr__(self, 'default_mode', ALIASES.get(self.default_mode, self.default_mode))
        if not isinstance(self.hotkey, str):
            raise ValueError("Hotkey must be text.")
        parse_hotkey(self.hotkey)
        if not isinstance(self.default_mode, str) or self.default_mode not in MODES:
            raise ValueError("Unknown default mode.")
        if type(self.max_image_width) is not int or not 320 <= self.max_image_width <= 8192:
            raise ValueError("Maximum image width must be between 320 and 8192.")
        if type(self.always_on_top) is not bool:
            raise ValueError("Always on top must be true or false.")
        threshold = self.smart_classification_threshold
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('Smart classification threshold must be between 0 and 1.')


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Settings must be a JSON object.")
        return Config(**data)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"Unable to load {path.name}: {exc}") from exc


def save_config(config: Config, path: Path = CONFIG_PATH) -> None:
    temp = path.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
