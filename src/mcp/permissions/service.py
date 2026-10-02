"""Tool metadata never confers authority. Writes always need fresh approval."""
from src.mcp.client.models import valid_id, valid_tool_name


class MCPPermissionService:
    def __init__(self, storage):
        self.storage = storage

    def review(self, connection_id, name, fingerprint, *, risk, mode):
        valid_id(connection_id)
        valid_tool_name(name)
        if risk in ('destructive', 'unknown') and mode != 'disabled':
            raise ValueError('Destructive and unclassified tools must remain disabled.')
        self.storage.save_policy(f'mcp:{connection_id}:{name}',
            {'fingerprint': fingerprint, 'risk': risk, 'mode': mode})

    def policy(self, connection_id, name, fingerprint):
        policy = self.storage.get_policy(f'mcp:{connection_id}:{name}')
        if policy is None or policy['fingerprint'] != fingerprint:
            return {'fingerprint': fingerprint, 'risk': 'unknown', 'mode': 'disabled'}
        return policy

    def permits(self, connection_id, name, fingerprint, *, approved=False):
        policy = self.policy(connection_id, name, fingerprint)
        if policy['mode'] == 'disabled' or policy['risk'] in ('unknown', 'destructive'):
            return False
        if policy['mode'] == 'ask_every_time' or policy['risk'] != 'read_only':
            return approved is True
        return True
