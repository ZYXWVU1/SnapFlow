"""Validated, non-secret settings stored beside the application."""
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from src.prompts import MODES, ALIASES
from src.paths import AppPaths

DEFAULT_PATHS = AppPaths()
ROOT = DEFAULT_PATHS.resource_root
CONFIG_PATH = DEFAULT_PATHS.config_file
SMART_CLASSIFICATION_THRESHOLD = 0.75
DEFAULT_AI_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_AI_MODEL = 'gpt-4.1-mini'


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
    default_mode: str = "smart"
    max_image_width: int = 1920
    always_on_top: bool = True
    smart_classification_threshold: float = SMART_CLASSIFICATION_THRESHOLD
    theme: str = 'system'
    onboarding_complete: bool = False
    monthly_cost_warning_usd: float = 5.0
    max_evaluation_cases: int = 100
    automatic_update_checks: bool = False
    ai_base_url: str | None = None
    ai_model: str | None = None

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
        if self.theme not in ('system', 'light', 'dark'):
            raise ValueError('Theme must be System, Light, or Dark.')
        if type(self.onboarding_complete) is not bool:
            raise ValueError('Onboarding completion must be true or false.')
        warning = self.monthly_cost_warning_usd
        if type(warning) not in (int, float) or not math.isfinite(warning) or not 0 <= warning <= 1000000:
            raise ValueError('Monthly cost warning must be between 0 and 1,000,000 USD.')
        if type(self.max_evaluation_cases) is not int or not 1 <= self.max_evaluation_cases <= 10000:
            raise ValueError('Maximum Evaluation cases must be between 1 and 10,000.')
        if type(self.automatic_update_checks) is not bool:
            raise ValueError('Automatic update checks must be true or false.')
        if self.ai_base_url is not None:
            if not isinstance(self.ai_base_url, str):
                raise ValueError('AI endpoint must be text.')
            endpoint = self.ai_base_url.strip()
            try:
                parsed = urlsplit(endpoint)
                hostname = (parsed.hostname or '').lower()
                _port = parsed.port
                valid_endpoint = (
                    parsed.scheme in ('https', 'http') and bool(hostname)
                    and not any(character.isspace() for character in endpoint)
                    and parsed.username is None and parsed.password is None
                    and not parsed.query and not parsed.fragment
                    and (parsed.scheme == 'https' or hostname in ('localhost', '127.0.0.1', '::1'))
                )
            except ValueError:
                valid_endpoint = False
            if not valid_endpoint:
                raise ValueError('Use an HTTPS AI endpoint, or HTTP on localhost only.')
            object.__setattr__(self, 'ai_base_url', endpoint.rstrip('/'))
        if self.ai_model is not None:
            if not isinstance(self.ai_model, str):
                raise ValueError('AI model must be text.')
            model = self.ai_model.strip()
            if not model or len(model) > 128:
                raise ValueError('AI model must contain 1 to 128 characters.')
            object.__setattr__(self, 'ai_model', model)


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
