import unittest

import httpx

from src.integrations.http import ApiFailure, FixedApiClient


class FixedApiClientTests(unittest.TestCase):
    def test_fixed_origin_and_bearer_header(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={'results': []})
        client = FixedApiClient('https://api.todoist.com/api/v1/', lambda: 'test-token',
                                transport=httpx.MockTransport(respond))
        self.assertEqual(client.request('GET', 'projects'), {'results': []})
        self.assertEqual(str(seen[0].url), 'https://api.todoist.com/api/v1/projects')
        self.assertEqual(seen[0].headers['Authorization'], 'Bearer test-token')
        with self.assertRaises(ValueError):
            client.request('GET', 'https://example.com/private')

    def test_authorization_and_uncertain_write_outcomes(self):
        unauthorized = FixedApiClient('https://api.todoist.com/api/v1/', lambda: 'expired',
            transport=httpx.MockTransport(lambda request: httpx.Response(401)))
        with self.assertRaises(ApiFailure) as caught:
            unauthorized.request('GET', 'projects')
        self.assertEqual(caught.exception.kind, 'needs_reconnect')
        def timeout(request):
            raise httpx.ReadTimeout('sensitive request text')
        uncertain = FixedApiClient('https://api.todoist.com/api/v1/', lambda: 'test-token',
                                    transport=httpx.MockTransport(timeout))
        with self.assertRaises(ApiFailure) as caught:
            uncertain.request('POST', 'tasks', json={'content': 'Task'}, write=True)
        self.assertEqual(caught.exception.kind, 'unknown_result')
        self.assertNotIn('sensitive', str(caught.exception))

    def test_missing_token_and_server_error_are_safe(self):
        client = FixedApiClient('https://api.todoist.com/api/v1/', lambda: None,
                                transport=httpx.MockTransport(lambda request: httpx.Response(200)))
        with self.assertRaises(ApiFailure) as caught:
            client.request('GET', 'projects')
        self.assertEqual(caught.exception.kind, 'disconnected')
        failing = FixedApiClient('https://api.todoist.com/api/v1/', lambda: 'token',
                                 transport=httpx.MockTransport(lambda request: httpx.Response(503)))
        with self.assertRaises(ApiFailure) as caught:
            failing.request('GET', 'projects')
        self.assertEqual(caught.exception.kind, 'failed')
