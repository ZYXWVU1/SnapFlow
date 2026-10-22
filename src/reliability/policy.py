from threading import RLock


class SafetyPolicy:
    _RESTRICTED = frozenset(('external_write', 'workflow_auto', 'mcp_auto', 'model_preload',
                            'cloud_fallback', 'extension_server', 'beta_feature', 'agent_execution', 'agent_resume',
                            'desktop_assistance', 'desktop_uia', 'desktop_actions', 'desktop_input_fallback',
                            'browser_companion'))
    _CORE = frozenset(('backup', 'diagnostics', 'settings', 'memory_inspection', 'skill_inspection'))

    def __init__(self):
        self._lock = RLock()
        self._safe_mode = False

    @property
    def safe_mode(self):
        with self._lock:
            return self._safe_mode

    def set_safe_mode(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Safe Mode must be true or false.')
        with self._lock:
            self._safe_mode = enabled

    def allows(self, operation):
        if operation not in self._RESTRICTED | self._CORE:
            return False
        return operation in self._CORE or not self.safe_mode

    def require(self, operation):
        if not self.allows(operation):
            raise PermissionError('Safe Mode blocks this operation. Restart normally to enable it.')


_POLICY = SafetyPolicy()


def get_safety_policy():
    return _POLICY
