"""Explicit orchestration only. Application auto dispatch is a later milestone."""
from .registry import WorkflowRegistry


class WorkflowEngine:
    def __init__(self, storage, executor):
        self.registry = WorkflowRegistry(storage)
        self.executor = executor

    def matching(self, skill_id):
        return self.registry.matching(skill_id)

    def execute_matching(self, context, *, cancel=None, preview=False):
        return [self.executor.execute(w, context, cancel=cancel, preview=preview)
                for w in self.matching(context.skill_id)]
