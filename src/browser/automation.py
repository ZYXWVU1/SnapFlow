"""Browser adapter for the existing immutable AutomationSkill and run lifecycle."""
from dataclasses import dataclass
import json
import time

from src.agent.models import digest, snapshot, thaw
from src.automation.runtime import AutomationRun
from src.automation.parameters import bind, references, validate_inputs
from src.automation.storage import content_hash
from src.automation.validation import validate_definition


@dataclass(frozen=True)
class BrowserStepPreview:
    allowed: bool
    title: str
    target: str
    proposed: object
    reason: str = ''


class BrowserAutomationRuntime:
    def __init__(self, gateway, session_provider, *, enabled=lambda: False):
        self.gateway, self.session_provider, self.enabled = gateway, session_provider, enabled

    def _guard(self, run=None, session_id=None):
        if not self.enabled() or run and (run.stopped.is_set() or run.status not in ('ready','running','paused')):
            raise PermissionError('Browser procedure stopped; attempted effects cannot be replayed.')
        if run and run.identity != digest([run.skill.to_dict(),run.inputs]):
            raise PermissionError('Skill or inputs changed; review a new run.')
        manager=self.gateway.manager
        session_id=session_id or (getattr(run,'browser_session_id',None) if run else None) or self.session_provider()
        with manager.lock:
            session=next((s for s in manager.sessions() if s.session_id==session_id),None)
            if session is None:
                raise PermissionError('Assist the current document and allow interactions again.')
            manager.actions.require(session.connection_id,session.session_id,manager.session_store.identity(session))
            manager.context.source(session.session_id)
            if run and digest(thaw(manager.context.latest(session.session_id).data)) != run.browser_snapshot_digest:
                raise PermissionError('Reviewed page inventory changed. Pause and explicitly rebind the remaining steps.')
            if run and session.origin not in run.skill.supported_origins:
                raise PermissionError('This website is outside the saved skill scope.')
            return session

    def prepare(self,skill,inputs,*,session_id=None):
        if skill.domain!='browser':
            raise ValueError('Choose a browser procedure.')
        validate_definition(skill)
        if skill.status in ('disabled','archived') or skill.trust=='imported_unreviewed':
            raise PermissionError('Create a reviewed draft before running an imported or disabled version.')
        session=self._guard(session_id=session_id)
        if session.origin not in skill.supported_origins:
            raise PermissionError('This website is outside the saved skill scope.')
        if self.gateway.manager.actions.journal.pending_review():
            raise PermissionError('Review uncertain writes in Recovery before another procedure.')
        values=validate_inputs(skill.inputs,inputs)
        if not references(skill.to_dict())<=set(values):
            raise ValueError('Supply all declared inputs.')
        run=AutomationRun(skill,snapshot(values))
        run.identity=digest([skill.to_dict(),run.inputs])
        run.browser_session_id=session.session_id
        run.browser_snapshot_digest=digest(thaw(self.gateway.manager.context.latest(session.session_id).data))
        return run

    def preview(self,run,index=None):
        session=self._guard(run)
        index=run.index if index is None else index
        step=run.skill.steps[index]
        target=step.target_template.target(lambda v:bind(v,run.inputs))
        if not step.executable:
            return BrowserStepPreview(True,step.title,target['name'],None,'Manual checkpoint; no executable operation.')
        spec=step.target_template.spec(step.action_type,step.value_template,lambda v:bind(v,run.inputs))
        if step.target_template.origin!=session.origin:
            return BrowserStepPreview(False,step.title,target['name'],spec['value'],'Assist the declared destination before continuing.')
        # Dry run checks the reviewed, value-free inventory. Each actual attempt
        # still resolves again and presents current/proposed state in the sidebar.
        data=thaw(self.gateway.manager.context.latest(session.session_id).data)
        matches=[e for e in data['interactive_elements'] if e['role']==target['role'] and (e['accessible_name'] or e['label'])==target['name']]
        allowed=spec['action_type']=='scroll' or len(matches)==1 and matches[0]['visible'] and matches[0]['enabled'] and not matches[0]['sensitive']
        if spec['action_type']=='set_text' and matches:
            allowed=allowed and matches[0]['editable']
        # Conditions requiring extra page state have no implicit read fallback.
        conditions=(*run.skill.preconditions,*step.preconditions,*step.postconditions,
            *(run.skill.postconditions if index==len(run.skill.steps)-1 else ()))
        reason='' if allowed else 'Target missing, ambiguous or unavailable in the reviewed inventory.'
        if conditions:
            allowed=False
            reason='Additional state conditions require review; this adapter verifies each requested control state only.'
        return BrowserStepPreview(bool(allowed),step.title,target['name'],spec['value'],reason)

    def dry_run(self,run):
        return tuple(self.preview(run,i) for i in range(run.index,len(run.skill.steps)))

    def execute_next(self,run,*,manual_confirmed=False,cancelled=lambda:False,guard=lambda:True):
        self._guard(run)
        if cancelled() or guard() is False:
            self.stop(run)
            raise PermissionError('Browser procedure cancelled.')
        if run.index>=len(run.skill.steps):
            raise PermissionError('This procedure has already completed.')
        step=run.skill.steps[run.index]
        preview=self.preview(run)
        if not preview.allowed:
            run.status='paused'
            return dict(success=False,status='paused',browser_verified=False)
        if not step.executable:
            if not manual_confirmed:
                run.status='paused'
                return dict(success=False,status='manual_checkpoint')
            result=dict(success=True,status='manual_confirmed',manual_confirmed=True)
        else:
            if run.index in run.requests:
                raise PermissionError('An attempted browser action cannot be replayed.')
            run.requests[run.index]='attempted'
            run.status='running'
            spec=step.target_template.spec(step.action_type,step.value_template,lambda v:bind(v,run.inputs))
            try:
                def continuity():
                    if (not self.enabled() or run.stopped.is_set() or cancelled() or guard() is False
                            or run.identity != digest([run.skill.to_dict(),run.inputs])):
                        raise PermissionError('Browser procedure authorization changed.')
                    return True
                def current():
                    continuity()
                    self._guard(run)
                    return True
                result=self.gateway.execute(run.browser_session_id,spec,
                    cancelled=lambda:run.stopped.is_set() or cancelled(),guard=current,terminal_guard=continuity)
            except Exception:
                run.status='interrupted'
                raise
            if not result.get('success') or result.get('browser_verified') is not True:
                run.results.append(result)
                run.status='interrupted'
                return result
        run.results.append(result)
        run.index+=1
        run.status='completed' if run.index==len(run.skill.steps) else 'paused' if step.action_type=='browser_controlled_navigation' else 'ready'
        return result

    def rebind(self,run):
        if run.status!='paused' or run.index in run.requests or run.stopped.is_set():
            raise PermissionError('Only an unattempted paused step can bind a newly reviewed document.')
        session=self._guard(session_id=self.session_provider())
        if session.origin not in run.skill.supported_origins:
            raise PermissionError('The reviewed website is outside this procedure.')
        run.browser_session_id=session.session_id
        run.browser_snapshot_digest=digest(thaw(self.gateway.manager.context.latest(session.session_id).data))
        run.status='ready'

    def stop(self,run):
        run.stopped.set()
        if run.status!='completed':run.status='stopped'


class BrowserAutomationCapabilityProvider:
    def __init__(self,store_provider,runtime_provider):
        self.store_provider,self.runtime_provider=store_provider,runtime_provider

    def register(self,registry,session,bundle):
        from dataclasses import replace
        from src.agent.capabilities import AgentCapability
        from src.automation.agent import input_schema
        sources=[s for s in bundle.sources if s.source_type=='browser_dom']
        if len(sources)!=1 or not session.constraints.allow_external_writes:return
        source=sources[0]
        try:
            runtime=self.runtime_provider()
            runtime.gateway.manager.context.validate_sources((source,))
            browser_id=json.loads(source.content)['browser_session_id']
            browser=runtime._guard(session_id=browser_id)
            store=self.store_provider()
        except Exception:return
        for skill in store.search(origin=browser.origin,published_only=True)[:20]:
            if skill.domain!='browser' or len(registry._entries)>=128:continue
            expected=content_hash(skill)
            descriptor=AgentCapability('browser_skill:'+skill.skill_id+':'+skill.version_id,skill.name,
                ('Reviewed browser procedure. Every effect requires exact approval in the browser sidebar. Manual checkpoints pause execution.\n'+
                 '\n'.join(step.title+' · '+step.action_type for step in skill.steps))[:2000],
                'run_skill',input_schema(skill),None,'external_write',True,True,'browser_automation_skill:'+skill.version_id)
            def live(skill=skill,expected=expected,descriptor=descriptor):
                try:
                    runtime.gateway.manager.context.validate_sources((source,))
                    runtime._guard(session_id=browser_id)
                    current=store.get(skill.version_id)
                    return replace(descriptor,availability=current.status=='published' and content_hash(current)==expected)
                except Exception:return None
            def invoke(inputs,*,cancelled=None,guard=None,constraints=None,skill=skill,live=live):
                if guard is None:raise PermissionError('Browser procedures require the live Agent guard.')
                cancelled=cancelled or (lambda:False)
                effective=constraints or session.constraints
                run=runtime.prepare(skill,inputs,session_id=browser_id)
                started=time.perf_counter()
                try:
                    while run.index<len(skill.steps):
                        if cancelled() or guard() is False or live() is None:raise PermissionError('Browser procedure authorization changed.')
                        if run.index>=effective.max_ui_actions:raise PermissionError('Agent action budget exhausted.')
                        # Agent cannot attest completion of a human-only checkpoint.
                        if not skill.steps[run.index].executable:
                            run.status='paused';break
                        result=runtime.execute_next(run,cancelled=cancelled,guard=guard)
                        if run.status!='ready':break
                except Exception:
                    run.status='interrupted' if run.results else 'paused'
                finally:
                    store.record_usage(run,(time.perf_counter()-started)*1000)
                    run.inputs={}
                complete=run.status=='completed'
                return dict(success=complete,status='verified' if complete else 'unknown' if run.status=='interrupted' else 'paused',
                    browser_verified=complete,step_count=len(run.results),action_id=run.run_id)
            registry.register(descriptor,invoke,refresh=live)
