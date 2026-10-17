"""Published procedure catalog. Agent approval never substitutes for desktop review."""
from dataclasses import replace
import time

from src.agent.capabilities import AgentCapability
from src.agent.models import thaw
from .storage import content_hash


def input_schema(skill):
    properties, required = {}, []
    for spec in skill.inputs:
        kind = {'text': 'string', 'enum': 'string', 'date': 'string', 'time': 'string'}.get(spec.value_type, spec.value_type)
        entry = {'type': kind, 'description': spec.label}
        if kind == 'string':
            entry['maxLength'] = spec.max_length
        if spec.enum_values:
            entry['enum'] = list(spec.enum_values)
        properties[spec.input_id] = entry
        if spec.required and spec.default_value is None:
            required.append(spec.input_id)
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}


class AutomationSkillCapabilityProvider:
    def __init__(self, store, runtime, review):
        self.store, self.runtime, self.review = store, runtime, review

    def register(self, registry, session, bundle):
        # Selected Desktop Context preserves existing ephemeral Agent history and consent.
        if not any(s.source_type == 'desktop_ui' for s in bundle.sources):
            return
        try:
            fence = self.runtime._guard()
            if fence.session_id != session.session_id:
                return
        except Exception:
            return
        for skill in self.store.search(app=fence.allowed_process_name, published_only=True)[:20]:
            if len(registry._entries) >= 128:
                break
            external = any(s.risk_level not in ('ui_local_edit', 'ui_navigation') for s in skill.steps)
            cid = 'automation:' + skill.skill_id + ':' + skill.version_id
            description = ('Expanded procedure (untrusted labels, each action requires fresh desktop approval):\n' +
                '\n'.join(str(i + 1) + '. ' + s.title + ' · ' + s.action_type + ' · ' + s.risk_level for i, s in enumerate(skill.steps)))[:2000]
            descriptor = AgentCapability(cid, skill.name, description, 'run_skill', input_schema(skill), None,
                'external_write' if external else 'local_write', True, True, 'automation_skill:' + skill.version_id)
            expected = content_hash(skill)
            def live(skill=skill, expected=expected, descriptor=descriptor):
                try:
                    current = self.store.get(skill.version_id)
                    current_fence = self.runtime._guard()
                    if current_fence.session_id != session.session_id:
                        return None
                    allowed = current.status == 'published' and content_hash(current) == expected
                    allowed = allowed and (session.constraints.allow_external_writes if descriptor.risk_level == 'external_write' else session.constraints.allow_local_writes)
                    return replace(descriptor, availability=bool(allowed))
                except Exception:
                    return None
            def invoke(inputs, *, cancelled=None, guard=None, constraints=None, skill=skill, live=live):
                if guard is None:
                    raise PermissionError('Automation requires the live Agent guard.')
                cancelled = cancelled or (lambda: False)
                effective = constraints or session.constraints
                run = self.runtime.prepare(skill, inputs)
                started = time.perf_counter()
                external_commits = 0
                def boundary():
                    current = live()
                    if cancelled() or current is None or not current.availability or guard() is False:
                        raise PermissionError('Agent or skill permission changed.')
                try:
                    while run.index < len(skill.steps):
                        boundary()
                        if run.index >= effective.max_ui_actions:
                            raise PermissionError('Agent desktop action budget exhausted.')
                        preview = self.runtime.preview(run)
                        if not preview.allowed:
                            run.status = 'paused'
                            break
                        is_external = preview.risk_level not in ('ui_navigation', 'ui_local_edit')
                        if not (effective.allow_external_writes if is_external else effective.allow_local_writes):
                            raise PermissionError('This desktop action exceeds goal write permissions.')
                        if preview.risk_level == 'ui_external_commit':
                            if external_commits >= getattr(effective, 'max_external_commits', 0):
                                raise PermissionError('Agent external commit budget exhausted.')
                            external_commits += 1
                        token = self.review(preview, cancelled=cancelled, guard=boundary)
                        boundary()
                        if not token:
                            run.status = 'paused'
                            break
                        # Human returns to the fenced application after reviewing the prompt.
                        foreground = getattr(self.runtime.service.provider, 'foreground_matches', None)
                        deadline = time.monotonic() + 15
                        while foreground and not foreground(self.runtime.service.fence):
                            boundary()
                            if time.monotonic() >= deadline:
                                raise InterruptedError('Target application was not activated.')
                            time.sleep(.05)
                        receipt = self.runtime.execute_next(run, token, cancelled=cancelled, guard=boundary)
                        if receipt.status != 'success':
                            break
                except Exception:
                    run.status = 'interrupted' if run.results else 'paused'
                finally:
                    self.store.record_usage(run, (time.perf_counter() - started) * 1000)
                completed = run.status == 'completed'
                return {'success': completed, 'status': 'success' if completed else 'interrupted',
                    'desktop_verified': completed, 'step_count': len(run.results), 'action_id': run.run_id}
            registry.register(descriptor, invoke, refresh=live)
