"""RAM-only inventory, matching drafts and single-use exact page-fill permits."""
import json
from hashlib import sha256
from copy import deepcopy
from uuid import uuid4

from src.agent.models import digest, canonical
from .action_policy import validate_spec, validate_observation, SENSITIVE, COMMIT, SECRET
from .resume_contract import validate_request, identifier


class BrowserAutofillStore:
    def __init__(self, manager, store_provider=lambda: None):
        self.manager, self.store_provider = manager, store_provider
        self.inventories, self.plans, self.jobs = {}, {}, {}
        self.used_inventory_ids = {}

    def library(self):
        value = self.store_provider()
        if value is None:
            raise PermissionError('Resume profiles are unavailable.')
        return value

    def revoke(self, session_id, *, forget_inventory_ids=True):
        self.inventories.pop(session_id, None)
        if forget_inventory_ids:
            self.used_inventory_ids.pop(session_id, None)
        for key, plan in tuple(self.plans.items()):
            if plan['session_id'] == session_id:
                self._cancel_plan_action(plan)
                self.plans.pop(key, None)
        for key, job in tuple(self.jobs.items()):
            if job['session_id'] == session_id:
                self.jobs.pop(key, None)

    def clear(self):
        self.inventories.clear()
        self.plans.clear()
        self.jobs.clear()
        self.used_inventory_ids.clear()

    def _cancel_plan_action(self, plan):
        action_id = plan.get('inflight')
        entry = self.manager.actions.pending.get(action_id)
        if entry is not None and entry[0] == plan['session_id']:
            self.manager.actions.cancel_session(plan['session_id'], force=True)

    def prune(self):
        now = self.manager.clock()*1000
        for session_id, inventory in tuple(self.inventories.items()):
            if inventory['expires_at'] <= now:
                self.revoke(session_id, forget_inventory_ids=False)
        for plan_id, plan in tuple(self.plans.items()):
            try:
                self.manager.session_store.require(plan['connection_id'], plan['session_id'], plan['identity'])
                self.plan(plan['connection_id'], plan['session_id'], plan_id)
            except (PermissionError, ValueError, KeyError):
                self._cancel_plan_action(plan)
                self.plans.pop(plan_id, None)
        for job_id in tuple(self.jobs):
            if not self.valid_job(job_id):
                self.jobs.pop(job_id, None)

    def no_separate_action(self, session_id):
        if any(owner == session_id for owner, _ in self.manager.actions.pending.values()):
            raise PermissionError('Finish or cancel the separate controlled action first.')

    def poison_inventory(self, session_id, inventory_id=None):
        if type(inventory_id) is str and len(inventory_id) == 32 and all(c in '0123456789abcdef' for c in inventory_id):
            used = self.used_inventory_ids.setdefault(session_id, set())
            if len(used) < 64:
                used.add(inventory_id)
        self.revoke(session_id, forget_inventory_ids=False)

    def active(self, session_id):
        return any(p['session_id'] == session_id and p['status'] == 'approved' and p['expires_at'] > self.manager.clock()*1000 for p in self.plans.values())

    def idle(self, session_id):
        trace = self.manager.teaching.traces.get(session_id)
        if trace is not None and trace.status == 'recording':
            raise PermissionError('Pause or finish website teaching first.')

    def inventory(self, session_id, inventory_id):
        value = self.inventories.get(session_id)
        if not value or value['inventory_id'] != inventory_id or not value['complete'] or value['expires_at'] <= self.manager.clock()*1000:
            raise PermissionError('Scan a fresh authorized page.')
        return value

    def validate_target(self, session_id, target):
        value = self.inventories.get(session_id)
        if not value or not value['complete'] or value['expires_at'] <= self.manager.clock()*1000:
            raise PermissionError('Resume target inventory expired.')
        field = next((f for f in value['fields'] if f['field_ref'] == target.get('field_ref')), None)
        if field is None or any(target.get(k) != field[k] for k in ('role', 'name', 'field_ref', 'descriptor_hash')):
            raise PermissionError('Resume target changed or belongs to another inventory.')

    def stamp(self, profile_id):
        library = self.library()
        return self.record_stamp(self.profile(profile_id),library.list_rules(profile_id))

    @staticmethod
    def record_stamp(profile, rules):
        # Store writes always increment revisions; content is neither needed nor
        # safe to repeatedly serialize into a small protocol-sized hash budget.
        versions=dict(profile=(profile['id'],profile['revision']),
            rules=sorted((r['id'],r['revision']) for r in rules))
        return sha256(canonical(versions,limit=1024*1024).encode('utf-8')).hexdigest()

    def profile(self, profile_id):
        value = self.library().get_profile(profile_id)
        if value is None:
            raise ValueError('Resume profile does not exist.')
        return value

    def plan(self, connection, session_id, plan_id):
        plan = self.plans.get(plan_id)
        if not plan or plan['connection_id'] != connection or plan['session_id'] != session_id or plan['expires_at'] <= self.manager.clock()*1000:
            raise PermissionError('Prepare a fresh resume fill plan.')
        self.inventory(session_id, plan['inventory_id'])
        if plan['stamp'] != self.stamp(plan['profile_id']) or plan['policy'] != self.manager.questions.revision():
            self.plans.pop(plan_id, None)
            raise PermissionError('Resume, teaching rules or runtime policy changed.')
        return plan

    @staticmethod
    def field(value):
        keys = {'field_ref','descriptor_hash','role','name','section','group','input_type','autocomplete','options','required','max_length'}
        if type(value) is not dict or set(value) != keys:
            raise ValueError('Invalid form inventory field.')
        identifier(value['field_ref'])
        for key, maximum in (('name',160),('section',400),('group',200),('input_type',40),('autocomplete',200),('descriptor_hash',64)):
            if type(value[key]) is not str or len(value[key]) > maximum:
                raise ValueError('Oversized form metadata.')
        if len(value['descriptor_hash']) != 64 or any(c not in '0123456789abcdef' for c in value['descriptor_hash']):
            raise ValueError('Invalid field descriptor.')
        if not value['name'].strip() or value['role'] not in ('textbox','combobox') or type(value['required']) is not bool or type(value['max_length']) is not int or not -1 <= value['max_length'] <= 1000000:
            raise ValueError('Unsupported form control.')
        if type(value['options']) is not list or len(value['options']) > 100 or any(type(v) is not str or len(v)>200 for v in value['options']):
            raise ValueError('Unsupported options.')
        if SENSITIVE.search(value['name']) or COMMIT.search(value['name']) or SECRET.search(canonical(value)):
            raise PermissionError('Protected form field.')
        return deepcopy(value)

    def request(self, connection, session_id, payload):
        try:
            validate_request(payload)
        except (ValueError, TypeError):
            # A malformed continuation cannot leave a partially accepted transfer
            # reusable. Verify ownership before touching any session buffers.
            if type(payload) is dict and payload.get('operation') == 'inventory':
                self.manager.session_store.require(connection, session_id, payload.get('identity'))
                args = payload.get('args')
                self.poison_inventory(session_id, args.get('inventory_id') if type(args) is dict else None)
            raise
        identity, operation, args = payload['identity'], payload['operation'], payload['args']
        session = self.manager.session_store.require(connection, session_id, identity)
        library = self.library()
        if operation == 'profiles':
            from src.resume.models import CATEGORIES
            return dict(profiles=library.list_profiles()[:8], categories=[dict(key=k,label=v) for k,v in CATEGORIES.items()])
        if operation == 'profile_page':
            profiles=library.list_profiles()
            page=[]
            for profile in profiles[args['offset']:args['offset']+8]:
                if len(canonical(dict(profiles=page+[profile],total=len(profiles))).encode('utf-8'))>20000: break
                page.append(profile)
            return dict(profiles=page,total=len(profiles))
        if operation == 'profile':
            profile = self.profile(args['profile_id'])
            return dict(profile={**{k:profile[k] for k in ('id','revision','name')},'entities':[]},rules=[])
        if operation == 'entity_page':
            entities=self.profile(args['profile_id'])['entities']
            return dict(entities=entities[args['offset']:args['offset']+8],total=len(entities))
        if operation == 'rules':
            profile = self.profile(args['profile_id'])
            rules = library.list_rules(profile['id'])
            result = dict(rules=[],total=len(rules))
            for rule in rules[args['offset']:args['offset']+4]:
                result['rules'].append(deepcopy(rule))
                try:
                    canonical(result,limit=20480)
                except ValueError:
                    result['rules'].pop()
                    if not result['rules']:
                        raise ValueError('A saved rule exceeds the supported response size.') from None
                    break
            return result
        if operation == 'manage':
            if args['profile_id'] is not None:
                self.profile(args['profile_id'])
            return dict(opened=True)
        if operation in ('delete', 'delete_rule'):
            self.profile(args['profile_id'])
            if operation == 'delete':
                library.delete_profile(args['profile_id'])
            else:
                if args['rule_id'] not in [r['id'] for r in library.list_rules(args['profile_id'])]:
                    raise PermissionError('Rule does not belong to the selected resume.')
                library.delete_rule(args['rule_id'])
            self.invalidate_profile(args['profile_id'])
            return dict(deleted=True)
        if operation == 'cancel_job':
            job = self.jobs.get(args['job_id'])
            if job and job['session_id'] == session_id and job['connection_id'] == connection:
                self.jobs.pop(args['job_id'],None)
            return dict(cancelled=True)
        if operation == 'job':
            job = self.valid_job(args['job_id'])
            if not job or job['session_id'] != session_id or job['connection_id'] != connection:
                raise PermissionError('AI matching job expired.')
            return dict(job_id=args['job_id'], state=job['state'], suggestions=job['suggestions'],code=job['code'])
        if operation == 'inventory':
            self.idle(session_id)
            self.no_separate_action(session_id)
            if self.active(session_id):
                raise PermissionError('Finish or stop the current fill first.')
            try:
                current = self.inventories.get(session_id)
                used = self.used_inventory_ids.setdefault(session_id, set())
                if args['sequence'] == 1:
                    if args['inventory_id'] in used or len(used) >= 64:
                        raise PermissionError('Inventory identity was already consumed; scan with fresh authorization.')
                    used.add(args['inventory_id'])
                    self.revoke(session_id, forget_inventory_ids=False)
                    current = dict(inventory_id=args['inventory_id'],fields=[],sequence=0,complete=False,digest=args['digest'],
                        expires_at=min(session.expires_at*1000,(self.manager.clock()+120)*1000))
                    self.inventories[session_id] = current
                if not current or current['inventory_id'] != args['inventory_id'] or current['complete'] or current['sequence']+1 != args['sequence'] or current['expires_at'] <= self.manager.clock()*1000 or current['digest'] != args['digest']:
                    raise PermissionError('Missing, expired or out-of-order inventory chunks.')
                fields = [self.field(v) for v in args['fields']]
                combined = current['fields'] + fields
                if len(combined)>50 or len({f['field_ref'] for f in combined}) != len(combined):
                    raise ValueError('Duplicate fields or inventory limit exceeded.')
                canonical(combined,limit=256*1024)
                if args['complete'] and digest(combined) != args['digest']:
                    raise ValueError('Inventory digest mismatch.')
                current.update(fields=combined,sequence=args['sequence'],complete=args['complete'])
                return dict(inventory_id=current['inventory_id'],complete=current['complete'])
            except (ValueError, TypeError, KeyError, PermissionError):
                self.poison_inventory(session_id,args['inventory_id'])
                raise
        if operation in ('teach', 'preview_rule'):
            from src.resume.teaching import build_rule
            self.idle(session_id)
            inv = self.inventory(session_id,args['inventory_id'])
            field = next((f for f in inv['fields'] if f['field_ref']==args['field_ref']),None)
            if field is None: raise PermissionError('Choose an inventoried field.')
            rule = build_rule(args['profile_id'],field,args['category'],args['entity_selector'],args['scope'],args['formatter'])
            if args['scope'].get('kind')=='site' and args['scope'].get('origin')!=session.origin:
                raise PermissionError('Site rule must use the current origin.')
            if operation == 'preview_rule':
                from src.resume.matching import match_fields
                profile = self.profile(args['profile_id'])
                assignments = next((p.get('assignments',{}) for p in self.plans.values()
                    if p['session_id']==session_id and p['profile_id']==args['profile_id'] and p['inventory_id']==inv['inventory_id']), {})
                row = match_fields(profile,[rule],[field],session.origin,assignments)[0]
                return {key:row[key] for key in ('category','value','eligible','reason')}
            saved = library.save_rule(rule)
            self.invalidate_profile(args['profile_id'])
            return dict(rule=saved)
        if operation == 'suggest':
            self.idle(session_id)
            inv = self.inventory(session_id,args['inventory_id'])
            self.profile(args['profile_id'])
            for job_id, job in tuple(self.jobs.items()):
                if not self.valid_job(job_id):
                    self.jobs.pop(job_id,None)
                elif (job['session_id']==session_id and job['connection_id']==connection and job['profile_id']==args['profile_id']
                    and job['inventory_id']==inv['inventory_id'] and job['runtime_choice']==args['runtime_choice']
                    and job['state'] in ('queued','answering','completed')):
                    return dict(job_id=job_id,state=job['state'])
            if len(self.jobs)>=8: raise PermissionError('AI matching job limit reached.')
            job_id=uuid4().hex
            self.jobs[job_id]=dict(connection_id=connection,session_id=session_id,identity=deepcopy(identity),profile_id=args['profile_id'],inventory_id=inv['inventory_id'],stamp=self.stamp(args['profile_id']),policy=self.manager.questions.revision(),expires_at=inv['expires_at'],state='queued',suggestions={},code='',runtime_choice=args['runtime_choice'])
            return dict(job_id=job_id,state='queued')
        if operation == 'prepare':
            from src.resume.matching import match_fields
            self.idle(session_id)
            self.no_separate_action(session_id)
            if self.active(session_id): raise PermissionError('Finish or stop the current fill first.')
            inv=self.inventory(session_id,args['inventory_id'])
            profile=self.profile(args['profile_id'])
            rules=library.list_rules(profile['id'])
            profile_stamp=self.record_stamp(profile,rules)
            suggestions={}
            for job in self.jobs.values():
                if job['session_id']==session_id and job['profile_id']==profile['id'] and job['state']=='completed' and self.valid_job(next(k for k,v in self.jobs.items() if v is job)):
                    suggestions.update(job['suggestions'])
            matched=match_fields(profile,rules,inv['fields'],session.origin,args['assignments'],suggestions)
            rows=[]
            for row in matched:
                field=next(f for f in inv['fields'] if f['field_ref']==row['field_ref'])
                target={k:field[k] for k in ('role','name','field_ref','descriptor_hash')}
                spec=None
                if row['eligible']:
                    spec=validate_spec(dict(action_type='select_option' if field['role']=='combobox' else 'set_text',target=target,value=row['value']),session.origin)
                rows.append(dict(**row,name=field['name'],section=field['section'],group=field['group'],spec=spec,selected=False,before=None,status='ready' if spec else 'skipped'))
            for key,p in tuple(self.plans.items()):
                if p['session_id']==session_id: self.plans.pop(key,None)
            if self.stamp(profile['id'])!=profile_stamp:
                raise PermissionError('Resume or rules changed during preparation; prepare again.')
            plan_id=uuid4().hex
            self.plans[plan_id]=dict(plan_id=plan_id,session_id=session_id,connection_id=connection,identity=deepcopy(identity),inventory_id=inv['inventory_id'],profile_id=profile['id'],stamp=profile_stamp,policy=self.manager.questions.revision(),rows=rows,status='draft',expires_at=min(inv['expires_at'],(self.manager.clock()+120)*1000),fingerprint='',cursor=0,inflight=None,assignments=deepcopy(args['assignments']))
            return dict(plan_id=plan_id,expires_at=int(self.plans[plan_id]['expires_at']))
        if operation=='cancel':
            plan=self.plans.get(args['plan_id'])
            if plan is not None:
                if plan['connection_id']!=connection or plan['session_id']!=session_id:
                    raise PermissionError('Fill plan belongs to another page session.')
                self._cancel_plan_action(plan)
                self.plans.pop(plan['plan_id'],None)
            return dict(cancelled=True)
        plan=self.plan(connection,session_id,args['plan_id'])
        self.idle(session_id)
        if operation=='rows':
            result=dict(plan_id=plan['plan_id'],rows=[],total=len(plan['rows']),expires_at=int(plan['expires_at']),status=plan['status'])
            for row in plan['rows'][args['offset']:args['offset']+4]:
                result['rows'].append(deepcopy(row))
                try:
                    canonical(result,limit=24576)
                except ValueError:
                    result['rows'].pop()
                    if not result['rows']:
                        raise ValueError('A resume row exceeds the supported response size.') from None
                    break
            return result
        self.manager.actions.require(connection,session_id,identity)
        if operation in ('observe','review'):
            if plan['status'] not in ('draft','preview'): raise PermissionError('Fill plan already consumed.')
            selected=args.get('selected',[])
            if len(set(selected))!=len(selected): raise ValueError('Duplicate selection.')
            observations={}
            for value in args['observations']:
                if type(value) is not dict or set(value)!={'field_ref','before'} or value['field_ref'] in observations:
                    raise ValueError('Invalid field observation.')
                observations[value['field_ref']]=validate_observation(value['before'])
            eligible={r['field_ref'] for r in plan['rows'] if r['eligible']}
            stored={r['field_ref'] for r in plan['rows'] if r['eligible'] and r['before'] is not None}
            if not set(selected)<=eligible or not set(observations)<=eligible or not set(selected)<=(set(observations)|stored):
                raise PermissionError('Selection contains an unreviewed field.')
            for row in plan['rows']:
                if row['field_ref'] in observations:
                    row['before']=observations[row['field_ref']]
                row['selected']=row['field_ref'] in selected
            if operation=='observe':
                plan.update(status='draft',fingerprint='')
                return dict(plan_id=plan['plan_id'],observed_count=sum(r['before'] is not None for r in plan['rows']))
            plan.update(status='preview',expires_at=min(plan['expires_at'],(self.manager.clock()+30)*1000))
            plan['fingerprint']=sha256(canonical({k:v for k,v in plan.items() if k!='fingerprint'},limit=2*1024*1024).encode('utf-8')).hexdigest()
            return dict(plan_id=plan['plan_id'],fingerprint=plan['fingerprint'],selected_count=len(selected),expires_at=int(plan['expires_at']))
        if operation=='approve':
            if plan['status']!='preview' or plan['fingerprint']!=args['fingerprint'] or not any(r['selected'] for r in plan['rows']):
                raise PermissionError('Review a fresh nonempty fill selection.')
            if any(owner==session_id for owner,_ in self.manager.actions.pending.values()):
                raise PermissionError('Cancel the separate controlled action first.')
            plan['status']='approved'
            return dict(plan_id=plan['plan_id'],status='approved')
        if operation=='claim':
            selected=[r for r in plan['rows'] if r['selected']]
            if plan['status']!='approved' or plan['inflight'] or plan['cursor']>=len(selected):
                raise PermissionError('No approved next field.')
            row=selected[plan['cursor']]
            if row['field_ref']!=args['field_ref']: raise PermissionError('Approved field order changed.')
            self.validate_target(session_id,row['spec']['target'])
            action=self.manager.actions.prepare(connection,session_id,dict(identity=identity,spec=row['spec'],before=row['before']),batch=plan['plan_id'])
            # Consume before the journal or browser can block. An error cannot replay this row.
            plan['cursor']+=1
            plan['inflight']=action['action_id']
            row['status']='unknown'
            try:
                claimed=self.manager.actions.claim(connection,session_id,dict(identity=identity,action_id=action['action_id'],fingerprint=action['fingerprint']))
                self.manager.actions.require(connection,session_id,identity)
                fresh=self.plan(connection,session_id,plan['plan_id'])
                pending=self.manager.actions.pending.get(action['action_id'])
                if fresh is not plan or fresh['status']!='approved' or fresh['inflight']!=action['action_id'] or pending is None or pending[0]!=session_id or pending[1] is not claimed or claimed['status']!='claimed' or claimed['expires_at']<=self.manager.clock()*1000:
                    raise PermissionError('Resume fill authority changed during claim.')
                self.validate_target(session_id,row['spec']['target'])
            except Exception:
                plan['status']='stopped'
                pending=self.manager.actions.pending.get(action['action_id'])
                if pending is not None and pending[0]==session_id:
                    self.manager.actions.cancel_session(session_id,force=True)
                else:
                    try:
                        self.manager.actions.journal.finish(action['action_id'],'unknown')
                    except Exception:
                        pass
                raise
            return dict(action=deepcopy(claimed))
        if operation=='result':
            if plan['inflight']!=args['action_id']: raise PermissionError('Unexpected fill result.')
            self.manager.actions.finish(connection,session_id,dict(identity=identity,action_id=args['action_id'],status=args['status']))
            selected=[r for r in plan['rows'] if r['selected']]
            selected[plan['cursor']-1]['status']=args['status']
            plan['inflight']=None
            if args['status']!='verified': plan['status']='stopped'
            elif plan['cursor']==len(selected): plan['status']='completed'
            return dict(status=plan['status'])
        raise ValueError('Unsupported resume operation.')

    def invalidate_profile(self, profile_id):
        for key,plan in tuple(self.plans.items()):
            if plan['profile_id']==profile_id:
                self.manager.actions.cancel_session(plan['session_id'],force=True)
                self.plans.pop(key,None)
        for key,job in tuple(self.jobs.items()):
            if job['profile_id']==profile_id: self.jobs.pop(key,None)

    def valid_job(self, job_id):
        job=self.jobs.get(job_id)
        if job:
            try:
                self.manager.session_store.require(job['connection_id'],job['session_id'],job['identity'])
                self.inventory(job['session_id'],job['inventory_id'])
                if job['expires_at']<=self.manager.clock()*1000 or job['policy']!=self.manager.questions.revision() or job['stamp']!=self.stamp(job['profile_id']): return None
                return job
            except (PermissionError,ValueError,KeyError): pass
        return None

    def complete_job(self, job_id, answer=None):
        job=self.valid_job(job_id)
        if not job: return
        try:
            result=json.loads(answer) if type(answer) is str else answer
            if type(result) is not dict or set(result)!={'suggestions'} or type(result['suggestions']) is not list or len(result['suggestions'])>50: raise ValueError('Invalid model output.')
            from src.resume.models import CATEGORIES
            fields={f['field_ref'] for f in self.inventory(job['session_id'],job['inventory_id'])['fields']}
            suggestions={}
            for row in result['suggestions']:
                if type(row) is not dict or set(row)!={'field_ref','category'} or row['field_ref'] not in fields or row['category'] not in CATEGORIES or row['field_ref'] in suggestions: raise ValueError('Invalid model suggestion.')
                suggestions[row['field_ref']]=row['category']
            job.update(state='completed',suggestions=suggestions)
        except (ValueError,TypeError,KeyError):
            job.update(state='failed',suggestions={},code='provider_unavailable')
