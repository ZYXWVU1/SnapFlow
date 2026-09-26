"""Fixed-origin HTTP boundary with safe errors and no automatic write retries."""
import httpx


ALLOWED_ORIGINS = frozenset({
    'https://api.todoist.com/api/v1/',
    'https://www.googleapis.com/calendar/v3/',
    'https://sheets.googleapis.com/v4/',
})


class ApiFailure(RuntimeError):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


class FixedApiClient:
    def __init__(self, base_url, token, *, transport=None, timeout=15.0):
        if base_url not in ALLOWED_ORIGINS:
            raise ValueError('Unsupported service origin.')
        self.base_url = base_url
        self.token = token
        self.client = httpx.Client(base_url=base_url, transport=transport,
                                   timeout=timeout, follow_redirects=False)

    def request(self, method, path, *, json=None, params=None, write=False):
        if method not in ('GET', 'POST') or not isinstance(path, str) or not path or \
                path.startswith('/') or '://' in path or '..' in path:
            raise ValueError('Unsupported service request.')
        token = self.token()
        if not token:
            raise ApiFailure('disconnected', 'Integration is not connected.')
        try:
            response = self.client.request(method, path, json=json, params=params,
                headers={'Authorization': 'Bearer ' + token})
        except httpx.RequestError:
            if write:
                raise ApiFailure('unknown_result', 'The service response is unknown. Review the account before retrying.') from None
            raise ApiFailure('failed', 'Unable to reach the service. Try again later.') from None
        if response.status_code in (401, 403):
            raise ApiFailure('needs_reconnect', 'Service authorization needs attention.')
        if not 200 <= response.status_code < 300:
            raise ApiFailure('failed', 'The service could not complete the request.')
        try:
            return response.json()
        except ValueError:
            raise ApiFailure('failed', 'The service returned an unreadable response.') from None

    def close(self):
        self.client.close()
