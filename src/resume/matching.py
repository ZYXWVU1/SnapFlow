"""Conservative local matching. Saved rules are references, never browser authority."""
import re
from collections import Counter

from .formatting import format_value
from .models import CATEGORIES
from .categories import LIST_CATEGORIES


ALIASES = {
    'person.full_name': ('full name', 'applicant name', 'your name', 'legal name'),
    'person.given_name': ('first name', 'given name'),
    'person.family_name': ('last name', 'family name', 'surname'),
    'contact.email': ('email', 'email address', 'your email'),
    'contact.phone': ('phone', 'phone number', 'telephone', 'mobile phone'),
    'address.city': ('city', 'town'), 'address.region': ('state', 'province', 'region'),
    'address.country': ('country', 'country of residence'),
    'links.linkedin': ('linkedin', 'linkedin url', 'linkedin profile'),
    'links.github': ('github', 'github url', 'github profile'),
    'links.portfolio': ('portfolio', 'portfolio url', 'personal website'),
    'skills.items': ('skills', 'technical skills'),
    'experience.employer': ('employer', 'company', 'company name', 'employer name'),
    'experience.title': ('job title', 'position', 'position title', 'role'),
    'experience.location': ('work location', 'job location', 'location'),
    'experience.start_date': ('start date', 'employment start date', 'from'),
    'experience.end_date': ('end date', 'employment end date', 'to'),
    'experience.description': ('job description', 'responsibilities', 'description'),
    'education.institution': ('school', 'school name', 'university', 'institution', 'institution name'),
    'education.degree': ('degree', 'qualification'),
    'education.field_of_study': ('field of study', 'major', 'specialization'),
    'education.start_date': ('start date', 'education start date', 'from'),
    'education.end_date': ('end date', 'graduation date', 'to'),
}
SECTIONS = {
    'experience': ('work experience', 'employment history', 'employment', 'professional experience', 'work history', 'experience'),
    'education': ('education', 'education history', 'academic history', 'academic background'),
    'project': ('projects', 'project experience', 'personal projects', 'academic projects'),
    'certification': ('certifications', 'certification', 'licenses', 'credentials'),
    'award': ('awards', 'honors and awards'),
    'publication': ('publications', 'publication history'),
    'volunteering': ('volunteering', 'volunteer experience', 'volunteer work', 'leadership experience'),
    'additional': ('additional information', 'additional achievements'),
}
ALIASES.update({
    'address.street': ('street address', 'address line 1'), 'address.postal_code': ('postal code', 'zip code'),
    'person.summary': ('professional summary', 'professional profile'), 'languages.items': ('languages', 'spoken languages'),
    'education.location': ('school location', 'location'), 'education.gpa': ('gpa', 'grade point average'),
    'education.coursework': ('coursework', 'relevant coursework'), 'education.honors': ('honors', 'academic honors'),
    'project.name': ('project name', 'project title'), 'project.role': ('project role', 'role'),
    'project.organization': ('organization', 'project organization'), 'project.description': ('project description', 'description'),
    'project.technologies': ('technologies', 'project technologies', 'technology stack'), 'project.url': ('project url', 'project link'),
    'certification.name': ('certification name', 'credential name'), 'certification.issuer': ('issuer', 'issuing organization'),
    'certification.credential_id': ('credential id', 'license number'), 'certification.url': ('credential url', 'certification url'),
    'award.name': ('award name', 'award title'), 'award.issuer': ('awarding organization', 'issuer'),
    'publication.title': ('publication title', 'title'), 'publication.publisher': ('publisher',),
    'publication.authors': ('authors',), 'publication.url': ('publication url',),
    'volunteering.organization': ('organization', 'organization name'), 'volunteering.role': ('volunteer role', 'role'),
    'volunteering.description': ('description', 'responsibilities'),
    'additional.title': ('title',), 'additional.description': ('description',),
})
for _kind in SECTIONS:
    if _kind not in ('experience', 'education'):
        for _suffix, _aliases in (('start_date', ('start date', 'from')), ('end_date', ('end date', 'to'))):
            if _kind + '.' + _suffix in CATEGORIES:
                ALIASES[_kind + '.' + _suffix] = _aliases
AUTOCOMPLETE = {'name': 'person.full_name', 'given-name': 'person.given_name', 'family-name': 'person.family_name',
                'email': 'contact.email', 'tel': 'contact.phone', 'address-level2': 'address.city',
                'address-level1': 'address.region', 'country-name': 'address.country'}
OTHER_PERSON = re.compile(r'\b(reference|references|referee|referees|emergency|recipient|beneficiary|supervisor|manager|parent|guardian|spouse|other person)\b')
MANUAL = re.compile(r'\b(password|salary|compensation|sponsorship|visa|citizenship|work authorization|eligible to work|gender|race|ethnicity|disability|veteran|signature|consent|agree|social security|ssn|bank|credit card|submit|upload)\b')


def normalize(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 -]+', ' ', str(value).lower())).strip()


def section_kind(field):
    section = normalize(field.get('section', ''))
    found = []
    for kind, aliases in SECTIONS.items():
        if any(re.search(r'\b' + re.escape(alias) + r'\b', section) for alias in aliases):
            found.append(kind)
    if len(found) > 1 and 'experience' in found and not re.search(r'\b(work|employment|professional)\b', section):
        found.remove('experience')
    return found[0] if len(found) == 1 else ('ambiguous' if found else None)


def field_safety_reason(field):
    text = normalize(str(field.get('name', '')) + ' ' + str(field.get('section', '')))
    if OTHER_PERSON.search(text):
        return 'other person or reference field requires manual entry'
    if MANUAL.search(text):
        return 'sensitive or application decision requires manual entry'
    if field.get('role') not in {'textbox', 'combobox', 'listbox', 'searchbox', 'spinbutton'}:
        return 'unsupported control'
    if field.get('input_type', 'text') not in {'', 'text', 'email', 'tel', 'url', 'search', 'textarea', 'select-one', 'date', 'month', 'number'}:
        return 'unsupported control'
    if field.get('disabled') or field.get('readonly') or field.get('read_only'):
        return 'unavailable control'
    if field.get('value') or field.get('nonempty') or field.get('has_value'):
        return 'existing value requires explicit replacement review'
    return ''


def category_compatible(category, field):
    """Sections override labels and autocomplete, including taught rules."""
    if field_safety_reason(field):
        return False
    kind = section_kind(field)
    category_kind = category.split('.')[0]
    if category_kind in SECTIONS:
        return kind == category_kind
    return kind is None


def _deterministic_category(field):
    name = normalize(field.get('name', ''))
    candidates = [category for category, aliases in ALIASES.items()
                  if name in aliases and category_compatible(category, field)]
    # Autocomplete is corroborating evidence: a conflicting visible label abstains.
    token = str(field.get('autocomplete', '')).split()
    ac = AUTOCOMPLETE.get(token[-1]) if token else None
    if ac and category_compatible(ac, field):
        if candidates and ac not in candidates:
            return None
        if name in ALIASES.get(ac, ()):
            return ac
    return candidates[0] if len(candidates) == 1 else None


def _rule_matches(rule, field, profile_id, origin):
    if not (rule.get('enabled') is True and rule.get('confirmed_by_user') is True and rule.get('profile_id') == profile_id):
        return False
    category = rule.get('category')
    if category not in CATEGORIES or not category_compatible(category, field):
        return False
    scope = rule.get('scope', {})
    if scope.get('kind') not in {'site', 'reusable'} or (scope['kind'] == 'site' and scope.get('origin') != origin):
        return False
    if rule.get('control_kind') != field.get('role'):
        return False
    name = normalize(field.get('name', ''))
    cues = [normalize(cue) for cue in rule.get('field_cues', [])]
    if name not in cues:
        return False
    section = normalize(field.get('section', ''))
    sections = [normalize(cue) for cue in rule.get('section_cues', [])]
    return not sections or any(cue and (cue == section or re.search(r'\b' + re.escape(cue) + r'\b', section)) for cue in sections)


def _entity_id(profile, category, selector, group, assignments, repeated):
    kind = category.split('.')[0]
    if kind not in SECTIONS:
        return None
    entities = [entity for entity in profile.get('entities', []) if entity.get('kind') == kind]
    assigned = assignments.get(group)
    if assigned is not None:
        if not any(entity.get('id') == assigned for entity in entities):
            raise ValueError('invalid group entity assignment')
        return assigned
    if repeated or selector.get('kind') == 'group_assignment':
        raise ValueError('repeated groups require explicit entity assignment')
    selector_kind = selector.get('kind')
    if selector_kind == 'entity_id':
        if any(entity.get('id') == selector.get('id') for entity in entities):
            return selector['id']
        raise ValueError('selected entity is missing')
    if kind not in ('experience', 'education'):
        raise ValueError('this record requires explicit entity assignment')
    if selector_kind != 'most_recent_' + kind:
        raise ValueError('entity selection required')
    if not entities:
        raise ValueError('missing profile entity')
    if len(entities) > 1 and any(not entity.get('end_date') for entity in entities):
        raise ValueError('unknown end dates require an explicit entity assignment')
    # Only explicit present means current. Missing dates remain unknown.
    dated = [(str(entity.get('end_date', '')).lower() == 'present', str(entity.get('end_date') or entity.get('start_date', '')),
              str(entity.get('start_date', '')), entity['id']) for entity in entities]
    if any(not re.fullmatch(r'\d{4}(?:-\d{2})?(?:-\d{2})?', start) for _, _, start, _ in dated):
        raise ValueError('entity chronology is ambiguous')
    dated.sort(reverse=True)
    if len(dated) > 1 and dated[0][:-1] == dated[1][:-1]:
        raise ValueError('entity chronology is ambiguous')
    return dated[0][-1]


def match_fields(profile, rules, fields, origin, assignments=None, suggestions=None):
    """Produce local proposals. AI-only suggestions always need explicit teaching."""
    assignments = assignments or {}
    suggestions = suggestions if isinstance(suggestions, dict) else {}
    groups = {kind: {field.get('group', '') for field in fields if section_kind(field) == kind} for kind in SECTIONS}
    refs = Counter(field.get('field_ref') for field in fields)
    rows = []
    for field in fields:
        ref = field.get('field_ref')
        row = dict(field_ref=ref, category=None, value=None, reason='', eligible=False, rule_id=None)
        rows.append(row)
        reason = field_safety_reason(field)
        if not ref or refs[ref] != 1 or not field.get('descriptor_hash'):
            reason = 'missing or ambiguous inventory target'
        if reason:
            row['reason'] = reason
            continue
        matched = [rule for rule in rules if _rule_matches(rule, field, profile.get('id'), origin)]
        site = [rule for rule in matched if rule.get('scope', {}).get('kind') == 'site']
        matched = site or matched
        semantic = {(rule['category'], repr(sorted(rule.get('entity_selector', {}).items())), rule.get('formatter', 'identity')) for rule in matched}
        if len(semantic) > 1:
            row['reason'] = 'conflicting rules require teaching resolution'
            continue
        selected = matched[0] if matched else None
        category = selected['category'] if selected else _deterministic_category(field)
        suggested = False
        if category is None:
            suggestion = suggestions.get(ref)
            if isinstance(suggestion, dict):
                suggestion = suggestion.get('category')
            if isinstance(suggestion, str) and suggestion in CATEGORIES and category_compatible(suggestion, field):
                category, suggested = suggestion, True
        row['category'] = category
        if not category:
            row['reason'] = 'needs teaching: no unambiguous supported category'
            continue
        if suggested:
            row['reason'] = 'AI suggested category requires explicit teaching confirmation'
            continue
        row['rule_id'] = selected.get('id') if selected else None
        kind = category.split('.')[0]
        selector = selected.get('entity_selector', {}) if selected else {'kind': 'most_recent_' + kind}
        try:
            entity_id = _entity_id(profile, category, selector, field.get('group', ''), assignments, len(groups.get(kind, set())) > 1)
            if entity_id and kind == 'experience':
                entity = next(entity for entity in profile['entities'] if entity['id'] == entity_id)
                temporal = normalize(str(field.get('name', '')) + ' ' + str(field.get('section', '')))
                end_date = str(entity.get('end_date', '')).lower()
                if re.search(r'\b(current|present)\b', temporal) and end_date != 'present':
                    raise ValueError('current employment field requires explicit current employment')
                if re.search(r'\b(previous|former|past)\b', temporal) and (not end_date or end_date == 'present'):
                    raise ValueError('previous employment field requires a known completed job')
            facts = [fact for fact in profile.get('facts', []) if fact.get('key') == category and fact.get('entity_id') == entity_id and fact.get('reviewed') is True]
            if len(facts) != 1:
                raise ValueError('missing or ambiguous reviewed profile fact')
            formatter = selected.get('formatter', 'identity') if selected else ('list_join' if category in LIST_CATEGORIES else 'identity')
            row['value'] = format_value(facts[0]['value'], field, formatter)
            row['eligible'] = True
            row['reason'] = 'confirmed rule' if selected else ('deterministic category; proposed most recent record' if entity_id else 'deterministic category')
        except (ValueError, TypeError, KeyError) as exc:
            row['reason'] = str(exc)
    # Plain layout wrappers may hide repeated records from structural grouping.
    # A record category appearing twice inside one group cannot safely denote one
    # job/school; an assignment to that same ambiguous group cannot disambiguate it.
    fields_by_ref = {field.get('field_ref'): field for field in fields}
    record_keys = Counter((fields_by_ref[row['field_ref']].get('group', ''), row['category'])
                          for row in rows if row['category'] and row['category'].split('.')[0] in SECTIONS)
    for row in rows:
        if row['category'] and record_keys[(fields_by_ref[row['field_ref']].get('group', ''), row['category'])] > 1:
            row.update(eligible=False, value=None,
                       reason='repeated record category requires distinct, explicitly assigned groups')
    return rows
