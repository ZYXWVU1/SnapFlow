"""Application identity and dependency-free Semantic Version helpers."""

from dataclasses import dataclass
import re


APP_NAME = "SnapFlow"
APP_VERSION = "0.8.0-beta.2"
RELEASE_CHANNEL = "beta"


_SEMANTIC_VERSION = re.compile(
    r"v?(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


@dataclass(frozen=True)
class SemanticVersion:
    """Parsed Semantic Version components."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()


def parse_semantic_version(version: str) -> SemanticVersion:
    """Parse a SemVer string, allowing an optional lowercase ``v`` prefix."""
    if not isinstance(version, str):
        raise ValueError("Version must be a string.")
    match = _SEMANTIC_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"Invalid Semantic Version: {version!r}")

    prerelease = tuple(match.group("prerelease").split(".")) if match.group("prerelease") else ()
    if any(identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
           for identifier in prerelease):
        raise ValueError(f"Invalid Semantic Version: {version!r}")
    build = tuple(match.group("build").split(".")) if match.group("build") else ()
    return SemanticVersion(
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        prerelease,
        build,
    )


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Compare prerelease identifiers according to SemVer precedence."""
    if not left:
        return 0 if not right else 1
    if not right:
        return -1

    for left_id, right_id in zip(left, right):
        if left_id == right_id:
            continue
        left_numeric, right_numeric = left_id.isdigit(), right_id.isdigit()
        if left_numeric and right_numeric:
            return (int(left_id) > int(right_id)) - (int(left_id) < int(right_id))
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_id > right_id) - (left_id < right_id)
    return (len(left) > len(right)) - (len(left) < len(right))


def is_newer_version(candidate: str, current: str) -> bool:
    """Return whether ``candidate`` has greater SemVer precedence than ``current``."""
    candidate_version = parse_semantic_version(candidate)
    current_version = parse_semantic_version(current)

    candidate_core = (candidate_version.major, candidate_version.minor, candidate_version.patch)
    current_core = (current_version.major, current_version.minor, current_version.patch)
    if candidate_core != current_core:
        return candidate_core > current_core
    return _compare_prerelease(candidate_version.prerelease, current_version.prerelease) > 0
