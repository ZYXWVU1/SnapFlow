"""Register a skill here without changing controller or result-window dispatch."""
from .assignment import AssignmentSkill
from .event import EventSkill
from .code_error import CodeErrorSkill
from .table import TableSkill

SKILLS = {skill.id: skill for skill in (AssignmentSkill(), EventSkill(), CodeErrorSkill(), TableSkill())}
