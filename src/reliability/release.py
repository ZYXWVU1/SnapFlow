"""Offline release manifest and conservative, source-bound verification gates.

Evidence is a local JSON document with a ``gates`` mapping. Every PASS needs
an integer exit_code=0, UTC verified_at, and matching source_fingerprint. Test
evidence additionally records passed/failed/errors/skipped; command evidence
without counts must provide a nonempty command. Logs may be referenced only
relative to the evidence directory, with an exact SHA256 (never copied here).
These records attest controlled executions; this module does not run tests,
contact services, or infer live model success from mocked tests.
"""
from datetime import datetime, timezone, timedelta
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import re

from src.app_version import APP_VERSION, RELEASE_CHANNEL, is_newer_version, parse_semantic_version
from src.database import CURRENT_SCHEMA_VERSION


class GateStatus(str, Enum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    NOT_VERIFIED = 'NOT VERIFIED'


REQUIRED_GATES = ('available_full_regression', 'package_smoke', 'installer_upgrade',
                  'safe_mode', 'crash_recovery', 'private_mode', 'backup_restore',
                  'visual_memory', 'mcp_client', 'mcp_server', 'local_ai', 'soak_30min')
OPTIONAL_GATES = ('cloud_ai', 'soak_2h', 'manual_accessibility', 'sleep_resume')
# Agent qualification is additive: legacy Phase 13 callers keep their contract.
AGENT_REQUIRED_GATES = ('agent_planning', 'agent_execution', 'agent_recovery',
                        'agent_privacy', 'agent_approval', 'agent_no_duplicate_effects',
                        'agent_evaluation', 'agent_backup_restore', 'agent_package_smoke')
MAXIMUM_SUPPORTED_UPGRADE_FROM = '0.8.0-beta.1'
SHA256_RE = re.compile(r'[0-9a-f]{64}')
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


def _safe_path(root, relative):
    root = Path(root).absolute()
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError('Symbolic-link roots are not allowed.')
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or any(part in ('..', '') for part in relative.parts):
        raise ValueError('Expected a contained relative path.')
    candidate = root / relative
    if any(part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction())
           for part in (candidate, *candidate.parents)):
        raise ValueError('Symbolic links and junctions are not allowed.')
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path is outside the expected directory.')
    return candidate


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def source_fingerprint(root):
    """Hash actual source bytes, filenames, build inputs, and verifier scripts.

    Git HEAD is insufficient for a working candidate. Generated files, local
    settings, databases, models, logs, docs, and release outputs are excluded.
    Tests are included so verification also belongs to the exact test inputs.
    """
    root = Path(root).absolute()
    candidates = set()
    for folder in ('src', 'tests', 'scripts', 'packaging'):
        base = _safe_path(root, folder)
        if base.exists():
            for path in base.rglob('*'):
                if '__pycache__' in path.parts:
                    continue
                if path.suffix in ('.py', '.spec', '.iss', '.txt', '.json', '.toml'):
                    checked = _safe_path(root, path.relative_to(root))
                    if checked.is_file():
                        candidates.add(checked)
    workflow = _safe_path(root, '.github/workflows/phase13-release.yml')
    if workflow.is_file():
        candidates.add(workflow)
    for name in ('main.py', 'requirements.txt', 'requirements-dev.txt', 'pyproject.toml'):
        path = _safe_path(root, name)
        if path.is_file():
            candidates.add(path)
    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def create_release_manifest(artifact_dir, *, release_notes='RELEASE_NOTES.md', source_root=None,
                            maximum_supported_upgrade_from=MAXIMUM_SUPPORTED_UPGRADE_FROM):
    """Describe an existing candidate, never download or install it.

    maximum_supported_upgrade_from is the newest predecessor tested for an
    upgrade into this candidate; it does not promise untested older paths.
    minimum_data_version is the candidate's current on-disk schema after
    migration. Existing migrations still determine accepted historical data.
    """
    parse_semantic_version(maximum_supported_upgrade_from)
    if not is_newer_version(APP_VERSION, maximum_supported_upgrade_from):
        raise ValueError('The upgrade predecessor must be older than this candidate.')
    directory = Path(artifact_dir).absolute()
    notes = _safe_path(directory, release_notes)
    if not notes.is_file() or notes.stat().st_size == 0:
        raise ValueError('Release notes are missing or empty.')
    artifacts = []
    for path in sorted(directory.iterdir()):
        checked = _safe_path(directory, path.name)
        if checked.name == 'SHA256SUMS.txt' or checked.suffix.lower() not in ('.exe', '.zip', '.md', '.txt'):
            continue
        if checked.is_file():
            if not checked.stat().st_size:
                raise ValueError('A release artifact is empty.')
            artifacts.append({'name': checked.name, 'size_bytes': checked.stat().st_size,
                              'sha256': _sha256(checked)})
    if not any(Path(item['name']).suffix.lower() in ('.exe', '.zip') for item in artifacts):
        raise ValueError('An installer or portable candidate artifact is required.')
    manifest = {'schema_version': 1, 'version': APP_VERSION, 'channel': RELEASE_CHANNEL,
                'minimum_data_version': CURRENT_SCHEMA_VERSION,
                'maximum_supported_upgrade_from': maximum_supported_upgrade_from,
                'upgrade_compatibility_scope': 'newest tested predecessor; older versions require separate evidence',
                'release_notes': notes.relative_to(directory).as_posix(), 'artifacts': artifacts}
    if source_root is not None:
        manifest['source_fingerprint'] = source_fingerprint(source_root)
    return manifest


def load_release_evidence(path):
    path = Path(path).absolute()
    checked = _safe_path(path.parent, path.name)
    if checked.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError('Evidence exceeds the allowed size.')
    evidence = json.loads(checked.read_text(encoding='utf-8'))
    if not isinstance(evidence, dict) or not isinstance(evidence.get('gates'), dict):
        raise ValueError('Evidence must contain a gates mapping.')
    return evidence


def _gate_result(name, item, fingerprint, now, max_age, evidence_root):
    result = {'required': name in REQUIRED_GATES, 'status': GateStatus.NOT_VERIFIED.value,
              'reason': 'No verification evidence.'}
    if item is None:
        return result
    if not isinstance(item, dict):
        return dict(result, status=GateStatus.FAIL.value, reason='Malformed evidence.')
    for field in ('exit_code', 'passed', 'failed', 'errors', 'skipped', 'duration_seconds'):
        value = item.get(field)
        if type(value) is int or type(value) is float and math.isfinite(value):
            result[field] = value
    status = item.get('status')
    if status not in tuple(value.value for value in GateStatus):
        return dict(result, status=GateStatus.FAIL.value, reason='Unknown gate status.')
    if status != GateStatus.PASS.value:
        return dict(result, status=status, reason='Explicit failure.' if status == 'FAIL' else 'Explicitly unverified.')
    exit_code = item.get('exit_code')
    if type(exit_code) is not int:
        return dict(result, reason='Missing verified integer exit code.')
    if exit_code != 0:
        return dict(result, status='FAIL', reason='Verification process failed.')
    count_fields = ('passed', 'failed', 'errors', 'skipped')
    if any(field in item for field in count_fields):
        if any(type(item.get(field)) is not int or item[field] < 0 for field in count_fields):
            return dict(result, status='FAIL', reason='Invalid or incomplete test counts.')
        if item['failed'] or item['errors']:
            return dict(result, status='FAIL', reason='Test failures or errors recorded.')
        if item['passed'] == 0:
            return dict(result, reason='No test cases passed; skips do not establish success.')
    elif not isinstance(item.get('command'), str) or not item['command'].strip():
        return dict(result, reason='Neither a verified command nor test counts were recorded.')
    if item.get('source_fingerprint') != fingerprint or not SHA256_RE.fullmatch(fingerprint):
        return dict(result, reason='Evidence belongs to another source candidate.')
    try:
        verified_at = datetime.fromisoformat(item['verified_at'].replace('Z', '+00:00'))
        if verified_at.tzinfo is None or verified_at > now + timedelta(minutes=5) or now - verified_at > max_age:
            return dict(result, reason='Evidence timestamp is stale or invalid.')
    except (ValueError, TypeError, KeyError, AttributeError):
        return dict(result, reason='Missing valid verification timestamp.')
    if 'log_path' in item or 'log_sha256' in item:
        try:
            if evidence_root is None or not SHA256_RE.fullmatch(item.get('log_sha256', '')):
                raise ValueError('Invalid log reference.')
            log = _safe_path(evidence_root, item['log_path'])
            if not log.is_file() or _sha256(log) != item['log_sha256']:
                raise ValueError('Log digest differs.')
        except (OSError, ValueError, TypeError, KeyError):
            return dict(result, status='FAIL', reason='Evidence log is missing, unsafe, or differs from its digest.')
    if name == 'local_ai' and (item.get('qualified_model') is not True or item.get('inference_completed') is not True):
        return dict(result, reason='No qualified local model completed real inference.')
    if name in ('soak_30min', 'soak_2h'):
        duration = item.get('duration_seconds')
        needed = 1800 if name == 'soak_30min' else 7200
        if type(duration) not in (int, float) or not needed <= duration < float('inf'):
            return dict(result, reason='Required soak duration was not verified.')
    return dict(result, status='PASS', reason='Verified execution for this source candidate.')


def evaluate_release_gates(evidence, fingerprint, *, now=None, max_age_hours=72, evidence_root=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError('Use a timezone-aware clock and a positive evidence lifetime.')
    evidence = evidence if isinstance(evidence, dict) else {}
    entries = evidence.get('gates', {})
    entries = entries if isinstance(entries, dict) else {}
    gates = {name: _gate_result(name, entries.get(name), fingerprint, now,
             timedelta(hours=max_age_hours), evidence_root) for name in (*REQUIRED_GATES, *OPTIONAL_GATES)}
    required = [gates[name]['status'] for name in REQUIRED_GATES]
    status = 'FAIL' if 'FAIL' in required else 'NOT VERIFIED' if 'NOT VERIFIED' in required else 'PASS'
    return {'schema_version': 1, 'version': APP_VERSION, 'channel': RELEASE_CHANNEL,
            'source_fingerprint': fingerprint, 'generated_at': now.isoformat(), 'status': status,
            'gates': gates}


def write_release_report(report, output):
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output = _safe_path(output.parent, output.name)
    markdown = _safe_path(output.parent, output.with_suffix('.md').name)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    rows = [f"# SnapFlow {report['version']} release check", '', f"Overall: **{report['status']}**", '',
            '| Gate | Required | Status | Evidence |', '| --- | --- | --- | --- |']
    for name, gate in report['gates'].items():
        counts = ', '.join(f'{field}={gate[field]}' for field in ('exit_code', 'passed', 'failed', 'errors', 'skipped', 'duration_seconds') if field in gate)
        rows.append(f"| {name} | {'yes' if gate['required'] else 'no'} | {gate['status']} | {gate['reason']} {counts} |")
    rows += ['', f"Source fingerprint: `{report['source_fingerprint']}`", '',
             'Optional unverified gates remain visible and do not satisfy mandatory gates.', '']
    markdown.write_text('\n'.join(rows), encoding='utf-8')


def evaluate_agent_release_gates(evidence, fingerprint, *, now=None, max_age_hours=72, evidence_root=None):
    """Apply the same source, clock, command/count and log checks to Agent gates.

    Synthetic execution evidence does not establish installed application or
    live provider qualification; record those gates separately and explicitly.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError('Use a timezone-aware clock and a positive evidence lifetime.')
    evidence = evidence if isinstance(evidence, dict) else {}
    entries = evidence.get('gates', {})
    entries = entries if isinstance(entries, dict) else {}
    gates = {name: dict(_gate_result(name, entries.get(name), fingerprint, now,
        timedelta(hours=max_age_hours), evidence_root), required=True) for name in AGENT_REQUIRED_GATES}
    statuses = [gate['status'] for gate in gates.values()]
    status = 'FAIL' if 'FAIL' in statuses else 'NOT VERIFIED' if 'NOT VERIFIED' in statuses else 'PASS'
    return {'schema_version': 1, 'qualification': 'agent', 'version': APP_VERSION,
            'channel': RELEASE_CHANNEL, 'source_fingerprint': fingerprint,
            'generated_at': now.isoformat(), 'status': status, 'gates': gates}
