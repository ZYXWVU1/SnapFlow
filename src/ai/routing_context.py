"""Resolve current, comparable evaluation evidence for production AI routing.

No initialization creates data or probes hardware. Call these helpers in the
inference worker; absent or invalid evidence leaves the router's normal policy
and capability selection in control.
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from src.skills.custom.models import CustomSkillDefinition
from .hardware import HardwareProfileService


def hardware_identity(profile):
    """Preserve benchmark hardware hashes while excluding changing free RAM."""
    identity = {
        'os': profile.os_version, 'cpu': profile.cpu_name,
        'logical_cpu_count': profile.logical_cpu_count, 'total_ram_mb': profile.total_ram_mb,
        'gpu': sorted(((device.name, device.vendor or '', device.total_vram_mb)
                       for device in profile.gpu_devices),
                      key=lambda device: (device[0], device[1], -1 if device[2] is None else device[2])),
        'npu': sorted(str(getattr(device, 'name', device)) for device in profile.npu_devices),
        'providers': sorted(profile.available_execution_providers),
        'windows_ml': profile.windows_ml_available,
    }
    return 'hardware-sha256:' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode('utf-8')).hexdigest()


def collect_resources():
    """Return lightweight detected RAM and hardware identity, with safe unknowns."""
    resources = {'available_ram_mb': None, 'hardware_profile_id': None}
    try:
        profile = HardwareProfileService().detect()
        available = profile.available_ram_mb
        if type(available) is int and available >= 0:
            resources['available_ram_mb'] = available
        resources['hardware_profile_id'] = hardware_identity(profile)
    except Exception:
        # Hardware discovery is optional and must not prevent inference.
        pass
    return resources


def _identity(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _snapshot(definition, skill_id):
    """Validate a custom definition, comparing its complete supplied JSON shape."""
    if not isinstance(definition, dict) or definition.get('id') != skill_id:
        raise ValueError('Skill definition identity does not match.')
    CustomSkillDefinition.from_dict(definition)
    return json.dumps(definition, sort_keys=True, allow_nan=False)


class RoutingContextResolver:
    """Match stored profiles to live Phase 7 version and dataset records.

    Current definitions may equal a historical version after a restore. Snapshot
    equality, rather than a published-status label, determines comparability.
    Missing model revision strings intentionally disable measured routing.
    """
    def __init__(self, paths, performance_store):
        self.paths = paths
        self.performance_store = performance_store

    def resolve(self, skill_id, definition_snapshot, *, local_model_id, cloud_model_id,
                local_model_version, cloud_model_version, hardware_profile_id):
        """Return one current eligible pair's metadata, or an empty dictionary.

        Among comparable pairs, prefer the pair whose older measurement is most
        recent, then its newer measurement. Sample count and age remain governed
        by ``ModelPerformanceStore.eligible``.
        """
        if not all(_identity(value) for value in (skill_id, local_model_id, cloud_model_id,
                local_model_version, cloud_model_version, hardware_profile_id)):
            return {}
        try:
            snapshot = _snapshot(definition_snapshot, skill_id)
            path = Path(self.paths.learning_database).resolve()
            if not path.is_file() or self.performance_store is None:
                return {}
            # Storage constructors create tables and sometimes version records;
            # read the same Phase 7 shapes without those inference-time effects.
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                db.execute('BEGIN')
                versions = {version_id: payload for version_id, payload in db.execute(
                    'SELECT version_id, definition_snapshot FROM skill_versions WHERE skill_id = ?',
                    (skill_id,))}
                datasets = dict(db.execute(
                    'SELECT id, revision FROM evaluation_datasets WHERE skill_id = ?', (skill_id,)))
                return self._matching_pair(skill_id, snapshot, versions, datasets,
                    local_model_id, cloud_model_id, local_model_version, cloud_model_version,
                    hardware_profile_id)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            # Corrupt/unavailable optional metadata cannot establish model quality.
            return {}

    def _matching_pair(self, skill_id, snapshot, versions, datasets, local_model_id,
                       cloud_model_id, local_model_version, cloud_model_version, hardware_profile_id):
        store = self.performance_store
        pairs, checked = [], set()
        for profile in store.list_profiles(skill_id):
            if (profile.runtime_id != 'local_openai' or not profile.local or
                    profile.model_id != local_model_id or profile.model_version != local_model_version):
                continue
            key = (profile.skill_version_id, profile.dataset_id, profile.dataset_revision)
            if key in checked:
                continue
            checked.add(key)
            version_id, dataset_id, revision = key
            if datasets.get(dataset_id) != revision or version_id not in versions:
                continue
            try:
                stored_snapshot = _snapshot(json.loads(versions[version_id]), skill_id)
            except (ValueError, TypeError, KeyError, RecursionError):
                continue
            if snapshot != stored_snapshot:
                continue
            local = store.eligible('local_openai', local_model_id, skill_id, revision,
                local_model_version, hardware_profile_id, skill_version_id=version_id, dataset_id=dataset_id)
            cloud = store.eligible('configured_cloud', cloud_model_id, skill_id, revision,
                cloud_model_version, hardware_profile_id, skill_version_id=version_id, dataset_id=dataset_id)
            if local is None or cloud is None or not local.local or cloud.local:
                continue
            dates = sorted((datetime.fromisoformat(local.created_at), datetime.fromisoformat(cloud.created_at)))
            pairs.append((tuple(dates), version_id, dataset_id, revision))
        if not pairs:
            return {}
        _dates, version_id, dataset_id, revision = max(pairs)
        return {
            'skill_id': skill_id, 'skill_version_id': version_id,
            'dataset_id': dataset_id, 'dataset_revision': revision,
            'model_versions': {'local_openai': local_model_version, 'configured_cloud': cloud_model_version},
            'hardware_profile_id': hardware_profile_id,
        }
