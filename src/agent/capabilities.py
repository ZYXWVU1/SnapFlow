"""An explicit catalog of trusted adapters; model text never chooses code."""
from collections.abc import Mapping
from dataclasses import dataclass
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .models import Contract, RISKS, STEP_TYPES, canonical, digest, snapshot, thaw


class AgentInvocationStopped(ValueError):
    """The handler returned a receipt before a later execution check stopped it."""
    def __init__(self, output, message='Execution stopped after the capability returned a result.'):
        super().__init__(message)
        try:
            self._output = snapshot(output)
        except (ValueError, TypeError, RecursionError):
            receipt = {'receipt_truncated': True}
            if isinstance(output, Mapping):
                if type(output.get('success')) is bool:
                    receipt['success'] = output['success']
                for key, maximum in (('resource_id', 512), ('status', 64)):
                    value = output.get(key)
                    if type(value) in (str, int) and len(str(value)) <= maximum:
                        receipt[key] = value
            self._output = snapshot(receipt)

    @property
    def output(self):
        return thaw(self._output)


def _schema(value):
    if not isinstance(value, Mapping):
        raise ValueError('Capability schemas must be objects.')
    plain = thaw(snapshot(value))
    canonical(plain, limit=32768)
    def check(node):
        if isinstance(node, dict):
            # No retrieval of remote schemas, including newer dynamic references.
            for key in ('$ref', '$dynamicRef', '$recursiveRef'):
                if key in node and (not isinstance(node[key], str) or not node[key].startswith('#')):
                    raise ValueError('Only local schema references are allowed.')
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(plain)
    try:
        Draft202012Validator.check_schema(plain)
    except SchemaError as exc:
        raise ValueError('Invalid capability schema.') from exc
    return snapshot(plain)


@dataclass(frozen=True)
class AgentCapability(Contract):
    capability_id: str
    name: str
    description: str
    capability_type: str
    input_schema: dict
    output_schema: dict | None
    risk_level: str
    side_effecting: bool
    availability: bool
    source: str
    remote: bool = False
    ai_calls: int = 0

    def __post_init__(self):
        if type(self.capability_id) is not str or not re.fullmatch(r'[A-Za-z0-9_:.-]{1,256}', self.capability_id):
            raise ValueError('Invalid capability identifier.')
        for name, limit in (('name', 200), ('description', 2000), ('source', 256)):
            value = getattr(self, name)
            if type(value) is not str or not value.strip() or len(value) > limit:
                raise ValueError('Invalid capability metadata.')
        if self.capability_type not in STEP_TYPES or self.risk_level not in RISKS:
            raise ValueError('Unsupported capability type or risk.')
        if any(type(getattr(self, name)) is not bool for name in ('side_effecting', 'availability', 'remote')):
            raise ValueError('Capability flags must be booleans.')
        if type(self.ai_calls) is not int or not 0 <= self.ai_calls <= 50:
            raise ValueError('Invalid capability AI budget.')
        object.__setattr__(self, 'input_schema', _schema(self.input_schema))
        if self.output_schema is not None:
            object.__setattr__(self, 'output_schema', _schema(self.output_schema))

    @property
    def fingerprint(self):
        return digest(self.to_dict())


class AgentCapabilityRegistry:
    def __init__(self):
        self._entries = {}

    def register(self, capability, handler, *, refresh=None):
        if not isinstance(capability, AgentCapability) or not callable(handler) or (refresh is not None and not callable(refresh)):
            raise ValueError('Register a descriptor and trusted handler.')
        if capability.capability_id in self._entries or len(self._entries) >= 128:
            raise ValueError('Duplicate or excessive capability registration.')
        self._entries[capability.capability_id] = (capability, handler, refresh)

    def get(self, capability_id):
        entry = self._entries.get(capability_id)
        if entry is None:
            return None
        descriptor, _, refresh = entry
        current = refresh() if refresh is not None else descriptor
        if current is None:
            return None
        if not isinstance(current, AgentCapability) or current.capability_id != capability_id:
            raise ValueError('Capability refresh changed its registered identity.')
        return current

    def catalog(self):
        result = []
        for capability_id in self._entries:
            if capability_id in getattr(self, 'historical_capabilities', ()):
                continue
            current = self.get(capability_id)
            if current is not None:
                result.append(current.to_dict() | {'fingerprint': current.fingerprint})
        canonical(result, limit=262144)
        return result

    def invoke(self, capability_id, inputs, *, cancelled=None, guard=None, constraints=None):
        def boundary():
            if cancelled and cancelled():
                raise ValueError('Agent execution cancelled.')
            if guard is not None:
                if guard() is False:
                    raise ValueError('Agent execution permission was revoked.')
            capability = self.get(capability_id)
            if capability is None or not capability.availability:
                raise ValueError('The registered capability is unavailable.')
            if constraints is not None:
                if capability_id in constraints.forbidden_capabilities:
                    raise ValueError('The goal forbids this capability.')
                if capability.remote and capability.ai_calls and not constraints.allow_remote_ai:
                    raise ValueError('The goal forbids remote AI.')
                if capability.remote and capability.capability_type == 'call_mcp_tool' and not constraints.allow_remote_mcp:
                    raise ValueError('The goal forbids remote MCP.')
                if capability.side_effecting and (capability.risk_level == 'local_write' and not constraints.allow_local_writes or
                        capability.risk_level == 'external_write' and not constraints.allow_external_writes):
                    raise ValueError('The goal forbids this write.')
                if capability.ai_calls > constraints.max_ai_calls:
                    raise ValueError('The AI budget forbids this capability.')
            return capability
        capability = self.get(capability_id)
        if capability is None or not capability.availability:
            raise ValueError('The registered capability is unavailable.')
        if boundary().fingerprint != capability.fingerprint:
            raise ValueError('Capability changed during approval. Review the plan again.')
        if not isinstance(inputs, Mapping):
            raise ValueError('Capability inputs must be an object.')
        safe_inputs = thaw(snapshot(inputs))
        canonical(safe_inputs, limit=65536)
        try:
            Draft202012Validator(thaw(capability.input_schema)).validate(safe_inputs)
        except ValidationError as exc:
            raise ValueError('Capability inputs do not match the registered schema.') from exc
        current = boundary()
        if current.fingerprint != capability.fingerprint:
            raise ValueError('Capability changed before execution. Review the plan again.')
        result = self._entries[capability_id][1](safe_inputs, cancelled=cancelled, guard=guard, constraints=constraints)
        try:
            safe_output = thaw(snapshot(result))
            if boundary().fingerprint != capability.fingerprint:
                raise ValueError('Capability changed during execution. Review the result again.')
            if capability.output_schema is not None:
                Draft202012Validator(thaw(capability.output_schema)).validate(safe_output)
        except Exception as exc:
            raise AgentInvocationStopped(result) from exc
        return safe_output
