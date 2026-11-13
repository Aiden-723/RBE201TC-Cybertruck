import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from hardware_worker import atomic_bytes, parse_dht
from readings import SensorStore
from server import create_server


class SensorTests(unittest.TestCase):
    def test_dht_values_and_failed_protocol_never_become_zero(self):
        self.assertEqual(parse_dht('DHT\t7\tOK\t44.0\t27.1\n', 7),
                         {'temperature_c': 27.1, 'humidity_pct': 44.0})
        for line in ['DHT\t7\tERROR\tchecksum', 'DHT\t6\tOK\t50\t25',
                     'DHT\t7\tOK\tnan\t25', 'DHT\t7\tOK\t101\t25',
                     'DHT\t7\tOK\t50']:
            with self.assertRaises(ValueError):
                parse_dht(line, 7)

    def test_freshness_uses_monotonic_and_does_not_reuse_stale_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = [100.0]
            store = SensorStore(root, clock=lambda: now[0])
            data = {'status': 'live', 'monotonic': 100, 'value': [0, 1, 0, 1],
                    'sampled_at': '2000-01-01T00:00:00Z'}
            (root / 'line_sensors.json').write_text(json.dumps(data))
            self.assertEqual(store.one('line_sensors')['value'], [0, 1, 0, 1])
            now[0] = 103
            result = store.one('line_sensors')
            self.assertEqual(result['status'], 'stale')
            self.assertIsNone(result['value'])

    def test_missing_invalid_and_symlink_state_never_report_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SensorStore(root)
            self.assertEqual(store.one('dht11')['status'], 'starting')
            (root / 'dht11.json').write_text('{invalid')
            self.assertIsNone(store.one('dht11')['value'])
            (root / 'camera.json').symlink_to(root / 'dht11.json')
            self.assertEqual(store.one('camera')['status'], 'starting')
            self.assertEqual(SensorStore().one('camera')['status'], 'disabled')

    def test_frame_requires_fresh_camera_and_valid_bounded_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = [10.0]
            store = SensorStore(root, clock=lambda: now[0])
            (root / 'camera.json').write_text(json.dumps({'status': 'live', 'monotonic': 10, 'value': {}}))
            (root / 'camera.jpg').write_bytes(b'\xff\xd8test\xff\xd9')
            self.assertIsNotNone(store.frame())
            now[0] = 15
            self.assertIsNone(store.frame())
            now[0] = 10
            (root / 'camera.jpg').write_bytes(b'not an image')
            self.assertIsNone(store.frame())

    def test_frame_ids_are_stable_for_cached_frames_and_change_on_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            store=SensorStore(root, clock=lambda:10)
            (root/'camera.json').write_text(json.dumps({'status':'live','monotonic':10,'value':{}}))
            jpeg=b'\xff\xd8test\xff\xd9'
            atomic_bytes(root/'camera.jpg',jpeg)
            first=store.frame_packet()
            self.assertEqual(first[0],jpeg)
            self.assertEqual(first[1],store.frame_packet()[1])
            atomic_bytes(root/'camera.jpg',jpeg)
            self.assertNotEqual(first[1],store.frame_packet()[1])

    def test_camera_endpoint_preserves_auth_and_sensor_failures_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jpeg = b'\xff\xd8test\xff\xd9'
            (root / 'camera.jpg').write_bytes(jpeg)
            (root / 'camera.json').write_text(json.dumps({'status': 'live', 'monotonic': 10, 'value': {}}))
            (root / 'dht11.json').write_text(json.dumps({'status': 'error', 'monotonic': 10, 'value': None, 'error': 'test failure'}))
            store = SensorStore(root, clock=lambda: 10)
            server = create_server('127.0.0.1', 0, sensors=store)
            server.token = 'camera-test-token'
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            def request(path, authenticated):
                connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                try:
                    connection.request('GET', path, headers={'Authorization': 'Bearer camera-test-token'} if authenticated else {})
                    response = connection.getresponse()
                    return response.status, response.getheader('Content-Type'), response.read()
                finally:
                    connection.close()
            try:
                self.assertEqual(request('/api/v1/camera/frame', False)[0], 401)
                status, mime, body = request('/api/v1/camera/frame', True)
                self.assertEqual((status, mime, body), (200, 'image/jpeg', jpeg))
                status, _, body = request('/api/v1/status', True)
                self.assertEqual(status, 200)
                sensors = json.loads(body)['data']['sensors']
                self.assertEqual(sensors['dht11']['status'], 'error')
                self.assertEqual(sensors['camera']['status'], 'live')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)
