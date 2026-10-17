"""Single bounded desktop action gateway. Agents never access the native provider."""
from dataclasses import replace
from collections import deque
import hashlib
import os
from threading import Event, RLock, Lock
import time
from uuid import uuid4

from src.reliability.policy import get_safety_policy
from src.observability.performance import emit, trace
from .models import DesktopActionPreview, DesktopActionResult, TargetResolution, expired, now
from .policy import classify, review_state, validate_action
from .resolver import DesktopTargetResolver
from .verification import DesktopActionVerifier

_EXECUTION_LOCK = Lock()


class DesktopActionService:
    def __init__(self, provider, journal, *, safety_policy=None, enabled=lambda: False,
                 private_mode=lambda: False, local_processes=(), minimum_interval=.25,
                 allow_owned_test_window=False):
        self.provider, self.journal = provider, journal
        self.safety_policy = safety_policy or get_safety_policy()
        self.enabled, self.private_mode = enabled, private_mode
        self.local_processes = tuple(local_processes)
        # Trusted acceptance fixtures only; never persisted or planner-configurable.
        self.allow_owned_test_window = allow_owned_test_window is True
        self.minimum_interval = max(0, float(minimum_interval))
        self.resolver = DesktopTargetResolver()
        self.verifier = DesktopActionVerifier(self.resolver)
        self.lock, self.cancelled = RLock(), Event()
        self.fence = None
        self.previews, self.approvals = {}, {}
        self.actions, self.commits = 0, 0
        self.recent = deque(maxlen=4)

    def activate_fence(self, fence):
        with self.lock:
            self.stop()
            self.fence = fence
            self.actions = self.commits = 0
            self.cancelled = Event()

    def stop(self):
        with self.lock:
            self.cancelled.set()
            self.fence = None
            self.previews.clear()
            self.approvals.clear()

    def _boundary(self, request=None, fence=None):
        if self.cancelled.is_set():
            raise InterruptedError('Desktop actions were stopped.')
        if not self.enabled() or not self.safety_policy.allows('desktop_actions'):
            raise PermissionError('Controlled Desktop Actions are disabled or Safe Mode is active.')
        live = self.fence
        if live is None or expired(live.expires_at) or fence is not None and live != fence:
            raise PermissionError('The approved application fence expired or changed.')
        if live.allowed_process_id == os.getpid() and not self.allow_owned_test_window:
            raise PermissionError('Desktop actions cannot control SnapFlow itself or its security settings.')
        if request and (expired(request.expires_at) or request.agent_session_id != live.session_id or
                        request.action_type not in live.allowed_action_types):
            raise PermissionError('The action is outside the current fence or has expired.')
        return live

    def _state(self, fence, *, cancelled=lambda: False):
        with trace('desktop_target_resolution', 'desktop_actions'):
            state = self.provider.capture(fence, cancelled=lambda: self.cancelled.is_set() or cancelled())
        if not state.security_available:
            raise PermissionError('Unavailable due to Windows security boundary.')
        if (state.process_id, state.window_id, state.process_name.casefold()) != (
                fence.allowed_process_id, fence.allowed_window, fence.allowed_process_name.casefold()):
            raise ValueError('The fenced application or window changed.')
        return state

    def preview(self, request):
        resolution = TargetResolution('target_not_found', 0, 0)
        fence = None
        risk, error, state_hash = 'unknown', None, None
        try:
            fence = self._boundary(request)
            state = self._state(fence)
            resolution = self.resolver.resolve(request.target, state)
            if resolution.status != 'resolved':
                raise ValueError('The target is missing, ambiguous or lacks sufficient semantic confidence.')
            element = resolution.resolved_element
            risk = classify(request, element, local_processes=self.local_processes)
            if risk in ('ui_sensitive', 'ui_destructive'):
                raise PermissionError('Protected, sensitive and destructive actions are disabled.')
            if self.private_mode() and risk in ('ui_external_edit', 'ui_external_commit', 'unknown'):
                raise PermissionError('Private Mode blocks actions that may transmit content externally.')
            error = validate_action(request, element)
            if error:
                raise ValueError(error)
            if request.action_type == 'invoke' and not request.expected_postconditions:
                # Allowed only with explicit review; the eventual result remains unverified.
                pass
            if not self.verifier.matches(request.expected_preconditions, state):
                raise ValueError('The reviewed action preconditions do not hold.')
            self._boundary(request, fence)
            state_hash = review_state(request, element)
        except InterruptedError:
            error = 'Desktop actions were stopped.'
        except PermissionError as exc:
            error = str(exc)
        except Exception:
            error = error or 'Target resolution or precondition failed. Refresh the desktop context.'
        preview = DesktopActionPreview(uuid4().hex, request, resolution, risk, error is None,
            error, fence.fence_id if fence else None, state_hash)
        with self.lock:
            if len(self.previews) >= 64:
                self.previews.clear()
            self.previews[preview.preview_id] = preview
        emit('desktop_action_requested', component='desktop_actions', properties={'action': request.action_type, 'risk': risk})
        if resolution.status == 'resolved':
            emit('desktop_target_resolved', component='desktop_actions', properties={'action': request.action_type})
        return preview

    def approve(self, preview):
        """Trusted user interaction only. A caller cannot manufacture an approved preview."""
        with self.lock:
            if self.previews.pop(preview.preview_id, None) is not preview or not preview.allowed or expired(preview.expires_at):
                raise PermissionError('Review a fresh desktop action preview.')
            fence = self._boundary(preview.request)
            if fence.fence_id != preview.fence_id:
                raise PermissionError('The fence changed during review.')
            token = uuid4().hex
            if len(self.approvals) >= 64:
                self.approvals.clear()
            self.approvals[token] = preview
        emit('desktop_action_approved', component='desktop_actions', properties={'action': preview.request.action_type, 'risk': preview.risk_level})
        return token

    def execute(self, request, approval_token, *, cancelled=lambda: False, guard=None):
        started = now()
        invocation, sent, before, stage = None, False, None, 'approval'
        def result(status, **values):
            return DesktopActionResult(request.action_id, status, started_at=started, finished_at=now(), **values)
        if not _EXECUTION_LOCK.acquire(blocking=False):
            return result('blocked', safe_error='Another desktop action is running.')
        try:
            with self.lock:
                preview = self.approvals.pop(approval_token, None)
                if not preview or preview.request.fingerprint != request.fingerprint or expired(preview.expires_at):
                    return result('permission_denied', safe_error='Review and approve this exact action once.')
                fence = self._boundary(request)
                if preview.fence_id != fence.fence_id:
                    return result('stale_target')
            if cancelled():
                return result('cancelled')
            if guard:
                guard()
            stage = 'target_resolution'
            before = self._state(fence, cancelled=cancelled)
            if before.foreground_window != fence.allowed_window:
                return result('stale_target', safe_error='The target application is no longer active.')
            resolution = self.resolver.resolve(request.target, before)
            if resolution.status != 'resolved':
                return result(resolution.status if resolution.status in ('target_not_found', 'target_ambiguous') else 'stale_target')
            element = resolution.resolved_element
            risk = classify(request, element, local_processes=self.local_processes)
            if risk != preview.risk_level or risk in ('ui_sensitive', 'ui_destructive') or validate_action(request, element):
                return result('blocked')
            if self.private_mode() and risk in ('ui_external_edit', 'ui_external_commit', 'unknown'):
                return result('permission_denied')
            if review_state(request, element) != preview.state_hash or not self.verifier.matches(request.expected_preconditions, before):
                return result('stale_target', safe_error='The control changed after review. User edits take precedence.')
            if self.actions >= fence.max_ui_actions or risk == 'ui_external_commit' and self.commits >= fence.max_external_commits:
                return result('blocked', safe_error='The desktop action budget is exhausted.')
            if self.recent and time.monotonic() - self.recent[-1] < self.minimum_interval:
                return result('blocked', safe_error='Desktop action rate limit. Review before continuing.')
            self._boundary(request, fence)
            if cancelled():
                return result('cancelled')
            if guard:
                guard()
            # Deterministic opaque identity makes a repeat of an attempted request fail after restart.
            identity = hashlib.sha256((request.agent_session_id + ':' + request.action_id).encode()).hexdigest()[:32]
            stage = 'journal'
            invocation = self.journal.claim_desktop(identity)
            self.journal.approved(invocation)
            self.journal.started(invocation)
            self.actions += 1
            self.commits += risk == 'ui_external_commit'
            self.recent.append(time.monotonic())
            no_op = (request.action_type == 'toggle' and element.state.get('toggle_state') == request.value or
                request.action_type == 'select' and element.state.get('selected') is True or
                request.action_type in ('expand', 'collapse') and element.state.get('expanded') == (request.action_type == 'expand') or
                request.action_type == 'set_value' and element.state.get('value') == request.value)
            if no_op:
                method = 'no_action_needed'
            else:
                stage = 'dispatch'
                sent = True  # Any lost result after entering dispatch is unknown, never retryable.
                def dispatch_cancelled():
                    if cancelled() or self.cancelled.is_set() or not self.enabled() or not self.safety_policy.allows('desktop_actions') or self.fence != fence or expired(fence.expires_at) or expired(preview.expires_at) or self.private_mode() and risk in ('ui_external_edit', 'ui_external_commit', 'unknown'):
                        return True
                    foreground = getattr(self.provider, 'foreground_matches', None)
                    if foreground is not None and not foreground(fence):
                        return True
                    try:
                        if guard:
                            guard()
                    except Exception:
                        return True
                    return False
                with trace('desktop_uia_execution', 'desktop_actions'):
                    method = self.provider.execute(request, element, fence,
                        cancelled=dispatch_cancelled)
            stage = 'verification'
            self.journal.checkpoint_desktop(invocation, 'action_sent')
            self.journal.checkpoint_desktop(invocation, 'verification_started')
            with trace('desktop_post_verification', 'desktop_actions'):
                after = self._state(fence, cancelled=cancelled)
                verification = self.verifier.verify(request, before, after)
            self.journal.finish(invocation, 'completed' if verification.verified else 'unknown')
            invocation = None
            event = 'desktop_action_completed' if verification.verified else 'desktop_action_verification_failed'
            emit(event, component='desktop_actions', success=verification.verified,
                properties={'action': request.action_type, 'risk': risk})
            return result('success' if verification.verified else 'verification_failed', target_resolved=True,
                execution_method=method, before_state=element.state,
                after_state=next((e.state for e in after.elements if e.fingerprint == element.fingerprint), None),
                verification_result=verification)
        except InterruptedError:
            return result('interrupted' if sent else 'cancelled', safe_error='Stopped. Review any action already dispatched.')
        except PermissionError:
            return result('permission_denied' if not sent else 'interrupted', safe_error='Action permission or Windows security boundary blocked execution.')
        except Exception as error:
            return result('interrupted' if sent else 'blocked', warnings=(stage, type(error).__name__),
                safe_error='Action outcome unavailable. Review the current UI; do not repeat a write.')
        finally:
            if invocation:
                try:
                    self.journal.finish(invocation, 'unknown' if sent else 'failed')
                except Exception:
                    pass  # An unfinished durable record will be recovered as interrupted.
            _EXECUTION_LOCK.release()
