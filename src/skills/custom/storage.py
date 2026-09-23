"""Atomic local persistence with lossless corrupt-file recovery."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import uuid
from .models import CustomSkillDefinition


def default_path():
    root = Path(os.environ.get('APPDATA') or Path.home() / '.local' / 'share')
    return root / 'AI Screenshot Helper' / 'custom_skills.json'


class CustomSkillStorage:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()
        self.warning = ''
        self._skills = {}
        self._recover = False
        self._readonly = False
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1:
                self._readonly = True
                raise ValueError('Unsupported storage version.')
            if not isinstance(raw.get('skills'), list):
                raise ValueError('Invalid skill list.')
            for entry in raw['skills']:
                try:
                    skill = CustomSkillDefinition.from_dict(entry)
                    if skill.id in self._skills:
                        raise ValueError('Duplicate ID.')
                    self._skills[skill.id] = skill
                except ValueError:
                    self._recover = True
            if self._recover:
                self.warning = 'Some invalid skill definitions were skipped. The original file will be backed up on save.'
        except FileNotFoundError:
            pass
        except OSError:
            self._readonly = True
            self.warning = 'Unable to read custom skills. Check file permissions and reopen the application.'
        except (ValueError, RecursionError):
            self._recover = True
            self.warning = ('Unsupported custom skills storage version; changes are disabled.' if self._readonly else
                            'Custom skills file is damaged. The original file will be backed up on save.')

    def list_skills(self):
        return list(self._skills.values())

    def get_skill(self, skill_id):
        return self._skills.get(skill_id)

    def _commit(self, skills):
        if self._readonly:
            raise ValueError(self.warning)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({'version': 1, 'skills': [s.to_dict() for s in skills.values()]}, ensure_ascii=False, indent=2)
        fd, name = tempfile.mkstemp(prefix='custom_skills_', suffix='.tmp', dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            for entry in json.loads(temporary.read_text(encoding='utf-8'))['skills']:
                CustomSkillDefinition.from_dict(entry)
            if self._recover and self.path.exists():
                backup = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.bak')
                backup.write_bytes(self.path.read_bytes())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._skills = skills
        self._recover = False
        self.warning = ''

    def create_skill(self, skill):
        if skill.id in self._skills:
            raise ValueError('A skill with this ID already exists.')
        self.save_skill(skill)

    def save_skill(self, skill):
        skill = CustomSkillDefinition.from_dict(skill.to_dict())
        self._commit(self._skills | {skill.id: skill})

    def delete_skill(self, skill_id):
        if skill_id not in self._skills:
            raise ValueError('Skill no longer exists.')
        self._commit({k: v for k, v in self._skills.items() if k != skill_id})

    def set_enabled(self, skill_id, enabled):
        skill = self.get_skill(skill_id)
        if skill is None:
            raise ValueError('Skill no longer exists.')
        self.save_skill(replace(skill, enabled=enabled))
