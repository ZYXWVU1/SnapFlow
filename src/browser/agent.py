"""Inventoried browser capabilities; the extension retains exact action approval."""
from collections import Counter
from dataclasses import replace
import json

from src.agent.capabilities import AgentCapability, AgentInvocationStopped
from src.agent.models import digest, thaw
from .action_policy import validate_spec


class BrowserActionCapabilityProvider:
    def __init__(self, gateway_provider):
        self.gateway_provider = gateway_provider

    def _live(self, source, *, interact=True, expected_inventory=None):
        gateway = self.gateway_provider()
        if gateway is None:
            raise PermissionError('Browser actions are unavailable.')
        manager = gateway.manager
        with manager.lock:
            manager.context.validate_sources((source,))
            data = json.loads(source.content)
            session_id = data['browser_session_id']
            snapshot = manager.context.latest(session_id)
            if expected_inventory is not None and digest(snapshot.data) != expected_inventory:
                raise PermissionError('The reviewed browser control inventory changed. Review sources again.')
            session = manager.session_store.require(source.connection_id, session_id,
                thaw(snapshot.data['identity']))
            if interact and (session.authorized_scope != 'interact' or not manager.actions_allowed()):
                raise PermissionError('Allow controlled interactions with the reviewed page first.')
            return gateway, session, snapshot

    def register(self, registry, session, bundle):
        sources = tuple(s for s in bundle.sources if s.source_type == 'browser_dom')
        if len(sources) != 1 or session.constraints.max_ui_actions == 0:
            return
        source = sources[0]
        try:
            _, browser, snapshot = self._live(source)
        except (ValueError, PermissionError, KeyError, TypeError):
            return
        elements = thaw(snapshot.data['interactive_elements'])
        expected_inventory = digest(snapshot.data)
        # A role/name locator must resolve uniquely in the reviewed inventory.
        counts = Counter((e['role'], e['accessible_name'] or e['label']) for e in elements)
        targets = []
        roles = {'textbox': 'set_text', 'combobox': 'select_option', 'checkbox': 'set_checked'}
        for element in elements:
            role, name = element['role'], element['accessible_name'] or element['label']
            action = roles.get(role)
            if (not action or not name or counts[(role, name)] != 1 or not element['visible']
                    or not element['enabled'] or element['sensitive']
                    or action == 'set_text' and not element['editable']):
                continue
            targets.append((action, {'role': role, 'name': name}))
        targets.append(('scroll', {'role': 'document', 'name': 'Page'}))
        for action, target in targets[:60]:
            if len(registry._entries) >= 128:
                break
            initial = False if action == 'set_checked' else 100 if action == 'scroll' else '' if action == 'set_text' else 'Reviewed option'
            try:
                validate_spec({'action_type': action, 'target': target, 'value': initial}, browser.origin)
            except (ValueError, PermissionError):
                continue
            value_schema = ({'type': 'boolean'} if action == 'set_checked' else
                {'type': 'integer', 'minimum': -1000, 'maximum': 1000, 'not': {'const': 0}} if action == 'scroll' else
                {'type': 'string', 'maxLength': 1000, 'minLength': 0 if action == 'set_text' else 1})
            schema = {'type': 'object', 'properties': {'value': value_schema},
                'required': ['value'], 'additionalProperties': False}
            risk = 'local_write' if action == 'scroll' else 'external_write'
            cid = 'browser:' + action + ':' + digest([source.resource_uri, target])[:24]
            descriptor = AgentCapability(cid, (action.replace('_', ' ').title() + ': ' + target['name'])[:200],
                'Untrusted reviewed page target: ' + target['name'] + '. Origin: ' + browser.origin +
                '. The browser sidebar must approve the exact preview before each action. Final actions remain manual.',
                'browser_action', schema, None, risk, True, True, 'browser_snapshot:' + snapshot.snapshot_id)

            def refresh(descriptor=descriptor, risk=risk):
                try:
                    self._live(source, expected_inventory=expected_inventory)
                    allowed = session.constraints.allow_local_writes if risk == 'local_write' else session.constraints.allow_external_writes
                    return replace(descriptor, availability=bool(allowed))
                except (ValueError, PermissionError, KeyError, TypeError):
                    return None

            def invoke(inputs, *, cancelled=None, guard=None, constraints=None, action=action, target=target):
                if guard is None:
                    raise PermissionError('Browser actions require a fresh Agent execution guard.')
                def current():
                    if cancelled and cancelled():
                        raise InterruptedError('Browser action cancelled.')
                    if guard() is False:
                        raise PermissionError('Agent browser permission was revoked.')
                    gateway, live, _ = self._live(source, expected_inventory=expected_inventory)
                    return gateway, live
                gateway, live = current()
                spec = validate_spec({'action_type': action, 'target': target, 'value': inputs['value']}, live.origin)
                # This submits a proposal. Only the trusted sidebar can claim/approve it.
                output = gateway.execute(live.session_id, spec, cancelled=cancelled or (lambda: False),
                    guard=lambda: bool(current()), timeout=45)
                try:
                    current()
                except Exception as exc:
                    raise AgentInvocationStopped(output) from exc
                return output

            registry.register(refresh() or descriptor, invoke, refresh=refresh)
