import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.integrations.models import IntegrationConnection
from src.integrations.registry import IntegrationRegistry
from src.integrations.credentials import CredentialService
from src.integrations.storage import ConnectionStorage


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, target):
        return self.values.get(target)

    def set(self, target, value):
        self.values[target] = value

    def delete(self, target):
        self.values.pop(target, None)


class IntegrationFoundationTests(unittest.TestCase):
    def test_registry_has_scoped_providers_and_capabilities(self):
        registry = IntegrationRegistry()
        self.assertEqual({item.id for item in registry.definitions()}, {'google', 'todoist'})
        self.assertEqual(registry.for_capability('google_calendar').id, 'google')
        self.assertEqual(registry.for_capability('google_sheets').id, 'google')
        self.assertEqual(registry.for_capability('todoist_tasks').id, 'todoist')
        self.assertIsNone(registry.for_capability('arbitrary_http'))
        with self.assertRaises(KeyError):
            registry.get('unknown')

    def test_connection_model_rejects_secret_fields(self):
        connected = IntegrationConnection('google', 'connected', 'user@example.com',
                                          granted_capabilities=('google_calendar',))
        payload = connected.to_dict()
        self.assertEqual(payload['account_label'], 'user@example.com')
        self.assertNotIn('token', json.dumps(payload).lower())
        with self.assertRaises(ValueError):
            IntegrationConnection.from_dict(payload | {'access_token': 'secret'})
        with self.assertRaises(ValueError):
            IntegrationConnection('google', 'unauthorized')

    def test_credentials_are_separate_from_connection_metadata(self):
        backend = MemorySecrets()
        credentials = CredentialService(backend)
        self.assertIsNone(credentials.get('google'))
        credentials.set('google', 'private-value')
        self.assertEqual(credentials.get('google'), 'private-value')
        self.assertEqual(len(backend.values), 1)
        self.assertTrue(next(iter(backend.values)).startswith('SnapFlow:integration:'))
        credentials.delete('google')
        self.assertIsNone(credentials.get('google'))
        with self.assertRaises(ValueError):
            credentials.set('../google', 'bad')

    def test_metadata_storage_round_trip_and_rejects_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'integrations.json'
            storage = ConnectionStorage(path)
            connection = IntegrationConnection('todoist', 'connected', 'Todoist account')
            storage.save(connection)
            self.assertEqual(ConnectionStorage(path).get('todoist'), connection)
            self.assertNotIn('private', path.read_text(encoding='utf-8').lower())
            with self.assertRaises(ValueError):
                storage.save({'integration_id': 'google', 'access_token': 'private'})
            self.assertEqual(ConnectionStorage(path).get('todoist'), connection)

    def test_damaged_metadata_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'integrations.json'
            path.write_text('{', encoding='utf-8')
            storage = ConnectionStorage(path)
            self.assertTrue(storage.warning)
            with self.assertRaises(ValueError):
                storage.save(IntegrationConnection('google', 'connected'))
            self.assertEqual(path.read_text(encoding='utf-8'), '{')

    def test_boolean_metadata_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'integrations.json'
            path.write_text('{"version":true,"connections":[]}', encoding='utf-8')
            self.assertTrue(ConnectionStorage(path).warning)

    def test_concurrent_connection_updates_preserve_both_services(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'integrations.json'
            storage = ConnectionStorage(path)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(storage.save, IntegrationConnection(key, 'connected'))
                           for key in ('google', 'todoist')]
                for future in futures:
                    future.result()
            self.assertEqual({item.integration_id for item in ConnectionStorage(path).list_connections()},
                             {'google', 'todoist'})
