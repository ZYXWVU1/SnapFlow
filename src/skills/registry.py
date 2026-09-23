"""Register a skill here without changing controller or result-window dispatch."""
from .assignment import AssignmentSkill
from .event import EventSkill
from .code_error import CodeErrorSkill
from .table import TableSkill

SKILLS = {skill.id: skill for skill in (AssignmentSkill(), EventSkill(), CodeErrorSkill(), TableSkill())}


class SkillRegistry:
    """Built-ins have priority; custom adapters are fresh immutable snapshots."""
    def __init__(self, storage):
        self.storage = storage

    def enabled_definitions(self):
        return [s for s in self.storage.list_skills() if s.enabled]

    def get(self, skill_id):
        if skill_id in SKILLS:
            return SKILLS[skill_id]
        from .custom.runtime_skill import RuntimeCustomSkill
        definition = self.storage.get_skill(skill_id)
        return RuntimeCustomSkill(definition) if definition and definition.enabled else None
