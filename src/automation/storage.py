"""Atomic local versions; mutable lifecycle evidence is separate from immutable definitions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from uuid import uuid4

from src.agent.models import digest
from src.beta.flags import require_plain_path
from src.desktop_actions.models import now
from .models import AutomationSkill, TargetTemplate
from .validation import validate_definition

MAX_BYTES = 8 * 1024 * 1024


def content_hash(skill):
    data = skill.to_dict()
    data.pop('status')
    return digest(data)


class AutomationSkillStore:
    def __init__(self, path):
        self.path = require_plain_path(path)
        self.lock = RLock()
        self.records, self.usage = {}, []
        if self.path.exists():
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError('Automation library exceeds its size limit. Original preserved.')
            data = self.validate_payload(json.loads(self.path.read_text(encoding='utf-8')))
            self.records, self.usage = data['versions'], data['usage']

    @staticmethod
    def validate_payload(data):
        if type(data) is not dict or set(data) != {'format_version', 'versions', 'usage'} or data['format_version'] != 1:
            raise ValueError('Unsupported automation library.')
        if type(data['versions']) is not dict or len(data['versions']) > 256 or type(data['usage']) is not list or len(data['usage']) > 500:
            raise ValueError('Automation library exceeds supported bounds.')
        for version_id, record in data['versions'].items():
            if type(record) is not dict or set(record) != {'definition', 'status', 'reviewed_hash', 'test'}:
                raise ValueError('Invalid version record.')
            skill = AutomationSkill.from_dict(record['definition'])
            validate_definition(skill)
            if version_id != skill.version_id or record['status'] not in ('draft', 'tested', 'published', 'disabled', 'archived'):
                raise ValueError('Invalid version identity/status.')
            if record['reviewed_hash'] not in (None, content_hash(skill)):
                raise ValueError('Review evidence does not match the version.')
            evidence = record['test']
            if evidence is not None and (type(evidence) is not dict or set(evidence) != {'definition_hash', 'tested_at', 'step_count', 'verified', 'target_count'} or
                    evidence['definition_hash'] != content_hash(skill) or evidence['verified'] is not True or
                    evidence['step_count'] != len(skill.steps) or evidence['target_count'] != len(skill.steps)):
                raise ValueError('Invalid version test evidence.')
            if record['status'] in ('tested', 'published'):
                validate_definition(skill, publish=True)
                if record['reviewed_hash'] != content_hash(skill) or evidence is None:
                    raise ValueError('Published/tested versions require matching review and test evidence.')
        for entry in data['usage']:
            if type(entry) is not dict or set(entry) != {'skill_id', 'version_id', 'timestamp', 'status', 'duration_ms'} or entry['status'] not in ('completed', 'paused', 'stopped', 'interrupted'):
                raise ValueError('Invalid safe automation history.')
            from .models import identifier
            identifier(entry['skill_id'])
            identifier(entry['version_id'])
            if type(entry['duration_ms']) not in (int, float) or not 0 <= entry['duration_ms'] <= 86400000:
                raise ValueError('Invalid usage duration.')
        return data

    def _write(self, records, usage=None):
        usage = self.usage if usage is None else usage
        payload = self.validate_payload({'format_version': 1, 'versions': records, 'usage': usage})
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)
        if len(text.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Automation library exceeds its size limit.')
        require_plain_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.automation-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            require_plain_path(self.path)
            os.replace(temporary, self.path)
            self.records, self.usage = records, usage
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, version_id):
        with self.lock:
            record = self.records[version_id]
            return replace(AutomationSkill.from_dict(record['definition']), status=record['status'])

    def versions(self, skill_id=None):
        return tuple(self.get(key) for key in self.records if skill_id is None or self.records[key]['definition']['skill_id'] == skill_id)

    def search(self, query='', *, app=None, published_only=False):
        query = query.strip().casefold()
        result = []
        for skill in self.versions():
            text = ' '.join((skill.name, skill.description, *skill.supported_apps, *skill.tags, *(i.label for i in skill.inputs)))
            if query not in text.casefold() or published_only and skill.status != 'published':
                continue
            if app and app.casefold() not in {p.casefold() for p in skill.supported_apps}:
                continue
            result.append(skill)
        return tuple(result)

    def save_draft(self, skill):
        with self.lock:
            validate_definition(skill)
            if skill.version_id in self.records and self.records[skill.version_id]['status'] not in ('draft', 'tested'):
                raise ValueError('Published/imported/archived definitions are immutable. Create a draft version.')
            if skill.status not in ('draft', 'tested'):
                raise ValueError('Save a draft definition only.')
            skill = replace(skill, status='draft')
            records = dict(self.records)
            records[skill.version_id] = {'definition': skill.to_dict(), 'status': 'draft', 'reviewed_hash': None, 'test': None}
            self._write(records)
            from src.observability.performance import emit
            emit('automation_skill_created', component='skill', properties={'step_count': len(skill.steps)})
            return skill

    def _update(self, version_id, **updates):
        records = dict(self.records)
        records[version_id] = {**records[version_id], **updates}
        self._write(records)

    def review(self, version_id):
        with self.lock:
            skill = self.get(version_id)
            if skill.status not in ('draft', 'tested') or skill.trust == 'imported_unreviewed':
                raise ValueError('Create a draft and review every step first.')
            validate_definition(skill, publish=True)
            self._update(version_id, reviewed_hash=content_hash(skill))

    def record_test(self, run, previews):
        with self.lock:
            skill = self.get(run.skill.version_id)
            record = self.records[skill.version_id]
            if skill.status not in ('draft', 'tested') or record['reviewed_hash'] != content_hash(run.skill) or content_hash(skill) != content_hash(run.skill):
                raise ValueError('Review this exact version before testing.')
            if run.status != 'completed' or len(run.results) != len(skill.steps) or any(not r.get('success') or not r.get('desktop_verified') for r in run.results):
                raise ValueError('Every required step must execute and verify successfully.')
            if len(previews) != len(skill.steps) or not all(p.allowed for p in previews):
                raise ValueError('Every target must pass dry-run validation.')
            self._update(skill.version_id, status='tested', test={'definition_hash': content_hash(skill), 'tested_at': now(),
                'step_count': len(skill.steps), 'verified': True, 'target_count': len(previews)})
            from src.observability.performance import emit
            emit('automation_skill_tested', component='skill', properties={'step_count': len(skill.steps)})

    def publish(self, version_id):
        with self.lock:
            skill = self.get(version_id)
            validate_definition(skill, publish=True)
            record = self.records[version_id]
            if skill.status != 'tested' or not record['test'] or record['reviewed_hash'] != content_hash(skill):
                raise ValueError('Review, dry run, and execute a verified test of this exact version before publishing.')
            self._update(version_id, status='published')
            return self.get(version_id)

    def create_draft(self, version_id):
        skill = self.get(version_id)
        return self.save_draft(replace(skill, version_id='version_' + uuid4().hex, status='draft', updated_at=now(),
            trust='imported_reviewed' if skill.trust.startswith('imported') else 'personal',
            steps=tuple(replace(s, reviewed=False) for s in skill.steps)))

    restore = create_draft

    def repair(self, version_id, step_id, target):
        if not isinstance(target, TargetTemplate):
            raise ValueError('Choose a semantic replacement target.')
        original = self.get(version_id)
        from src.observability.performance import emit
        emit('automation_skill_repair_required', component='skill')
        if not any(s.step_id == step_id for s in original.steps):
            raise ValueError('Unknown step.')
        draft = self.create_draft(version_id)
        return self.save_draft(replace(draft, steps=tuple(replace(s, target_template=target,
            preconditions=(), postconditions=tuple(replace(c, target=target) if c.target == s.target_template else c for c in s.postconditions),
            reviewed=False) if s.step_id == step_id else s for s in draft.steps)))

    def compare(self, left, right):
        a, b = self.get(left), self.get(right)
        result = {}
        for label, first, second in (('inputs', a.inputs, b.inputs), ('steps', tuple((s.step_id, s.title, s.action_type, s.dependencies) for s in a.steps), tuple((s.step_id, s.title, s.action_type, s.dependencies) for s in b.steps)),
                ('targets', tuple(s.target_template for s in a.steps), tuple(s.target_template for s in b.steps)),
                ('risk', a.risk_summary, b.risk_summary), ('values', tuple(s.value_template for s in a.steps), tuple(s.value_template for s in b.steps)),
                ('verification', (a.preconditions, a.postconditions, tuple(s.postconditions for s in a.steps)), (b.preconditions, b.postconditions, tuple(s.postconditions for s in b.steps)))):
            if first != second:
                result[label] = 'Changed'
        return result

    def set_status(self, version_id, status):
        if status not in ('archived', 'disabled'):
            raise ValueError('Use the review/test/publish lifecycle to enable a version.')
        with self.lock:
            self._update(version_id, status=status)

    def delete(self, skill_id):
        with self.lock:
            self._write({k: r for k, r in self.records.items() if r['definition']['skill_id'] != skill_id},
                [r for r in self.usage if r['skill_id'] != skill_id])

    def record_usage(self, run, duration_ms):
        with self.lock:
            status = run.status if run.status in ('completed', 'paused', 'stopped') else 'interrupted'
            entry = {'skill_id': run.skill.skill_id, 'version_id': run.skill.version_id,
                'timestamp': now(), 'status': status, 'duration_ms': max(0, min(86400000, duration_ms))}
            self._write(dict(self.records), [*self.usage, entry][-500:])
            from src.observability.performance import emit
            emit('automation_skill_run', component='skill', success=status == 'completed', properties={'step_count': len(run.results)})

    def export(self, version_id, *, include_literals=False):
        skill = self.get(version_id)
        if not include_literals:
            from .parameters import parameterize, references
            skill = replace(skill, status='draft')
            for index, step in enumerate(skill.steps):
                if step.action_type == 'set_value' and not references(step.value_template):
                    skill = parameterize(skill, step.step_id, 'export_value_' + str(index), 'Value for ' + step.title)
            skill = replace(skill, inputs=tuple(replace(i, default_value=None) for i in skill.inputs))
        return {'format': 'snapauto', 'format_version': 1, 'definition': skill.to_dict()}

    def import_definition(self, payload):
        if type(payload) is not dict or set(payload) != {'format', 'format_version', 'definition'} or payload['format'] != 'snapauto' or payload['format_version'] != 1:
            raise ValueError('Unsupported .snapauto definition.')
        skill = validate_definition(AutomationSkill.from_dict(payload['definition']))
        skill = replace(skill, skill_id='skill_' + uuid4().hex, version_id='version_' + uuid4().hex,
            status='disabled', trust='imported_unreviewed', steps=tuple(replace(s, reviewed=False) for s in skill.steps))
        with self.lock:
            records = dict(self.records)
            records[skill.version_id] = {'definition': skill.to_dict(), 'status': 'disabled', 'reviewed_hash': None, 'test': None}
            self._write(records)
        return skill
