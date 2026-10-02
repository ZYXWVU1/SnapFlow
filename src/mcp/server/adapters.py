"""Thin adapters to existing services. Tools and resources share policy checks."""
import base64
import inspect
import json
from dataclasses import replace
import re
import threading
from src.diagnostics import redact_text
from src.skills.registry import SKILLS
from src.mcp.client.discovery import bounded_json

_SECRET_VALUE = re.compile(r'''(?ix)(["']?(?:access[_ -]?token|refresh[_ -]?token|password|api[_ -]?key|client[_ -]?secret|secret|credential)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}\]]+)''')


def safe_data(value, depth=0):
    if depth > 24:
        raise ValueError('Service result too deeply nested.')
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:1000]:
            key = str(key)[:128]
            compact = re.sub('[^a-z0-9]', '', key.lower())
            if any(word in compact for word in ('apikey', 'token', 'password', 'credential', 'secret',
                    'screenshotreference', 'thumbnailreference', 'filepath')):
                result[key] = '[REDACTED]'
            else:
                result[key] = safe_data(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [safe_data(item, depth + 1) for item in value[:1000]]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(lambda match: match[1] + '"[REDACTED]"', redact_text(value[:16000]))
    if value is None or type(value) in (bool, int, float):
        return value
    raise ValueError('Service returned unsupported content.')


class SnapFlowServiceAdapter:
    def __init__(self, policy, memory, skills, workflows, history):
        self.policy = policy
        self.memory, self.skills, self.workflows, self.history = memory, skills, workflows, history
        self.approve_workflow = None
        self.execute_workflow = None
        self._execution_lock = threading.Lock()
        self._request = threading.local()
        self._methods = {name: getattr(self, name) for name in ('search_visual_memory', 'get_memory_record',
            'list_visual_skills', 'get_visual_skill', 'list_workflows', 'get_workflow',
            'get_workflow_history', 'search_workflow_history', 'resource', 'ping',
            'preview_workflow', 'run_workflow')}

    def __call__(self, operation, arguments):
        return self.call(operation, arguments)

    def call_cancellable(self, operation, arguments, cancel):
        self._request.cancel = cancel
        try:
            return self.call(operation, arguments)
        finally:
            del self._request.cancel

    def ping(self):
        from src.mcp.identity import instance_marker
        return {'name': 'SnapFlow', 'available': True, 'instance_marker': instance_marker(self.policy.path.parent)}

    def _workflow_input(self, workflow_id, structured_context):
        from src.workflows.context import WorkflowContext
        from src.workflows.validator import skill_fields
        self._require('share_workflows')
        self._identifier(workflow_id)
        bounded_json(structured_context, 32768)
        if not isinstance(structured_context, dict):
            raise ValueError('Structured context must be an object of Skill fields.')
        # Validate depth before existing Skill normalization sees host input.
        safe_data(structured_context)
        workflow = self.workflows.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError('Workflow unavailable.')
        if workflow.trigger.skill_id not in SKILLS:
            self._require('share_custom_skills')
        skill = self.skills.get(workflow.trigger.skill_id)
        if skill is None or set(structured_context) - set(skill_fields(skill)):
            raise ValueError('Context fields do not match the Workflow Skill.')
        result = skill.parse(json.dumps(structured_context, allow_nan=False), 0.0)
        result = replace(result, warnings=['External host context; not a verified screenshot.'] + result.warnings)
        return workflow, WorkflowContext.from_result(result)

    def _preview(self, workflow, context):
        from src.skill_actions import ACTIONS
        from src.workflows.executor import WorkflowExecutor
        from src.workflows.conditions import evaluate_conditions
        result = WorkflowExecutor(self.skills).execute(workflow, context, preview=True)
        return {'workflow_id': workflow.id, 'workflow_name': workflow.name, 'preview': True,
            'status': result.status, 'conditions_match': evaluate_conditions(workflow.conditions, context.data),
            'conditions': [{'field': c.field, 'operator': c.operator, 'value': c.value} for c in workflow.conditions],
            'resolved_inputs': context.data, 'error': result.error,
            'steps': [{'id': s.step_id, 'action_id': s.action_id, 'status': s.status,
                'risk': ACTIONS[s.action_id].risk_level if s.action_id in ACTIONS else 'unknown',
                'label': ACTIONS[s.action_id].label if s.action_id in ACTIONS else s.action_id,
                'preview': s.preview, 'error': s.error} for s in result.steps]}

    def preview_workflow(self, workflow_id, structured_context):
        workflow, context = self._workflow_input(workflow_id, structured_context)
        return self._preview(workflow, context)

    def run_workflow(self, workflow_id, structured_context):
        self._require('allow_workflow_execution')
        if self.approve_workflow is None or self.execute_workflow is None:
            raise PermissionError('Approval Required / Unavailable.')
        if not self._execution_lock.acquire(blocking=False):
            raise PermissionError('Another MCP Workflow request is active.')
        try:
            workflow, context = self._workflow_input(workflow_id, structured_context)
            preview = self._preview(workflow, context)
            if not workflow.enabled or preview['status'] != 'success':
                raise ValueError('Workflow is disabled or cannot execute with this context.')
            policy, revision = self.policy.snapshot(), self.policy.revision
            cancel = getattr(self._request, 'cancel', None) or threading.Event()
            def current():
                return not cancel.is_set() and self.policy.snapshot() == policy and self.policy.revision == revision and self.workflows.get_workflow(workflow_id) == workflow
            if not current() or not self.approve_workflow(safe_data(preview), cancel, current):
                raise PermissionError('Workflow approval rejected or unavailable.')
            if not current():
                raise PermissionError('Workflow or server permissions changed after approval.')
            execution = self.execute_workflow(workflow, context, cancel, current)
            return {'execution_id': execution.execution_id, 'workflow_id': workflow.id, 'status': execution.status,
                'steps': [{'id': s.step_id, 'action_id': s.action_id, 'status': s.status} for s in execution.steps]}
        finally:
            self._execution_lock.release()

    def call(self, operation, arguments):
        initial, revision = self.policy.snapshot(), self.policy.revision
        if not initial.enabled:
            raise PermissionError('SnapFlow MCP server is disabled.')
        bounded_json(arguments, 65536)
        method = self._methods.get(operation)
        if method is None or not isinstance(arguments, dict):
            raise ValueError('Unsupported server operation.')
        try:
            inspect.signature(method).bind(**arguments)
        except TypeError:
            raise ValueError('Unexpected or missing server arguments.') from None
        result = method(**arguments)
        if self.policy.snapshot() != initial or self.policy.revision != revision:
            raise PermissionError('Server permissions changed during the request.')
        # This binary envelope is created only by our image adapter after both
        # sharing checks. Text sanitization must not corrupt its base64 payload.
        if operation == 'resource' and isinstance(result, dict) and set(result) == {'mime_type', 'image_base64'}:
            result = dict(result)
        else:
            result = safe_data(result)
        bounded_json(result, 524288)
        return result

    def _require(self, name):
        if not self.policy.snapshot().enabled or not getattr(self.policy.snapshot(), name):
            raise PermissionError('Sharing is disabled for this capability.')

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Limit must be between 1 and 100.')

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', value):
            raise ValueError('Invalid record identifier.')

    def search_visual_memory(self, query, limit=10):
        self._require('share_memory')
        self._limit(limit)
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError('Search query is too long.')
        return [{'memory_id': row.memory_id, 'title': row.title, 'snippet': row.snippet,
            'skill_type': row.skill_name, 'saved_at': row.saved_at} for row in self.memory.search(query, limit=limit)]

    def get_memory_record(self, memory_id):
        self._require('share_memory')
        self._identifier(memory_id)
        record = self.memory.get_record(memory_id)
        if record is None:
            raise ValueError('Memory record unavailable.')
        return {'id': record.id, 'title': record.title, 'description': record.description,
            'source_type': record.source_type, 'skill_id': record.skill_id,
            'structured_data': record.structured_data, 'tags': record.tags, 'saved_at': record.saved_at}

    def list_visual_skills(self):
        result = [self.get_visual_skill(skill_id) for skill_id in SKILLS]
        if self.policy.snapshot().share_custom_skills:
            result.extend(self.get_visual_skill(item.id) for item in self.skills.enabled_definitions())
        return result[:128]

    def get_visual_skill(self, skill_id):
        self._identifier(skill_id)
        if skill_id in SKILLS:
            skill = self.skills.get(skill_id)
            return {'id': skill.id, 'name': skill.title, 'schema': skill.schema, 'type': 'built_in'}
        self._require('share_custom_skills')
        definition = next((s for s in self.skills.enabled_definitions() if s.id == skill_id), None)
        if definition is None:
            raise ValueError('Skill unavailable.')
        return {'id': definition.id, 'name': definition.name, 'type': 'custom',
            'fields': [{'id': f.id, 'label': f.label, 'type': f.field_type, 'required': f.required}
                for f in definition.fields]}

    def list_workflows(self):
        self._require('share_workflows')
        return [self.get_workflow(item.id) for item in self.workflows.list_workflows()[:128]]

    def get_workflow(self, workflow_id):
        self._require('share_workflows')
        self._identifier(workflow_id)
        workflow = self.workflows.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError('Workflow unavailable.')
        return {'id': workflow.id, 'name': workflow.name, 'enabled': workflow.enabled,
            'trigger': {'type': workflow.trigger.type, 'skill_id': workflow.trigger.skill_id},
            'steps': [{'id': step.id, 'action_id': step.action_id, 'enabled': step.enabled} for step in workflow.steps],
            'conditions': [{'field': c.field, 'operator': c.operator} for c in workflow.conditions]}

    def get_workflow_history(self, limit=10):
        self._require('share_history')
        self._limit(limit)
        return self.history.list_entries()[-limit:]

    def search_workflow_history(self, query, limit=10):
        self._require('share_history')
        self._limit(limit)
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError('Search query is too long.')
        return [entry for entry in self.history.list_entries() if query.casefold() in
            (entry['workflow_name'] + ' ' + entry['status']).casefold()][-limit:]

    def resource(self, uri):
        if not isinstance(uri, str) or len(uri) > 2048:
            raise ValueError('Invalid resource URI.')
        if uri == 'snapflow://workflow-history/recent':
            return self.get_workflow_history()
        matched = re.fullmatch(r'snapflow://(memory|skill|workflow)/([A-Za-z0-9_-]{1,128})(/image)?', uri)
        if matched is None:
            raise ValueError('Unsupported resource URI.')
        kind, identifier, image = matched.groups()
        if image:
            self._require('share_memory')
            self._require('share_images')
            if kind != 'memory':
                raise ValueError('Unsupported image resource.')
            record = self.memory.get_record(identifier)
            if record is None:
                raise ValueError('Memory unavailable.')
            path = self.memory.image_path(record)
            if path is None or not path.is_file() or path.stat().st_size > 262144:
                raise ValueError('Image unavailable or exceeds sharing limit.')
            data = path.read_bytes()
            if len(data) > 262144:
                raise ValueError('Image exceeds sharing limit.')
            return {'mime_type': 'image/png', 'image_base64': base64.b64encode(data).decode('ascii')}
        return {'memory': self.get_memory_record, 'skill': self.get_visual_skill,
            'workflow': self.get_workflow}[kind](identifier)
