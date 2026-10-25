"""Closed resume operations; no filesystem path, script or page command input."""
import re
from src.agent.models import canonical
from .models import document_identity

FIELDS = {
    'profiles': set(), 'profile': {'profile_id'}, 'manage': {'profile_id'}, 'delete': {'profile_id'},
    'inventory': {'inventory_id', 'sequence', 'fields', 'complete', 'digest'},
    'prepare': {'profile_id', 'inventory_id', 'assignments'}, 'rows': {'plan_id', 'offset'},
    'review': {'plan_id', 'observations', 'selected'}, 'approve': {'plan_id', 'fingerprint'},
    'claim': {'plan_id', 'field_ref'}, 'result': {'plan_id', 'action_id', 'status'},
    'cancel': {'plan_id'}, 'teach': {'profile_id', 'inventory_id', 'field_ref', 'category', 'entity_selector', 'scope', 'formatter'},
    'delete_rule': {'profile_id', 'rule_id'},
    'suggest': {'profile_id', 'inventory_id', 'runtime_choice'}, 'job': {'job_id'}, 'cancel_job': {'job_id'},
}
FIELDS['preview_rule'] = FIELDS['teach']
FIELDS['observe'] = {'plan_id','observations'}
FIELDS['rules'] = {'profile_id','offset'}
FIELDS['profile_page'] = {'offset'}
FIELDS['entity_page'] = {'profile_id','offset'}


def identifier(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('Invalid resume identity.')


def validate_request(payload):
    if type(payload) is not dict or set(payload) != {'identity', 'operation', 'args'}:
        raise ValueError('Invalid resume request.')
    document_identity(payload['identity'])
    operation, args = payload['operation'], payload['args']
    if type(operation) is not str or operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
        raise ValueError('Unsupported resume operation or fields.')
    canonical(args, limit=24576)
    for key in ('profile_id', 'inventory_id', 'plan_id', 'field_ref', 'action_id', 'rule_id', 'job_id'):
        if key in args and not (key == 'profile_id' and operation == 'manage' and args[key] is None):
            identifier(args[key])
    if 'fingerprint' in args or 'digest' in args:
        for key in ('fingerprint', 'digest'):
            if key in args and (type(args[key]) is not str or not re.fullmatch('[a-f0-9]{64}', args[key])):
                raise ValueError('Invalid resume digest.')
    if operation == 'inventory' and (type(args['sequence']) is not int or not 1 <= args['sequence'] <= 16 or type(args['fields']) is not list or len(args['fields']) > 50 or type(args['complete']) is not bool):
        raise ValueError('Invalid resume inventory chunk.')
    if operation == 'rows' and (type(args['offset']) is not int or not 0 <= args['offset'] <= 50):
        raise ValueError('Invalid resume row offset.')
    if operation == 'rules' and (type(args['offset']) is not int or not 0 <= args['offset'] <= 2000):
        raise ValueError('Invalid rule offset.')
    if operation == 'profile_page' and (type(args['offset']) is not int or not 0 <= args['offset'] <= 100):
        raise ValueError('Invalid profile offset.')
    if operation == 'entity_page' and (type(args['offset']) is not int or not 0 <= args['offset'] <= 50):
        raise ValueError('Invalid entity offset.')
    if operation == 'prepare' and (type(args['assignments']) is not dict or len(args['assignments']) > 50 or any(type(k) is not str or len(k)>200 for k in args['assignments'])):
        raise ValueError('Invalid entity assignments.')
    if operation == 'prepare':
        for value in args['assignments'].values(): identifier(value)
    if operation == 'review' and (type(args['observations']) is not list or len(args['observations']) > 50 or type(args['selected']) is not list or len(args['selected']) > 50):
        raise ValueError('Invalid resume review.')
    if operation in ('review','observe'):
        from .action_policy import validate_observation
        if type(args['observations']) is not list or len(args['observations'])>50: raise ValueError('Invalid observations.')
        for value in args.get('selected',[]): identifier(value)
        for row in args['observations']:
            if type(row) is not dict or set(row) != {'field_ref','before'}: raise ValueError('Invalid field pre-state.')
            identifier(row['field_ref']); validate_observation(row['before'])
    if operation in ('teach','preview_rule'):
        from src.resume.models import CATEGORIES, FORMATTERS
        if type(args['category']) is not str or args['category'] not in CATEGORIES or type(args['formatter']) is not str or args['formatter'] not in FORMATTERS:
            raise ValueError('Unknown category or formatter.')
        selector,scope=args['entity_selector'],args['scope']
        if type(selector) is not dict or type(selector.get('kind')) is not str or selector.get('kind') not in ('none','entity_id','group_assignment','most_recent_experience','most_recent_education') or set(selector)!=({'kind','id'} if selector.get('kind')=='entity_id' else {'kind'}):
            raise ValueError('Invalid resume entity selector.')
        if 'id' in selector: identifier(selector['id'])
        if type(scope) is not dict or type(scope.get('kind')) is not str or scope.get('kind') not in ('site','reusable') or set(scope)!=({'kind','origin'} if scope.get('kind')=='site' else {'kind'}): raise ValueError('Invalid teaching scope.')
        if scope['kind']=='site':
            from .models import origin
            origin(scope['origin'])
    if operation == 'result' and args['status'] not in ('verified', 'not_applied', 'unknown'):
        raise ValueError('Invalid resume outcome.')
    if operation == 'suggest' and args['runtime_choice'] not in (None, 'local_only', 'cloud_only'):
        raise ValueError('Choose a valid AI runtime.')
    return payload


def validate_response(payload):
    if type(payload) is not dict or set(payload) != {'ok', 'kind', 'operation', 'data'} or payload['ok'] is not True or payload['kind'] != 'resume' or payload['operation'] not in FIELDS or type(payload['data']) is not dict:
        raise ValueError('Invalid resume response.')
    canonical(payload, limit=28000)
    data, operation = payload['data'], payload['operation']
    expected = {
        'profiles': {'profiles','categories'}, 'profile_page': {'profiles','total'}, 'entity_page': {'entities','total'}, 'profile': {'profile','rules'}, 'manage': {'opened'},
        'delete': {'deleted'}, 'delete_rule': {'deleted'}, 'inventory': {'inventory_id','complete'},
        'prepare': {'plan_id','expires_at'}, 'rows': {'plan_id','rows','total','expires_at','status'},
        'review': {'plan_id','fingerprint','selected_count','expires_at'}, 'observe': {'plan_id','observed_count'}, 'rules': {'rules','total'},
        'approve': {'plan_id','status'}, 'claim': {'action'}, 'result': {'status'},
        'cancel': {'cancelled'}, 'teach': {'rule'}, 'preview_rule': {'category','value','eligible','reason'}, 'suggest': {'job_id','state'},
        'job': {'job_id','state','suggestions','code'}, 'cancel_job': {'cancelled'},
    }[operation]
    if set(data) != expected:
        raise ValueError('Unexpected resume response fields.')
    for key in ('plan_id','inventory_id','job_id'):
        if key in data: identifier(data[key])
    if operation=='claim':
        from .protocol import _action_receipt
        _action_receipt(data['action'])
    if operation=='rows':
        if type(data['rows']) is not list or len(data['rows'])>4 or type(data['total']) is not int or not 0<=data['total']<=50 or type(data['expires_at']) is not int or data['status'] not in ('draft','preview','approved','completed','stopped'): raise ValueError('Invalid resume rows.')
        from .action_policy import validate_observation, validate_spec
        for row in data['rows']:
            expected_row={'field_ref','category','value','reason','eligible','rule_id','name','section','group','spec','selected','before','status'}
            if type(row) is not dict or set(row)!=expected_row or type(row['eligible']) is not bool or type(row['selected']) is not bool or type(row['reason']) is not str or len(row['reason'])>500 or row['status'] not in ('ready','skipped','unknown','verified','not_applied'): raise ValueError('Invalid resume row.')
            identifier(row['field_ref'])
            if row['before'] is not None: validate_observation(row['before'])
            if row['spec'] is not None:
                validate_spec(row['spec'],'https://autofill.invalid')
                if row['spec']['action_type'] not in ('set_text','select_option') or row['spec']['target'].get('field_ref')!=row['field_ref']: raise ValueError('Invalid fill operation.')
    if operation in ('profiles','profile_page'):
        if type(data['profiles']) is not list or len(data['profiles'])>100: raise ValueError('Invalid profile summaries.')
        if operation=='profiles':
            from src.resume.categories import CATEGORIES
            if type(data['categories']) is not list or len(data['categories'])>128: raise ValueError('Invalid category descriptions.')
            seen=set()
            for category in data['categories']:
                if (type(category) is not dict or set(category)!={'key','label'} or type(category['key']) is not str
                        or category['key'] not in CATEGORIES or category['key'] in seen or type(category['label']) is not str
                        or not 0<len(category['label'])<=200): raise ValueError('Invalid category description.')
                seen.add(category['key'])
        if operation=='profile_page' and (type(data['total']) is not int or not 0<=data['total']<=100): raise ValueError('Invalid profile count.')
        for profile in data['profiles']:
            if type(profile) is not dict or set(profile)!={'id','revision','name'} or type(profile['revision']) is not int or profile['revision']<1 or type(profile['name']) is not str or len(profile['name'])>200: raise ValueError('Invalid profile summary.')
            identifier(profile['id'])
    if operation in ('delete','delete_rule','cancel','cancel_job','manage') and type(next(iter(data.values()))) is not bool: raise ValueError('Invalid resume acknowledgment.')
    return payload
