import http.client
import threading
import unittest
from server import create_server


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server('127.0.0.1', 0)
        self.server.token = 'static-test-token'
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def get(self, path, token=False, method='GET'):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            client.request(method, path, headers={'Authorization': 'Bearer static-test-token'} if token else {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def test_public_ui_under_token(self):
        code, headers, body = self.get('/')
        self.assertEqual(code, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn('设备监控'.encode(), body)
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for path, mime in [('/index.html', 'text/html'), ('/styles.css', 'text/css'), ('/app.js', 'text/javascript')]:
            code, headers, body = self.get(path)
            self.assertEqual(code, 200, path)
            self.assertIn(mime, headers['Content-Type'])
            self.assertTrue(body)

    def test_api_auth_and_no_arbitrary_files(self):
        for path in ['/api/v1/' + x for x in ('health', 'system', 'devices', 'status', 'unknown')]:
            self.assertEqual(self.get(path)[0], 401, path)
        for path in ['/frontend/', '/backend/server.py', '/README.md', '/../backend/server.py', '/%2e%2e/backend/server.py', '/styles.css/extra', '//styles.css', '/app.js%00', '/frontend/index.html']:
            self.assertEqual(self.get(path, True)[0], 404, path)
        self.assertEqual(self.get('/api/v1/health', True)[0], 200)
        self.assertEqual(self.get('/', method='POST')[0], 405)
