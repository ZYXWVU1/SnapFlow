"""Dynamic ActionDefinitions. Dry runs prepare data and never invoke tools."""
from copy import deepcopy
import json
import re
import time
from src.skill_actions import ACTIONS, ActionDefinition, ActionResult
from src.workflows.effects import render_template
from src.mcp.client.discovery import bounded_json


def resolve(value, data):
    if isinstance(value, str):
        match = re.fullmatch(r'\{([a-z][a-z0-9_]*)\}', value)
        if match:
            if match.group(1) not in data:
                raise ValueError('Mapped Skill field is unavailable.')
            return deepcopy(data[match.group(1)])
        return render_template(value, data)
    if isinstance(value, list):
        return [resolve(item, data) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, data) for key, item in value.items()}
    return value


class MCPToolAction(ActionDefinition):
    def __init__(self, manager, descriptor):
        object.__setattr__(self, 'manager', manager)
        object.__setattr__(self, 'descriptor', deepcopy(descriptor))
        policy = manager.permissions.policy(descriptor.connection_id, descriptor.name, descriptor.fingerprint)
        super().__init__(descriptor.action_id, f'MCP · {descriptor.title}', 'mcp', lambda result: '',
            enabled=self.available, config_schema={'arguments': {'type': 'object'},
                'schema_fingerprint': {'type': 'string'}}, risk_level=policy['risk'])

    def available(self, result=None):
        current = next((t for t in self.manager.snapshot(self.descriptor.connection_id).tools
            if t.name == self.descriptor.name), None)
        return (current is not None and current.fingerprint == self.descriptor.fingerprint and
            self.manager.state(self.descriptor.connection_id).status in ('connected', 'permission_review_required'))

    def validate_config(self, config):
        if (not isinstance(config, dict) or set(config) != {'arguments', 'schema_fingerprint'} or
                not isinstance(config['arguments'], dict)):
            return ['MCP Action requires arguments and a schema fingerprint.']
        if config['schema_fingerprint'] != self.descriptor.fingerprint:
            return ['MCP tool schema changed. Review this Workflow step.']
        try:
            bounded_json(config['arguments'], 65536)
        except (ValueError, TypeError, RecursionError):
            return ['MCP arguments exceed the JSON limit.']
        return []

    def prepare(self, result, config):
        from jsonschema import Draft202012Validator
        errors = self.validate_config(config)
        if errors:
            raise ValueError(' '.join(errors))
        if not self.available():
            raise ValueError('MCP tool is unavailable. Reconnect and review this step.')
        arguments = resolve(config['arguments'], result.data)
        bounded_json(arguments, 65536)
        Draft202012Validator(self.descriptor.input_schema).validate(arguments)
        return arguments

    def preview(self, result, config):
        arguments = self.prepare(result, config)
        policy = self.manager.permissions.policy(self.descriptor.connection_id,
            self.descriptor.name, self.descriptor.fingerprint)
        return (f'Server: {self.descriptor.connection_id}\nTool: {self.descriptor.name}\n'
            f"Risk: {policy['risk']} · Permission: {policy['mode']}\n"
            + json.dumps(arguments, ensure_ascii=False, indent=2))

    def execute(self, result, config, *, approval=None, cancel=None, execution_guard=None):
        try:
            arguments = self.prepare(result, config)
            policy = self.manager.permissions.policy(self.descriptor.connection_id,
                self.descriptor.name, self.descriptor.fingerprint)
            if policy['mode'] == 'disabled' or policy['risk'] in ('unknown', 'destructive'):
                return ActionResult(False, 'MCP tool is disabled. Review its permissions in Extensions.')
            requires_approval = policy['mode'] == 'ask_every_time' or policy['risk'] != 'read_only'
            approved = requires_approval and approval is not None and approval(self.descriptor, arguments, policy) is True
            if requires_approval and not approved:
                return ActionResult(False, 'MCP tool approval was rejected or unavailable.')
            if (cancel is not None and cancel.is_set()) or (execution_guard is not None and not execution_guard()):
                return ActionResult(False, 'MCP execution cancelled or Workflow changed.')
            # Manager rechecks profile, live descriptor, policy and resolved arguments.
            future = self.manager.call_tool(self.descriptor.connection_id, self.descriptor.name, arguments,
                approved=approved, expected_fingerprint=self.descriptor.fingerprint,
                execution_guard=lambda: (cancel is None or not cancel.is_set()) and
                    (execution_guard is None or execution_guard()))
            deadline = time.monotonic() + 125
            while True:
                if cancel is not None and cancel.is_set():
                    future.cancel()
                    return ActionResult(False, 'MCP execution cancelled.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    future.cancel()
                    return ActionResult(False, 'MCP operation timed out.')
                try:
                    response = future.result(min(.1, remaining))
                    break
                except TimeoutError:
                    continue
            return ActionResult(response.success, response.category or ('Tool completed.' if response.success else 'Tool failed.'),
                kind='mcp', payload=response.text)
        except Exception:
            return ActionResult(False, 'MCP tool could not run. Check its schema, permissions and connection.')


def register_actions(manager, connection_id, actions=None):
    actions = ACTIONS if actions is None else actions
    for key in tuple(actions):
        if key.startswith(f'mcp:{connection_id}:'):
            actions.pop(key)
    for descriptor in manager.snapshot(connection_id).tools:
        actions[descriptor.action_id] = MCPToolAction(manager, descriptor)
