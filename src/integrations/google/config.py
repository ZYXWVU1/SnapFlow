"""Resolve the Google OAuth client configuration for this installation."""
import os
from pathlib import Path


def resolve_google_client_id(resource_root: str | Path) -> str | None:
    """Read the Google client ID from the environment or packaged resources."""
    environment_value = os.environ.get("SNAPFLOW_GOOGLE_CLIENT_ID")
    if environment_value is not None:
        environment_value = environment_value.strip()
        if environment_value:
            return environment_value

    client_id_file = Path(resource_root) / "google_oauth_client_id.txt"
    try:
        client_id = client_id_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return client_id or None


def resolve_google_client_secret(resource_root: str | Path) -> str | None:
    """Read the optional Google OAuth client secret from env or packaged resources."""
    environment_value = os.environ.get("SNAPFLOW_GOOGLE_CLIENT_SECRET")
    if environment_value is not None:
        environment_value = environment_value.strip()
        if environment_value:
            return environment_value

    client_secret_file = Path(resource_root) / "google_oauth_client_secret.txt"
    try:
        client_secret = client_secret_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return client_secret or None
