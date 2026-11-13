import http.client
import json
import itertools
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
        for path, mime in [('/index.html', 'text/html'), ('/styles.css', 'text/css'), ('/app.js', 'text/javascript'), ('/vehicle.js', 'text/javascript'), ('/cockpit.css', 'text/css'), ('/cybertruck-photo.jpg', 'image/jpeg')]:
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

    def test_fast_line_read_is_authenticated_physical_order_without_full_metrics_or_motion(self):
        self.assertEqual(self.get('/api/v1/line-sensors')[0],401)
        def forbidden(*args):raise AssertionError('Fast sensor display must not collect metrics or control motors')
        self.server.service.collector.system=forbidden
        count=[0]
        for raw in itertools.product((0,1),repeat=4):
            def one(name):
                self.assertEqual(name,'line_sensors');count[0]+=1
                return dict(status='live',value=list(raw),age_seconds=.01,monotonic=20,successes=count[0])
            self.server.service.sensors.one=one
            code,headers,body=self.get('/api/v1/line-sensors',True)
            self.assertEqual(code,200);self.assertEqual(headers['Cache-Control'],'no-store')
            data=json.loads(body)['data']
            self.assertEqual(data['raw_value'],list(raw));self.assertEqual(data['value'],list(reversed(raw)))
            self.assertEqual(data['channel_order'],['X4','X3','X2','X1'])
        self.assertEqual(count[0],16)
        self.server.service.sensors.one=lambda name:dict(status='error',value=None,age_seconds=0,error='read failed')
        data=json.loads(self.get('/api/v1/line-sensors',True)[2])['data']
        self.assertIsNone(data['value']);self.assertEqual(data['status'],'error')

    def test_fast_display_uses_actual_follower_override_not_a_second_hardcoded_order(self):
        from basic_line_follower import BasicLineFollower
        self.server.driving_mode.follower=BasicLineFollower(None,None,threaded=False,channel_permutation=(0,1,2,3))
        self.server.service.sensors.one=lambda name:dict(status='live',value=[1,1,1,0],age_seconds=0)
        data=json.loads(self.get('/api/v1/line-sensors',True)[2])['data']
        self.assertEqual(data['value'],[1,1,1,0]);self.assertEqual(data['channel_order'],['X1','X2','X3','X4'])
