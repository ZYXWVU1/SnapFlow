"""Revision-aware local index; return snapshots to callers."""
from copy import deepcopy


class WorkflowRegistry:
    def __init__(self, storage):
        self.storage = storage
        self._revision = -1
        self._index = {}

    def matching(self, skill_id):
        if self._revision != self.storage.revision:
            self._index = {}
            for workflow in self.storage.list_workflows():
                if workflow.enabled:
                    self._index.setdefault(workflow.trigger.skill_id, []).append(workflow)
            self._revision = self.storage.revision
        return deepcopy(self._index.get(skill_id, []))
