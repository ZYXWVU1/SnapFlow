"""Closed, local UX flags. Privacy and permission enforcement are never flags."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from types import MappingProxyType

from src.paths import AppPaths
from src.reliability.policy import get_safety_policy


def require_plain_path(path):
    """Reject symlinks and Windows reparse points in every existing ancestor."""
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Choose a regular local file and folder.')
    return path


@dataclass(frozen=True)
class FeatureFlag:
    id: str
    default_enabled: bool
    beta: bool
    restart_required: bool
    security_sensitive: bool
    title: str
    description: str
    limitation: str
    beta_default_enabled: bool = False


FLAGS = MappingProxyType({
    'demonstration_automation_v1': FeatureFlag('demonstration_automation_v1', False, True, False, True,
        'Demonstration Automation', 'Explicitly teach, review, test and reuse semantic desktop tasks.',
        'Beta: selected windows only; structured actions; each action needs fresh approval.'),
    'controlled_computer_use_v1': FeatureFlag('controlled_computer_use_v1', False, True, False, True,
        'Controlled Desktop Actions', 'Review and approve structured actions in a selected Windows application.',
        'Beta: foreground only, protected/destructive actions blocked, input fallback unavailable.'),
    'agent_missions_v1': FeatureFlag('agent_missions_v1', False, True, False, False,
        'Agent Missions', 'Plan, review, and explicitly run goal-based missions.',
        'Beta: read-only capabilities first; execution requires validated plans and approvals.'),
    'beta_insights': FeatureFlag('beta_insights', False, True, False, False,
        'Beta Insights', 'Show your safe local usage totals and operation timings.',
        'Requires local reliability records; historical totals may be incomplete.'),
    'feedback_wording': FeatureFlag('feedback_wording', False, True, False, False,
        'Feedback wording experiment', 'Try one of two labels for the feedback button.',
        'Requires explicit experiment participation and local reliability records.'),
})


class BetaFeatureService:
    def __init__(self, paths=None, *, policy=None, release_channel='stable'):
        self.paths = paths or AppPaths()
        self.policy = policy or get_safety_policy()
        self.channel = release_channel if release_channel in {'stable', 'beta', 'dev'} else 'stable'
        self.path = self.paths.data_dir / 'beta_features.json'
        self.overrides, self.assignments = {}, {}
        self.experiments_enabled = False
        self.warning = ''
        try:
            require_plain_path(self.path)
            if self.path.exists():
                if self.path.stat().st_size > 16384:
                    raise ValueError('Oversized beta settings.')
                payload = json.loads(self.path.read_text(encoding='utf-8'))
                if type(payload) is not dict or set(payload) != {'version', 'overrides', 'experiments_enabled', 'assignments'}:
                    raise ValueError('Invalid beta settings.')
                if type(payload['version']) is not int or payload['version'] != 1:
                    raise ValueError('Unsupported beta settings.')
                self._validate(payload['overrides'], payload['experiments_enabled'])
                assignments = payload['assignments']
                if type(assignments) is not dict or any(k != 'feedback_wording' or v not in ('A', 'B') for k, v in assignments.items()):
                    raise ValueError('Invalid local experiment assignment.')
                self.overrides = dict(payload['overrides'])
                self.experiments_enabled = payload['experiments_enabled']
                self.assignments = dict(assignments) if self.experiments_enabled else {}
        except (OSError, ValueError, TypeError, RecursionError):
            self.warning = 'Beta settings could not be read. Beta features are off; the original file is preserved.'

    @property
    def registry(self):
        return FLAGS

    @staticmethod
    def _validate(overrides, experiments_enabled):
        if type(overrides) is not dict or any(k not in FLAGS or type(v) is not bool for k, v in overrides.items()):
            raise ValueError('Only registered beta features accept true or false values.')
        if type(experiments_enabled) is not bool:
            raise ValueError('Experiment participation must be true or false.')

    def configured(self, feature_id):
        flag = FLAGS.get(feature_id)
        if flag is None:
            return False
        default = flag.beta_default_enabled if self.channel == 'beta' else flag.default_enabled
        return self.overrides.get(feature_id, default)

    def enabled(self, feature_id):
        return self.policy.allows('beta_feature') and self.configured(feature_id)

    def require(self, feature_id):
        if not self.enabled(feature_id):
            raise PermissionError('Enable this beta feature in Settings and restart outside Safe Mode.')

    def save(self, overrides, *, experiments_enabled):
        self._validate(overrides, experiments_enabled)
        assignments = dict(self.assignments) if experiments_enabled else {}
        payload = {'version': 1, 'overrides': overrides, 'experiments_enabled': experiments_enabled,
                   'assignments': assignments}
        self._write(payload)
        self.overrides, self.experiments_enabled, self.assignments = dict(overrides), experiments_enabled, assignments
        self.warning = ''

    def _write(self, payload):
        require_plain_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.beta-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            require_plain_path(self.path)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def variant(self, experiment_id, *, analytics_enabled):
        if experiment_id != 'feedback_wording' or not self.experiments_enabled or analytics_enabled is not True or not self.enabled(experiment_id):
            return None
        if experiment_id not in self.assignments:
            assignment = secrets.choice(('A', 'B'))
            assignments = {**self.assignments, experiment_id: assignment}
            try:
                self._write({'version': 1, 'overrides': self.overrides,
                             'experiments_enabled': True, 'assignments': assignments})
            except (OSError, ValueError):
                self.warning = 'Local experiment assignment could not be saved. The default interface is in use.'
                return None
            self.assignments = assignments
        return self.assignments[experiment_id]
