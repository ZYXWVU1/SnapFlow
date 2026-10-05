"""Validated, non-secret settings stored beside the application."""
import json
import math
import os
import shutil
from datetime import datetime, timezone
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from src.prompts import MODES, ALIASES
from src.paths import AppPaths
from src.app_version import RELEASE_CHANNEL
from src.network_policy import is_loopback_url

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
    update_channel: str = RELEASE_CHANNEL
    ai_base_url: str | None = None
    ai_model: str | None = None
    visual_memory_enabled: bool = True
    show_memory_save_button: bool = True
    semantic_search_enabled: bool = False
    ai_memory_questions: bool = False
    contextual_assistant_enabled: bool = True
    context_memory_search_enabled: bool = False
    local_observability_enabled: bool = True
    local_crash_reports_enabled: bool = True
    ai_execution_mode: str = 'cloud_only'
    private_mode: bool = False
    allow_cloud_fallback: bool = False
    local_ai_endpoint: str = 'http://127.0.0.1:8080/v1'
    local_ai_model: str | None = None
    local_ai_model_version: str | None = None
    ai_model_version: str | None = None
    local_ai_vision: bool = False
    local_ai_structured_output: bool = False
    local_ai_context_length: int = 8192
    local_embedding_model: str | None = None
    local_embedding_dimension: int = 0
    local_embedding_version: str = '1'

    def __post_init__(self) -> None:
        if self.update_channel not in ('stable', 'beta', 'dev'):
            raise ValueError('Unknown update channel.')
        if self.ai_execution_mode not in ('automatic', 'prefer_local', 'local_only', 'cloud_only', 'ask_every_time'):
            raise ValueError('Unknown AI execution mode.')
        for name in ('private_mode', 'allow_cloud_fallback', 'local_ai_vision', 'local_ai_structured_output',
                     'local_observability_enabled', 'local_crash_reports_enabled'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'{name} must be true or false.')
        try:
            local_url = urlsplit(self.local_ai_endpoint)
            local_port = local_url.port
            local_valid = is_loopback_url(self.local_ai_endpoint) and not local_url.query and not local_url.fragment
        except (TypeError, ValueError):
            local_valid = False
        if not local_valid:
            raise ValueError('Local AI requires an HTTP(S) loopback endpoint.')
        for name in ('local_ai_model', 'local_embedding_model', 'local_ai_model_version', 'ai_model_version', 'local_embedding_version'):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 128):
                raise ValueError('Local model identifiers must contain 1–128 characters.')
        if type(self.local_ai_context_length) is not int or not 256 <= self.local_ai_context_length <= 1000000:
            raise ValueError('Local context length must be between 256 and 1,000,000.')
        if type(self.local_embedding_dimension) is not int or not 0 <= self.local_embedding_dimension <= 65536:
            raise ValueError('Embedding dimension must be between 0 and 65,536.')
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
        for name in ('visual_memory_enabled', 'show_memory_save_button',
                     'semantic_search_enabled', 'ai_memory_questions',
                     'contextual_assistant_enabled', 'context_memory_search_enabled'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'{name} must be true or false.')
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


def backup_corrupt_settings(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise OSError('Corrupt settings must be a regular file.')
    directory = path.parent / 'backups'
    if directory.is_symlink():
        raise OSError('Settings backup directory cannot be a symbolic link.')
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / ('corrupt-settings-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') +
                               '-' + uuid.uuid4().hex + '.json')
    with path.open('rb') as source, destination.open('xb') as target:
        shutil.copyfileobj(source, target)
        target.flush()
        os.fsync(target.fileno())
    return destination


def save_config(config: Config, path: Path = CONFIG_PATH) -> None:
    path = Path(path)
    if path.exists():
        try:
            load_config(path)
        except ValueError:
            backup_corrupt_settings(path)
    temp = path.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
