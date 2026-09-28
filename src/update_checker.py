"""Bounded, HTTPS-only manual update metadata check."""
from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (HTTPRedirectHandler, Request, build_opener)

from src.app_version import APP_NAME, APP_VERSION, is_newer_version, parse_semantic_version
from src.release_config import RELEASE_API_HOST, RELEASE_API_URL


REQUEST_TIMEOUT_SECONDS = 6
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RELEASE_NOTES_CHARS = 12_000


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    name: str
    notes: str
    published_at: str
    is_newer: bool


@dataclass(frozen=True)
class UpdateCheckResult:
    release: ReleaseInfo | None = None
    error_code: str | None = None


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _validate_release_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    tag = payload.get("tag_name")
    try:
        parse_semantic_version(tag)
    except ValueError as exc:
        raise ValueError("invalid_version") from exc
    name = payload.get("name")
    notes = payload.get("body")
    published_at = payload.get("published_at")
    if not isinstance(name, str):
        name = tag
    if not isinstance(notes, str):
        notes = ""
    if not isinstance(published_at, str):
        published_at = ""
    return ReleaseInfo(tag[:128], name[:256], notes[:MAX_RELEASE_NOTES_CHARS],
                       published_at[:64], is_newer_version(tag, APP_VERSION))


def check_latest_release():
    """Fetch only metadata from the configured GitHub Releases API endpoint."""
    parsed_endpoint = urlparse(RELEASE_API_URL)
    if parsed_endpoint.scheme != "https" or parsed_endpoint.hostname != RELEASE_API_HOST:
        return UpdateCheckResult(error_code="invalid_source")
    request = Request(RELEASE_API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}-Update-Checker/{APP_VERSION}",
    })
    opener = build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != RELEASE_API_HOST:
                return UpdateCheckResult(error_code="invalid_source")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return UpdateCheckResult(error_code="no_release")
        return UpdateCheckResult(error_code="unavailable")
    except (URLError, TimeoutError, OSError):
        return UpdateCheckResult(error_code="offline")
    if len(raw) > MAX_RESPONSE_BYTES:
        return UpdateCheckResult(error_code="response_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
        release = _validate_release_payload(payload)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return UpdateCheckResult(error_code="invalid_response")
    return UpdateCheckResult(release=release)
