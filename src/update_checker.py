"""Bounded, HTTPS-only manual update metadata check."""
from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (HTTPRedirectHandler, Request, build_opener)

from src.app_version import APP_NAME, APP_VERSION, RELEASE_CHANNEL, is_newer_version, parse_semantic_version
from src.release_config import RELEASE_API_HOST, RELEASE_API_URL
from src.network_policy import get_network_policy, NetworkPolicyError


REQUEST_TIMEOUT_SECONDS = 6
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RELEASE_NOTES_CHARS = 12_000
MAX_RELEASES = 30
UPDATE_CHANNELS = frozenset({'stable', 'beta', 'dev'})


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
    if not isinstance(tag, str) or len(tag) > 128:
        raise ValueError('invalid_version')
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


def select_release(payload, channel=RELEASE_CHANNEL):
    """Select the highest SemVer in an explicitly allowed release channel.

    Stable excludes all prereleases; beta allows beta prereleases and stable.
    Other prerelease labels (including alpha/rc) require explicit dev opt-in.
    Legacy single-release metadata is accepted for existing offline callers.
    """
    if not isinstance(channel, str) or channel not in UPDATE_CHANNELS:
        raise ValueError('invalid_channel')
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or len(payload) > MAX_RELEASES:
        raise ValueError('invalid_payload')
    selected = None
    valid_versions = 0
    invalid_versions = 0
    for item in payload:
        if not isinstance(item, dict) or item.get('draft') is not None and item.get('draft') is not False:
            continue
        try:
            version = parse_semantic_version(item.get('tag_name'))
            release = _validate_release_payload(item)
        except ValueError:
            invalid_versions += 1
            continue
        valid_versions += 1
        if item.get('prerelease') is True and not version.prerelease and channel != 'dev':
            continue
        if version.prerelease:
            if channel == 'stable' or channel == 'beta' and version.prerelease[0] != 'beta':
                continue
        if selected is None or is_newer_version(release.tag_name, selected.tag_name):
            selected = release
    if invalid_versions and not valid_versions:
        raise ValueError('invalid_version')
    return selected


def check_latest_release(channel=None):
    """Fetch only metadata from the configured GitHub Releases API endpoint."""
    channel = RELEASE_CHANNEL if channel is None else channel
    if not isinstance(channel, str) or channel not in UPDATE_CHANNELS:
        return UpdateCheckResult(error_code='invalid_channel')
    parsed_endpoint = urlparse(RELEASE_API_URL)
    if parsed_endpoint.scheme != "https" or parsed_endpoint.hostname != RELEASE_API_HOST:
        return UpdateCheckResult(error_code="invalid_source")
    try:
        get_network_policy().require_allowed(RELEASE_API_URL, 'update_check', contains_user_content=False)
    except NetworkPolicyError:
        return UpdateCheckResult(error_code="private_mode")
    request = Request(RELEASE_API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}-Update-Checker/{APP_VERSION}",
    })
    opener = build_opener(_RejectRedirects())
    try:
        get_network_policy().require_allowed(RELEASE_API_URL, 'update_check', contains_user_content=False)
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != RELEASE_API_HOST:
                return UpdateCheckResult(error_code="invalid_source")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except NetworkPolicyError:
        return UpdateCheckResult(error_code="private_mode")
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
        release = select_release(payload, channel)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return UpdateCheckResult(error_code="invalid_response")
    if release is None:
        return UpdateCheckResult(error_code='no_release')
    return UpdateCheckResult(release=release)
