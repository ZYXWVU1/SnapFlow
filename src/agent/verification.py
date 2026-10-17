"""Conservative evidence checks; generated prose is not proof of an effect."""
from dataclasses import dataclass
import re
from jsonschema import Draft202012Validator
from .models import thaw
from src.mcp.client.discovery import validate_schema


@dataclass(frozen=True)
class VerificationResult:
    step_id: str
    verified: bool
    method: str
    evidence: tuple
    warnings: tuple[str, ...] = ()
    confidence: float | None = None

    def to_dict(self):
        return {'step_id': self.step_id, 'verified': self.verified, 'method': self.method,
                'evidence': list(self.evidence), 'warnings': list(self.warnings), 'confidence': self.confidence}


class AgentVerifier:
    def __init__(self, *, source_ids=(), source_guard=None):
        self.source_ids = frozenset(source_ids)
        self.source_guard = source_guard

    def verify(self, step, capability, output):
        rule = thaw(step.verification) if step.verification else {}
        method = rule.get('method', 'workflow_status' if capability.capability_type == 'run_workflow' else 'result_exists' if capability.side_effecting else 'schema')
        if capability.capability_type == 'desktop_action' or capability.source.startswith('automation_skill:'):
            method = 'desktop_postcondition'
        elif capability.capability_type == 'run_workflow':
            method = 'workflow_status'
        elif capability.side_effecting:
            method = 'result_exists'
        elif capability.capability_type == 'request_user_input':
            method = 'user_confirmation'
        evidence, warnings = [], []
        verified = isinstance(output, dict) and bool(output) and output.get('success') is not False
        if capability.output_schema is not None:
            try:
                Draft202012Validator(validate_schema(thaw(capability.output_schema))).validate(output)
            except Exception:
                verified = False
                warnings.append('Output does not match the registered schema.')
        if step.expected_output:
            try:
                Draft202012Validator(validate_schema(thaw(step.expected_output))).validate(output)
            except Exception:
                verified = False
                warnings.append('Expected output schema was not satisfied.')
        if method == 'desktop_postcondition':
            verified = verified and output.get('desktop_verified') is True and output.get('status') == 'success'
            evidence.append({'type': 'desktop_postcondition', 'verified': bool(verified)})
            if not verified:
                warnings.append('Desktop outcome is unverified. Review the UI before replanning; do not repeat the write.')
        elif method in ('result_exists', 'resource_exists', 'tool_response'):
            field = 'resource_id' if capability.side_effecting else rule.get('field', 'resource_id')
            value = output.get(field) if isinstance(output, dict) else None
            verified = verified and type(value) in (str, int) and not isinstance(value, bool) and bool(value)
            if verified:
                evidence.append({'type': 'returned_identifier', 'field': field, 'value': value})
            else:
                warnings.append('No verified resource identifier was returned. Review the effect before retrying.')
        elif method == 'workflow_status':
            verified = verified and output.get('status') == 'success' and bool(output.get('steps')) and all(s.get('status') == 'success' for s in output.get('steps', ()) if s.get('required', True))
            evidence.append({'type': 'workflow_status', 'status': output.get('status')})
        elif method == 'field_match':
            verified = verified and output.get(rule.get('field')) == rule.get('value')
            evidence.append({'type': 'field_match', 'field': rule.get('field')})
        elif method == 'user_confirmation':
            verified = verified and output.get('confirmed') is True
            evidence.append({'type': 'user_confirmation', 'confirmed': verified})
        elif method not in ('schema', 'source_grounding'):
            verified = False
            warnings.append('Unsupported verification method.')
        if method == 'source_grounding' or 'source_ids' in output:
            refs = output.get('source_ids', ())
            valid_refs = isinstance(refs, list) and bool(refs) and all(type(s) is str for s in refs) and set(refs) <= self.source_ids
            if 'text' in output:
                citations = set(re.findall(r'\[([A-Za-z]+\d+)\]', output['text']))
                valid_refs = valid_refs and citations == set(refs)
            verified = verified and valid_refs
            if self.source_guard:
                try:
                    self.source_guard()
                except Exception:
                    verified = False
            evidence.append({'type': 'source_references', 'source_ids': list(refs) if valid_refs else []})
            warnings.append('Source references checked; factual accuracy of generated prose requires user review.')
        if method == 'schema' and verified:
            evidence.append({'type': 'schema', 'valid': True})
        return VerificationResult(step.step_id, bool(verified), method, tuple(evidence), tuple(warnings))
