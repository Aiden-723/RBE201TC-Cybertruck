import importlib.util
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from collectors import LinuxCollector
from service import Telemetry


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('server'), 'HTTP server missing')
        import server
        self.assertTrue(hasattr(server, 'create_server'), 'HTTP factory missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = server.create_server('127.0.0.1', 0, collector=LinuxCollector(Path(self.temp.name)), telemetry=Telemetry())
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, method='GET', headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), json.loads(body) if body else None
        finally:
            connection.close()

    def test_idle_client_has_bounded_lifetime(self):
        import socket
        self.assertTrue(hasattr(self.server, 'request_deadline'), 'request lifetime unbounded')
        self.server.request_deadline = 0.15
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            client.settimeout(2)
            client.sendall(b'GET /api/v1/health HTTP/1.1\r\nHost: test\r\n')
            self.assertEqual(client.recv(1), b'')
        self.assertEqual(self.request('/api/v1/health')[0], 200)

    def test_authentication_write_rejection_and_bind_policy(self):
        from unittest.mock import patch
        import os
        import server
        self.server.token = 'x' * 32
        self.assertEqual(self.request('/api/v1/health')[0], 401)
        self.assertEqual(self.request('/api/v1/health', headers={'Authorization': 'Bearer wrong'})[0], 401)
        headers = {'Authorization': 'Bearer ' + 'x' * 32}
        self.assertEqual(self.request('/api/v1/health', headers=headers)[0], 200)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT', 'HEAD'):
            code, response_headers, _ = self.request('/api/v1/status', method, headers)
            self.assertEqual(code, 405, method)
            self.assertEqual(response_headers['Allow'], 'GET')
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                server.create_server('0.0.0.0', 0)
        with patch.dict(os.environ, {'DEVICE_CONSOLE_TOKEN': 'x' * 32}, clear=True):
            bound = server.create_server('0.0.0.0', 0)
            try:
                self.assertEqual(bound.token, 'x' * 32)
            finally:
                bound.server_close()

    def test_http_exposes_stale_and_disconnected_without_motion_claims(self):
        now = [0.0]
        state = Telemetry(clock=lambda: now[0])
        self.server.service.telemetry = state
        state.connect()
        state.ingest(b'$MAll:2,-2,-191,22#')
        data = self.request('/api/v1/status')[2]['data']['telemetry']
        self.assertEqual(data['status'], 'observed')
        self.assertEqual(data['functional_health'], 'unverified')
        now[0] = 2.0
        self.assertEqual(self.request('/api/v1/status')[2]['data']['telemetry']['status'], 'observed')
        now[0] = 2.001
        state.ingest(b'$MAll:bad#')
        self.assertEqual(self.request('/api/v1/status')[2]['data']['telemetry']['status'], 'stale')
        state.disconnect('serial_open_or_read_error')
        self.assertEqual(self.request('/api/v1/status')[2]['data']['telemetry']['status'], 'disconnected')

    def test_real_http_get_contract(self):
        for endpoint in ('health', 'system', 'devices', 'status'):
            with self.subTest(endpoint=endpoint):
                code, headers, body = self.request('/api/v1/' + endpoint)
                self.assertEqual(code, 200)
                self.assertEqual(body['schema_version'], '1.0')
                self.assertIn('generated_at', body)
                self.assertIn('data', body)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertNotIn('Access-Control-Allow-Origin', headers)
        data = self.request('/api/v1/status')[2]['data']
        self.assertEqual(data['telemetry']['status'], 'disabled')
        self.assertEqual(data['devices']['motor']['encoder_validity'], 'unverified')
        self.assertEqual(self.request('/api/v1/nope')[0], 404)
        self.assertEqual(self.request('/../../etc/passwd')[0], 404)
