from .base import Skill
from .validation import fields, positive_integer, string_list, text
from src.prompts import DEBUG_GUIDANCE


class CodeErrorSkill(Skill):
    id, title = 'code_error', 'Code Error'
    schema = {'language': 'string or null', 'error_type': 'string or null', 'error_message': 'string or null',
              'file': 'string or null', 'line': 'positive integer or null', 'evidence': ['string'],
              'likely_cause': 'string or null', 'suggested_fixes': [dict.fromkeys(('title', 'before', 'after', 'explanation'), 'string or null')],
              'confidence': 'low | medium | high | null'}
    instructions = DEBUG_GUIDANCE + ' Include file and line only if visible. Fixes may omit before/after code when not supported.'
    action_ids = ('copy_error', 'copy_fix', 'copy_diagnosis', 'explain', 'ask_ai')
    presentation = (('error_type', 'Problem'), ('error_message', 'Error'), ('language', 'Language'),
                    ('file', 'File'), ('line', 'Line'), ('evidence', 'Evidence'), ('likely_cause', 'Likely Cause'),
                    ('suggested_fixes', 'Suggested Fix'), ('confidence', 'Diagnosis confidence'))

    def normalize(self, raw):
        data, warnings = fields(raw, self.schema, {'line': positive_integer, 'evidence': string_list,
                                                  'suggested_fixes': lambda v: []})
        fixes = raw.get('suggested_fixes', [])
        if not isinstance(fixes, list):
            warnings.append('Suggested fixes could not be read.')
            fixes = []
        for fix in fixes:
            if isinstance(fix, dict):
                normalized = {key: text(fix.get(key)) for key in ('title', 'before', 'after', 'explanation')}
                for key in ('before', 'after'):
                    value = fix.get(key)
                    normalized[key] = value if isinstance(value, str) and value.strip() else None
                if any(normalized.values()):
                    data['suggested_fixes'].append(normalized)
        if data['confidence'] not in ('low', 'medium', 'high', None):
            data['confidence'] = None
            warnings.append('Diagnosis confidence could not be read.')
        return data, warnings
