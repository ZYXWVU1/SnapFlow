"""Fixed-origin HTTP boundary with safe errors and no automatic write retries."""
import httpx

from src.network_policy import NetworkPolicyError, get_network_policy, require_http_request


ALLOWED_ORIGINS = frozenset({
    'https://api.todoist.com/api/v1/',
    'https://www.googleapis.com/calendar/v3/',
    'https://sheets.googleapis.com/v4/',
})


class ApiFailure(RuntimeError):
    def __init__(self, kind, message, status_code=None):
        self.kind = kind
        self.status_code = status_code
        super().__init__(message)


class FixedApiClient:
    def __init__(self, base_url, token, *, transport=None, timeout=15.0, execution_guard=None, execution_journal=None):
        if base_url not in ALLOWED_ORIGINS:
            raise ValueError('Unsupported service origin.')
        self.base_url = base_url
        self.token = token
        self.execution_guard = execution_guard
        self.execution_journal = execution_journal
        self.client = httpx.Client(base_url=base_url, transport=transport,
                                   timeout=timeout, follow_redirects=False, trust_env=False,
                                   event_hooks={'request': [self._enforce_request]})

    def request(self, method, path, *, json=None, params=None, write=False):
        if method not in ('GET', 'POST') or not isinstance(path, str) or not path or \
                path.startswith('/') or '://' in path or '..' in path:
            raise ValueError('Unsupported service request.')
        write = write or method != 'GET'
        if write:
            from src.reliability.policy import get_safety_policy
            if not get_safety_policy().allows('external_write'):
                raise ApiFailure('safe_mode', 'Safe Mode blocks external writes.')
        self._check_guard()
        purpose = 'integration_write' if write else 'integration_read'
        get_network_policy().require_allowed(self.base_url, purpose)
        token = self.token()
        self._check_guard()
        get_network_policy().require_allowed(self.base_url, purpose)
        if not token:
            raise ApiFailure('disconnected', 'Integration is not connected.')
        invocation = None
        if write and self.execution_journal is not None:
            try:
                invocation = self.execution_journal.plan('integration_write')
                self.execution_journal.started(invocation)
            except Exception:
                raise ApiFailure('journal_unavailable', 'Write not sent: recovery journal is unavailable.') from None
        def finish(status):
            if invocation is not None:
                try:
                    self.execution_journal.finish(invocation, status)
                except Exception:
                    raise ApiFailure('unknown_result', 'Write result could not be recorded. Review the account before retrying.') from None
        try:
            response = self.client.request(method, path, json=json, params=params,
                headers={'Authorization': 'Bearer ' + token})
        except (ApiFailure, NetworkPolicyError):
            # The request hook rejected dispatch after journal startup. No
            # external write was sent, so it does not need ambiguity review.
            finish('failed')
            raise
        except httpx.RequestError:
            if write:
                finish('unknown')
                raise ApiFailure('unknown_result', 'The service response is unknown. Review the account before retrying.') from None
            raise ApiFailure('failed', 'Unable to reach the service. Try again later.') from None
        if response.status_code in (401, 403):
            finish('failed')
            raise ApiFailure('needs_reconnect', 'Service authorization needs attention.',
                             response.status_code)
        if not 200 <= response.status_code < 300:
            if write and not 400 <= response.status_code < 500:
                finish('unknown')
                raise ApiFailure('unknown_result',
                    'The service response is unknown. Review the account before retrying.', response.status_code)
            finish('failed')
            raise ApiFailure('failed', 'The service could not complete the request.')
        try:
            payload = response.json()
        except ValueError:
            finish('unknown' if write else 'failed')
            raise ApiFailure('unknown_result' if write else 'failed', 'The service returned an unreadable response. Review the account before retrying.') from None
        if write and not isinstance(payload, dict):
            finish('unknown')
            raise ApiFailure('unknown_result', 'Write response was malformed. Review the account before retrying.')
        finish('completed')
        return payload

    def close(self):
        self.client.close()

    def _check_guard(self):
        if self.execution_guard is not None and not self.execution_guard():
            raise ApiFailure('cancelled', 'Workflow request cancelled or revoked.')

    def _enforce_request(self, request):
        self._check_guard()
        if request.method != 'GET':
            from src.reliability.policy import get_safety_policy
            if not get_safety_policy().allows('external_write'):
                raise ApiFailure('safe_mode', 'Safe Mode blocks external writes.')
        require_http_request(request, 'integration_read' if request.method == 'GET' else 'integration_write')
