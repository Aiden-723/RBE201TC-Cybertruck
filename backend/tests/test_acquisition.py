"""SOFTWARE_FIXTURE-only acquisition/HTTP regressions; never opens hardware."""
import copy
import csv
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from acquisition import AcquisitionManager, AcquisitionError
from experiment_log import LogPersistenceError, COURSE_FIELDS, FIELDS, capture_station
from server import create_server


class Feed:
    def __init__(self):
        self.lock = threading.Lock()
        self.initial = threading.Event()
        self.mono = time.monotonic() - .01
        self.good, self.bad = 1, 0
        self.failure = self.moving = False
        self.stamp = datetime.now(timezone.utc).isoformat()

    def publish(self, *, failure=False, moving=False):
        with self.lock:
            self.mono, self.stamp = time.monotonic(), datetime.now(timezone.utc).isoformat()
            self.failure, self.moving = failure, moving
            self.good += int(not failure)
            self.bad += int(failure)

    def __call__(self, timeout):
        with self.lock:
            result = {'host': {'is_rdk': True, 'clock_valid': True,
                               'device_time': datetime.now(timezone.utc).isoformat()},
                      'system': {'uptime_seconds': {'value': time.monotonic()}},
                      'control': {'enabled': True, 'fault': None, 'state': 'driving' if self.moving else 'stopped',
                                  'outputs': [200, 200, 200, 200] if self.moving else [0, 0, 0, 0]},
                      'sensors': {'dht11': {'status': 'error' if self.failure else 'live',
                                          'monotonic': self.mono, 'sampled_at': self.stamp,
                                          'age_seconds': time.monotonic() - self.mono,
                                          'successes': self.good, 'failures': self.bad,
                                          'error': 'SOFTWARE_FIXTURE_CHECKSUM_FAILURE',
                                          'value': {'temperature_c': 23.5, 'humidity_pct': 51},
                                          'last_good': {'value': {'temperature_c': 99, 'humidity_pct': 99}}}}}
        self.initial.set()
        return result


class SoftwareMotor:
    """Scheduling probe only: no serial transport, worker, GPIO, or real motion."""
    def __init__(self):
        self.commands = []

    def snapshot(self):
        return {'enabled': True, 'armed': False, 'fault': None,
                'state': 'stopped', 'outputs': [0, 0, 0, 0]}

    def command(self, data):
        self.commands.append(dict(data))
        return self.snapshot()


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_')
        self.addCleanup(self.temp.cleanup)
        self.feed = Feed()
        self.manager = AcquisitionManager(self.temp.name, self.feed, timeout=.8)
        self.addCleanup(self.manager.close)

    def join_worker(self):
        worker = self.manager.worker
        self.assertIsNotNone(worker)
        worker.join(2)
        self.assertFalse(worker.is_alive(), 'SOFTWARE_FIXTURE worker did not stop')
        state = self.manager.snapshot()
        self.assertFalse(state['busy'])
        self.assertIsNone(state['operation'])
        return state

    def start(self, **extra):
        accepted = self.manager.command({'action': 'start_run', 'purpose': 'bench_diagnostic', **extra})
        self.assertTrue(accepted['busy'])
        self.assertEqual(accepted['operation'], 'start')
        state = self.join_worker()
        self.assertIsNone(state['error'])
        self.assertIsNotNone(state['active_run_id'])
        return state

    def close_run(self):
        accepted = self.manager.command({'action': 'close_run'})
        self.join_worker()
        self.assertIsNone(self.manager.snapshot()['error'])
        return accepted

    def capture(self):
        self.feed.initial.clear()
        result = self.manager.command({'action': 'capture', 'station_id': 'BENCH_UNLOCATED'})
        self.assertTrue(result['busy'])
        self.assertEqual(result['operation'], 'capture')
        self.assertTrue(self.feed.initial.wait(1))
        return result

    def finish(self):
        return self.join_worker()['selected_run']['events'][-1]

    def restart(self):
        replacement = AcquisitionManager(self.temp.name, self.feed)
        self.addCleanup(replacement.close)
        return replacement

    def successful_closed_fixture(self):
        self.start(); self.capture(); self.feed.publish()
        self.assertEqual(self.finish()['Status'], 'success')
        self.close_run()
        return self.manager.log

    def assert_history_rejected(self, run_id):
        replacement = self.restart()
        state = replacement.snapshot()
        self.assertEqual(state['runs'], [])
        self.assertIsNone(state['selected_run'])
        for kind in ('csv', 'manifest'):
            with self.assertRaises(AcquisitionError):
                replacement.export(run_id, kind)

    def test_pending_is_durable_before_fetch_then_success_updates_same_event(self):
        started = self.start()
        self.assertEqual(started['active_run_id'], started['selected_run']['run_id'])
        observed = []

        def observe_pending(timeout):
            with self.manager.log.path.open(encoding='utf-8-sig', newline='') as stream:
                observed.append(list(csv.DictReader(stream)))
            return self.feed(timeout)

        self.manager.fetch_status = observe_pending
        self.capture()
        self.assertEqual(len(observed[0]), 1)
        self.assertEqual(observed[0][0]['Status'], 'pending')
        self.feed.publish()
        row = self.finish()
        self.assertEqual(row['Status'], 'success')
        self.assertEqual(row['Temperature_°C'], 23.5)
        self.assertEqual(row['Humidity_%RH'], 51)
        self.assertEqual(len(self.manager.log.records), 1)
        self.close_run()
        manifest = json.loads(self.manager.log.manifest_path.read_text())
        self.assertEqual(manifest['acquisition_transport'], 'in_process_snapshot')
        self.assertEqual(manifest['row_count'], 1)
        self.assertEqual(manifest['raw_csv_sha256'], hashlib.sha256(self.manager.log.path.read_bytes()).hexdigest())

    def test_sensor_failure_never_uses_cached_last_good(self):
        self.start(); self.capture(); self.feed.publish(failure=True)
        row = self.finish()
        self.assertEqual(row['Status'], 'sensor_failure')
        self.assertEqual(row['Temperature_°C'], '')
        self.assertEqual(row['Humidity_%RH'], '')

    def test_cancel_is_fast_retained_and_cannot_be_replaced_by_a_late_sample(self):
        self.start(); self.capture()
        began = time.monotonic()
        self.manager.command({'action': 'cancel'})
        self.assertLess(time.monotonic() - began, .5)
        row = self.finish()
        self.assertEqual(row['Status'], 'cancelled')
        self.feed.publish()
        self.assertEqual(self.manager.snapshot()['selected_run']['events'][-1]['Status'], 'cancelled')

    def test_close_during_capture_cancels_and_closes_without_blocking(self):
        self.start(); self.capture()
        began = time.monotonic()
        accepted = self.manager.command({'action': 'close_run'})
        self.assertLess(time.monotonic() - began, .5)
        self.assertTrue(accepted['busy'])
        self.assertTrue(accepted['closing'])
        self.assertEqual(self.finish()['Status'], 'cancelled')
        self.assertIsNone(self.manager.snapshot()['active_run_id'])
        self.assertTrue(self.manager.log.closed)

    def test_no_sample_times_out_and_motion_is_not_recorded_as_success(self):
        self.manager.timeout = .25
        self.start(); self.capture()
        self.assertEqual(self.finish()['Status'], 'timeout')
        self.capture(); self.feed.publish(moving=True)
        self.assertEqual(self.finish()['Status'], 'request_failure')

    def test_formal_needs_real_identifiers_and_complete_finite_coordinates(self):
        payload = {'action': 'start_run', 'purpose': 'room_stationary',
                   'group_id': 'SOFTWARE_FIXTURE_GROUP', 'room_id': 'SOFTWARE_FIXTURE_ROOM',
                   'stations': [{'station_id': 'FIXTURE_P01', 'x_m': 0, 'y_m': 0, 'height_m': .1}]}
        for key in ('group_id', 'room_id', 'stations'):
            broken = copy.deepcopy(payload); broken.pop(key)
            with self.assertRaises(AcquisitionError): self.manager.command(broken)
        for value in (None, float('nan'), True, '0'):
            broken = copy.deepcopy(payload); broken['stations'][0]['x_m'] = value
            with self.assertRaises(ValueError): self.manager.command(broken)
        self.manager.command(payload)
        self.assertEqual(self.join_worker()['stations'], ['FIXTURE_P01'])
        with self.assertRaises(AcquisitionError): self.manager.command(payload)

    def test_duplicate_capture_and_unknown_fields_do_not_add_events(self):
        self.start(); self.capture()
        with self.assertRaises(AcquisitionError):
            self.manager.command({'action': 'capture', 'station_id': 'BENCH_UNLOCATED'})
        self.manager.command({'action': 'cancel'}); self.finish()
        with self.assertRaises(AcquisitionError):
            self.manager.command({'action': 'capture', 'station_id': 'BENCH_UNLOCATED', 'speed': 200})
        self.assertEqual(len(self.manager.log.records), 1)

    def test_disk_failure_is_visible_and_never_published_as_success(self):
        self.start()
        self.feed.initial.clear()
        with patch.object(self.manager.log, '_atomic', side_effect=LogPersistenceError('SOFTWARE_FIXTURE_DISK_FAILURE')):
            self.manager.command({'action': 'capture', 'station_id': 'BENCH_UNLOCATED'})
            self.join_worker()
        self.assertIn('SOFTWARE_FIXTURE_DISK_FAILURE', self.manager.snapshot()['error'])
        self.assertEqual(len(self.manager.log.records), 0)
        self.assertFalse(self.feed.initial.is_set(), 'fetch must not precede durable pending')

    def test_restart_preserves_abandoned_pending_instead_of_faking_a_terminal_result(self):
        self.start(); self.manager.log.begin(self.manager.stations['BENCH_UNLOCATED'])
        run_id = self.manager.log.run_id
        before = self.manager.log.path.read_bytes()
        self.manager.log = None  # Fixture for a process that exited without close().
        replacement = self.restart()
        state = replacement.snapshot()
        self.assertTrue(state['selected_run']['interrupted'])
        self.assertEqual(state['selected_run']['events'][0]['Status'], 'pending')
        self.assertEqual(replacement.export(run_id, 'csv')[1], before)

    def test_export_cannot_traverse_or_follow_external_symlinks(self):
        self.start()
        for bad in ('../outside', '/etc/passwd', '%2e%2e', 'missing'):
            with self.assertRaises(AcquisitionError): self.manager.export(bad, 'csv')
        with self.assertRaises(AcquisitionError):
            self.manager.export(self.manager.log.run_id, 'arbitrary_file')
        outside = Path(self.temp.name) / 'SOFTWARE_FIXTURE_OUTSIDE'
        outside.mkdir()
        (Path(self.temp.name) / 'room_stationary').symlink_to(outside, target_is_directory=True)
        self.close_run()
        accepted = self.manager.command({'action': 'start_run', 'purpose': 'room_stationary',
                                        'group_id': 'SOFTWARE_FIXTURE', 'room_id': 'SOFTWARE_FIXTURE',
                                        'stations': [{'station_id': 'F', 'x_m': 0, 'y_m': 0, 'height_m': .1}]})
        self.assertTrue(accepted['busy'])
        state = self.join_worker()
        self.assertIn('invalid_output_directory', state['error'])
        self.assertIsNone(state['active_run_id'])
        self.assertEqual(list(outside.iterdir()), [])

    def test_snapshot_and_active_export_do_not_read_disk(self):
        self.start(); self.capture(); self.feed.publish(); self.finish()
        log = self.manager.log
        durable = log.path.read_bytes()
        manifest_bytes = log.manifest_path.read_bytes()
        with patch.object(Path, 'open', side_effect=AssertionError('SOFTWARE_FIXTURE_FORBIDDEN_DISK_READ')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('SOFTWARE_FIXTURE_FORBIDDEN_DISK_READ')), \
                patch.object(Path, 'read_text', side_effect=AssertionError('SOFTWARE_FIXTURE_FORBIDDEN_DISK_READ')):
            state = self.manager.snapshot(log.run_id)
            self.assertEqual(state['selected_run']['events'][0]['Temperature_°C'], 23.5)
            self.assertEqual(self.manager.export(log.run_id, 'csv')[1], durable)
            self.assertEqual(self.manager.export(log.run_id, 'manifest')[1], manifest_bytes)

    def test_cancel_accepted_at_barrier_before_complete_wins_over_success(self):
        self.start()
        reached, release = threading.Event(), threading.Event()

        def before_complete(*args, complete, **kwargs):
            def paused_complete(*values, **fields):
                reached.set()
                if not release.wait(2):
                    raise AssertionError('SOFTWARE_FIXTURE complete barrier timed out')
                return complete(*values, **fields)
            return capture_station(*args, complete=paused_complete, **kwargs)

        with patch('acquisition.capture_station', side_effect=before_complete):
            try:
                self.capture(); self.feed.publish()
                self.assertTrue(reached.wait(1))
                self.assertFalse(self.manager.snapshot()['committing'])
                accepted = self.manager.command({'action': 'cancel'})
                self.assertTrue(accepted['busy'])
                self.assertTrue(self.manager.cancel_event.is_set())
            finally:
                release.set()
                self.join_worker()
        row = self.manager.snapshot()['selected_run']['events'][0]
        self.assertEqual(row['Status'], 'cancelled')
        self.assertEqual(row['Temperature_°C'], '')
        self.assertEqual(row['Humidity_%RH'], '')
        self.assertIn('cancel', row['Explanation'].lower())

    def test_cancel_after_terminal_claim_is_conflict_but_close_still_closes(self):
        self.start()
        reached, release = threading.Event(), threading.Event()
        original_finish = self.manager.log.finish

        def claimed_finish(*args, **kwargs):
            reached.set()
            if not release.wait(2):
                raise AssertionError('SOFTWARE_FIXTURE commit barrier timed out')
            return original_finish(*args, **kwargs)

        with patch.object(self.manager.log, 'finish', side_effect=claimed_finish):
            try:
                self.capture(); self.feed.publish()
                self.assertTrue(reached.wait(1))
                pending = self.manager.snapshot()
                self.assertTrue(pending['committing'])
                self.assertEqual(pending['selected_run']['events'][0]['Status'], 'pending')
                self.assertEqual(self.manager.export(self.manager.log.run_id, 'csv')[1],
                                 self.manager.log.path.read_bytes())
                with self.assertRaisesRegex(AcquisitionError, '^result_committing$'):
                    self.manager.command({'action': 'cancel'})
                self.assertFalse(self.manager.cancel_event.is_set())
                accepted = self.manager.command({'action': 'close_run'})
                self.assertTrue(accepted['closing'])
                self.assertTrue(accepted['busy'])
            finally:
                release.set()
                self.join_worker()
        self.assertEqual(self.manager.snapshot()['selected_run']['events'][0]['Status'], 'success')
        self.assertTrue(self.manager.log.closed)
        self.assertIsNone(self.manager.snapshot()['active_run_id'])
        self.assertIsNone(self.manager.snapshot()['error'])

    def test_close_received_during_remember_is_not_lost(self):
        self.start()
        reached, release = threading.Event(), threading.Event()
        original_remember = self.manager._remember
        calls = []

        def paused_remember(log):
            calls.append(log.closed)
            if len(calls) == 1:
                reached.set()
                if not release.wait(2):
                    raise AssertionError('SOFTWARE_FIXTURE remember barrier timed out')
            return original_remember(log)

        with patch.object(self.manager, '_remember', side_effect=paused_remember):
            try:
                self.capture(); self.feed.publish()
                self.assertTrue(reached.wait(1))
                self.assertFalse(self.manager.log.closed)
                accepted = self.manager.command({'action': 'close_run'})
                self.assertTrue(accepted['closing'])
                self.assertTrue(accepted['busy'])
            finally:
                release.set()
                state = self.join_worker()
        self.assertEqual(calls, [False, True])
        self.assertTrue(self.manager.log.closed)
        self.assertIsNone(state['active_run_id'])
        self.assertFalse(state['closing'])
        self.assertIsNone(state['error'])
        manifest = json.loads(self.manager.export(self.manager.log.run_id, 'manifest')[1])
        self.assertIsInstance(manifest['closed_at'], str)

    def test_manifest_array_is_isolated_without_crashing_history_discovery(self):
        log = self.successful_closed_fixture()
        log.manifest_path.write_text('[]', encoding='utf-8')
        self.assert_history_rejected(log.run_id)

    def test_manifest_array_closed_at_is_not_accepted_as_a_valid_run(self):
        log = self.successful_closed_fixture()
        manifest = json.loads(log.manifest_path.read_bytes())
        manifest['closed_at'] = []
        log.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assert_history_rejected(log.run_id)

    def test_manifest_nan_closed_at_is_not_accepted_as_a_valid_run(self):
        log = self.successful_closed_fixture()
        manifest = json.loads(log.manifest_path.read_bytes())
        manifest['closed_at'] = float('nan')
        log.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assert_history_rejected(log.run_id)

    def assert_truncated_csv_rejected(self, quoted_tail=False):
        log = self.successful_closed_fixture()
        row = next(iter(log.records.values()))
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        writer.writerow([row[field] for field in (FIELDS[:-1] if quoted_tail else FIELDS[:9])])
        raw = stream.getvalue().encode('utf-8-sig')
        if quoted_tail:
            raw = raw[:-2] + b',"SOFTWARE_FIXTURE_UNTERMINATED'
        log.path.write_bytes(raw)
        manifest = json.loads(log.manifest_path.read_bytes())
        manifest['raw_csv_sha256'] = hashlib.sha256(raw).hexdigest()
        log.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assert_history_rejected(log.run_id)

    def test_truncated_success_row_is_not_presented_as_success(self):
        self.assert_truncated_csv_rejected()

    def test_unterminated_quoted_success_row_is_not_presented_as_success(self):
        self.assert_truncated_csv_rejected(quoted_tail=True)

    def test_exported_csv_bytes_match_durable_csv_and_manifest_sha(self):
        self.start(notes='SOFTWARE_FIXTURE, 温湿度 "quoted"\nsecond line')
        self.capture(); self.feed.publish(); self.finish()
        log = self.manager.log
        active_csv = self.manager.export(log.run_id, 'csv')[1]
        self.assertEqual(active_csv, log.path.read_bytes())
        self.assertTrue(active_csv.startswith(b'\xef\xbb\xbf'))
        self.close_run()
        closed_csv = self.manager.export(log.run_id, 'csv')[1]
        exported_manifest = self.manager.export(log.run_id, 'manifest')[1]
        self.assertEqual(active_csv, closed_csv)
        self.assertEqual(closed_csv, log.path.read_bytes())
        self.assertEqual(exported_manifest, log.manifest_path.read_bytes())
        self.assertEqual(json.loads(exported_manifest)['raw_csv_sha256'], hashlib.sha256(closed_csv).hexdigest())
        replacement = self.restart()
        self.assertEqual(replacement.export(log.run_id, 'csv')[1], closed_csv)
        self.assertEqual(replacement.export(log.run_id, 'manifest')[1], exported_manifest)


class AcquisitionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_')
        self.addCleanup(self.temp.cleanup)
        self.feed = Feed()
        self.motor = SoftwareMotor()
        self.server = create_server(port=0, collector=object(), telemetry=object(), sensors=object(),
                                    motor=self.motor, acquisition_dir=self.temp.name)
        self.server.token = None  # Do not inherit host environment authentication.

        def fixture_service(endpoint):
            if endpoint == 'status':
                return self.feed(1)
            if endpoint == 'health':
                return {'status': 'ok', 'read_only': True,
                        'capabilities': {'actuation': 'SOFTWARE_FIXTURE'}}
            raise AssertionError('SOFTWARE_FIXTURE unexpected service read: ' + endpoint)

        self.server.service.get = fixture_service
        self.server.acquisition.timeout = .8
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.cleanup_server)
        self.host = '%s:%s' % self.server.server_address
        self.key = self.request('GET', '/api/v1/experiments')[2]['data']['experiment_key']

    def cleanup_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def join_worker(self):
        worker = self.server.acquisition.worker
        self.assertIsNotNone(worker)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        state = self.server.acquisition.snapshot()
        self.assertFalse(state['busy'])
        self.assertIsNone(state['error'])
        return state

    def start(self):
        result = self.request('POST', '/api/v1/experiments', {'action': 'start_run', 'purpose': 'bench_diagnostic'})
        self.assertEqual(result[0], 200)
        self.assertTrue(result[2]['data']['busy'])
        self.assertEqual(result[2]['data']['operation'], 'start')
        state = self.join_worker()
        self.assertIsNotNone(state['active_run_id'])
        return state['active_run_id']

    def request(self, method, path, body=None, headers=None, *, raw=None, timeout=2):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=timeout)
        if raw is None:
            raw = json.dumps(body).encode() if body is not None else None
        defaults = {'Host': self.host, 'Origin': 'http://' + self.host,
                    'X-Experiment-Key': getattr(self, 'key', ''), 'Content-Type': 'application/json'}
        if headers: defaults.update(headers)
        try:
            connection.request(method, path, body=raw, headers=defaults)
            response = connection.getresponse(); content = response.read()
            data = json.loads(content) if response.getheader('Content-Type', '').startswith('application/json') else content
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def assert_control_and_health_responsive(self):
        for path in ('/api/v1/control', '/api/v1/health', '/api/v1/experiments'):
            began = time.monotonic()
            self.assertEqual(self.request('GET', path)[0], 200)
            self.assertLess(time.monotonic() - began, .5, path)
        began = time.monotonic()
        result = self.request('POST', '/api/v1/control', {'action': 'stop'},
                              {'X-Control-Key': self.server.control_key})
        self.assertEqual(result[0], 200)
        self.assertLess(time.monotonic() - began, .5)
        self.assertEqual(self.motor.commands[-1], {'action': 'stop'})

    def test_mutation_authentication_origin_separate_key_and_strict_body(self):
        start = {'action': 'start_run', 'purpose': 'bench_diagnostic'}
        self.assertNotEqual(self.key, self.server.control_key)
        for headers in ({'Origin': 'https://evil.invalid'}, {'Origin': ''},
                        {'X-Experiment-Key': self.server.control_key}, {'X-Experiment-Key': ''},
                        {'Host': 'evil.invalid:80'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('POST', '/api/v1/experiments', start, headers)[0], 403)
        self.assertEqual(self.request('GET', '/api/v1/experiments', headers={'Host': 'evil.invalid:80'})[0], 403)
        self.assertEqual(self.request('POST', '/api/v1/control', {'action': 'stop'},
                                      {'X-Control-Key': self.key})[0], 403)
        self.server.token = 'SOFTWARE_FIXTURE_TOKEN'
        self.assertEqual(self.request('GET', '/api/v1/experiments')[0], 401)
        self.assertEqual(self.request('POST', '/api/v1/experiments', start)[0], 401)
        auth = {'Authorization': 'Bearer SOFTWARE_FIXTURE_TOKEN'}
        self.assertEqual(self.request('POST', '/api/v1/experiments', ['invalid'], auth)[0], 409)
        for raw in (b'{"action":', b'\xff\xfe', b'{} trailing'):
            with self.subTest(raw=raw):
                self.assertEqual(self.request('POST', '/api/v1/experiments', headers=auth, raw=raw)[0], 400)
        self.assertEqual(self.request('POST', '/api/v1/experiments', start,
                                      {**auth, 'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.request('POST', '/api/v1/experiments', start,
                                      {**auth, 'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.request('POST', '/api/v1/experiments', start, auth)[0], 200)
        self.join_worker()
        self.assertEqual(self.request('POST', '/api/v1/experiments',
                                      {'action': 'capture', 'station_id': 'BENCH_UNLOCATED', 'notes': 'x' * 33000}, auth)[0], 400)

    def test_missing_or_duplicate_sensitive_headers_are_rejected(self):
        raw = json.dumps({'action': 'start_run', 'purpose': 'bench_diagnostic'}).encode()
        base = [('Host', self.host), ('Origin', 'http://' + self.host),
                ('X-Experiment-Key', self.key), ('Content-Type', 'application/json'),
                ('Content-Length', str(len(raw)))]
        cases = []
        for name, expected in (('Host', 403), ('Origin', 403), ('X-Experiment-Key', 403),
                               ('Content-Length', 400)):
            value = next(value for key, value in base if key == name)
            cases.append((name + '_duplicate', base + [(name, value)], expected))
            cases.append((name + '_missing', [(key, value) for key, value in base if key != name], expected))
        self.server.token = 'SOFTWARE_FIXTURE_TOKEN'
        auth = ('Authorization', 'Bearer SOFTWARE_FIXTURE_TOKEN')
        cases = [(name, headers + [auth], expected) for name, headers, expected in cases]
        cases.append(('Authorization_duplicate', base + [auth, auth], 401))
        for name, headers, expected in cases:
            with self.subTest(case=name):
                connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
                try:
                    connection.putrequest('POST', '/api/v1/experiments',
                                          skip_host=True, skip_accept_encoding=True)
                    for key, value in headers:
                        connection.putheader(key, value)
                    connection.endheaders(raw)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, expected)
                finally:
                    connection.close()
        self.assertIsNone(self.server.acquisition.snapshot()['active_run_id'])

    def test_capture_returns_immediately_and_does_not_block_health_or_cancel(self):
        self.start()
        began = time.monotonic()
        result = self.request('POST', '/api/v1/experiments', {'action': 'capture', 'station_id': 'BENCH_UNLOCATED'})
        self.assertEqual(result[0], 200)
        self.assertTrue(result[2]['data']['busy'])
        self.assertEqual(result[2]['data']['operation'], 'capture')
        self.assertLess(time.monotonic() - began, .5)
        self.assertTrue(self.feed.initial.wait(1))
        self.assert_control_and_health_responsive()
        self.assertEqual(self.request('POST', '/api/v1/experiments', {'action': 'cancel'})[0], 200)
        self.join_worker()
        self.assertEqual(self.request('GET', '/api/v1/experiments')[2]['data']['selected_run']['events'][0]['Status'], 'cancelled')

    def test_slow_fsync_start_and_close_posts_return_without_blocking_control_or_health(self):
        reached, release = threading.Event(), threading.Event()
        import experiment_log
        original_fsync = experiment_log.os.fsync

        def slow_fsync(fd):
            reached.set()
            if not release.wait(3):
                raise AssertionError('SOFTWARE_FIXTURE fsync barrier timed out')
            return original_fsync(fd)

        with patch('experiment_log.os.fsync', side_effect=slow_fsync):
            try:
                for action in ('start_run', 'close_run'):
                    with self.subTest(action=action):
                        reached.clear(); release.clear()
                        payload = {'action': action}
                        if action == 'start_run':
                            payload['purpose'] = 'bench_diagnostic'
                        began = time.monotonic()
                        response = self.request('POST', '/api/v1/experiments', payload)
                        self.assertEqual(response[0], 200)
                        self.assertLess(time.monotonic() - began, .5)
                        self.assertTrue(response[2]['data']['busy'])
                        self.assertEqual(response[2]['data']['operation'], 'start' if action == 'start_run' else 'close')
                        self.assertTrue(reached.wait(1))
                        self.assert_control_and_health_responsive()
                        release.set()
                        self.join_worker()
            finally:
                release.set()
                if self.server.acquisition.worker:
                    self.server.acquisition.worker.join(2)
        self.assertTrue(self.server.acquisition.log.closed)

    def test_cancel_after_terminal_claim_returns_409_and_close_is_accepted(self):
        self.start()
        log = self.server.acquisition.log
        reached, release = threading.Event(), threading.Event()
        original_finish = log.finish

        def claimed_finish(*args, **kwargs):
            reached.set()
            if not release.wait(2):
                raise AssertionError('SOFTWARE_FIXTURE HTTP commit barrier timed out')
            return original_finish(*args, **kwargs)

        with patch.object(log, 'finish', side_effect=claimed_finish):
            try:
                self.assertEqual(self.request('POST', '/api/v1/experiments',
                                              {'action': 'capture', 'station_id': 'BENCH_UNLOCATED'})[0], 200)
                self.assertTrue(self.feed.initial.wait(1))
                self.feed.publish()
                self.assertTrue(reached.wait(1))
                conflict = self.request('POST', '/api/v1/experiments', {'action': 'cancel'})
                self.assertEqual(conflict[0], 409)
                self.assertEqual(conflict[2]['error']['code'], 'result_committing')
                accepted = self.request('POST', '/api/v1/experiments', {'action': 'close_run'})
                self.assertEqual(accepted[0], 200)
                self.assertTrue(accepted[2]['data']['closing'])
                self.assert_control_and_health_responsive()
            finally:
                release.set()
                self.join_worker()
        self.assertTrue(log.closed)
        self.assertEqual(self.server.acquisition.snapshot()['selected_run']['events'][0]['Status'], 'success')

    def test_csv_and_manifest_export_only_serve_known_files(self):
        run_id = self.start()
        self.assertEqual(self.request('POST', '/api/v1/experiments',
                                      {'action': 'capture', 'station_id': 'BENCH_UNLOCATED'})[0], 200)
        self.assertTrue(self.feed.initial.wait(1))
        self.feed.publish(); self.join_worker()
        self.assertEqual(self.request('POST', '/api/v1/experiments', {'action': 'close_run'})[0], 200)
        self.join_worker()
        code, headers, raw = self.request('GET', '/api/v1/experiments/' + run_id + '/csv')
        self.assertEqual(code, 200)
        self.assertEqual(next(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))[:8], list(COURSE_FIELDS))
        self.assertIn('attachment;', headers['Content-Disposition'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(raw, self.server.acquisition.log.path.read_bytes())
        manifest = self.request('GET', '/api/v1/experiments/' + run_id + '/manifest')[2]
        self.assertEqual(manifest['raw_csv_sha256'], hashlib.sha256(raw).hexdigest())
        for path in ('/api/v1/experiments/../../etc/passwd/csv', '/api/v1/experiments/%2e%2e/csv',
                     '/api/v1/experiments/missing/csv', '/api/v1/experiments/' + run_id + '/source',
                     '/api/v1/experiments/' + run_id + '/csv/extra'):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404)

    def test_http_snapshot_and_active_export_do_not_read_disk(self):
        run_id = self.start()
        durable = self.server.acquisition.log.path.read_bytes()
        with patch.object(Path, 'open', side_effect=AssertionError('SOFTWARE_FIXTURE_FORBIDDEN_DISK_READ')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('SOFTWARE_FIXTURE_FORBIDDEN_DISK_READ')):
            self.assertEqual(self.request('GET', '/api/v1/experiments')[0], 200)
            self.assertEqual(self.request('GET', '/api/v1/experiments/' + run_id)[0], 200)
            self.assertEqual(self.request('GET', '/api/v1/experiments/' + run_id + '/csv')[2], durable)
            self.assertEqual(self.request('GET', '/api/v1/experiments/' + run_id + '/manifest')[0], 200)
            self.assert_control_and_health_responsive()

    def test_http_inflight_handlers_are_bounded_and_excess_connections_rejected(self):
        # Let the setUp key request finish releasing its slot before saturation.
        acquired = 0
        try:
            for _ in range(8):
                self.assertTrue(self.server.request_slots.acquire(timeout=1))
                acquired += 1
        finally:
            for _ in range(acquired):
                self.server.request_slots.release()
        original_finish = self.server.finish_request
        condition, release = threading.Condition(), threading.Event()
        entered, active, peak, timed_out = [], 0, 0, []

        def held_finish(request, client_address):
            nonlocal active, peak
            with condition:
                entered.append(threading.get_ident())
                active += 1
                peak = max(peak, active)
                condition.notify_all()
            try:
                if not release.wait(3):
                    timed_out.append('SOFTWARE_FIXTURE saturation barrier timed out')
                    return
                return original_finish(request, client_address)
            finally:
                with condition:
                    active -= 1
                    condition.notify_all()

        clients = []
        with patch.object(self.server, 'finish_request', side_effect=held_finish):
            try:
                for _ in range(8):
                    connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
                    clients.append(connection)
                    connection.request('GET', '/api/v1/health', headers={'Host': self.host})
                with condition:
                    self.assertTrue(condition.wait_for(lambda: active == 8, timeout=1))
                    self.assertEqual(len(set(entered)), 8)
                # The ninth and repeated excess clients must be closed rather than
                # spawn more request threads or wait for one of the eight slots.
                for index in range(9, 21):
                    with self.subTest(connection=index):
                        excess = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        excess.settimeout(.75)
                        began = time.monotonic()
                        try:
                            excess.connect(self.server.server_address)
                            try:
                                excess.sendall(('GET /api/v1/health HTTP/1.1\r\nHost: ' +
                                                self.host + '\r\n\r\n').encode())
                                self.assertEqual(excess.recv(1), b'')
                            except (ConnectionResetError, BrokenPipeError):
                                pass  # Immediate connection rejection has no response body.
                            self.assertLess(time.monotonic() - began, .5)
                        finally:
                            excess.close()
                with condition:
                    self.assertEqual(active, 8)
                    self.assertEqual(peak, 8)
                    self.assertEqual(len(entered), 8, 'Excess requests must not create handler threads')
                release.set()
                for connection in clients:
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 200)
                with condition:
                    self.assertTrue(condition.wait_for(lambda: active == 0, timeout=1))
                self.assertEqual(timed_out, [])
            finally:
                release.set()
                for connection in clients:
                    connection.close()
                with condition:
                    condition.wait_for(lambda: active == 0, timeout=2)
        self.assert_control_and_health_responsive()

    def test_slow_download_client_does_not_delay_control_dispatch(self):
        run_id = self.start()
        export_path = '/api/v1/experiments/' + run_id + '/csv'
        body = b'SOFTWARE_FIXTURE_DOWNLOAD\n' + b'x' * (8 * 1024 * 1024 - 26)
        handler_class = self.server.RequestHandlerClass
        original_setup = handler_class.setup
        original_get = handler_class.acquisition_get
        entered, finished = threading.Event(), threading.Event()

        def small_send_buffer(handler):
            original_setup(handler)
            handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)

        def observed_export(handler, path):
            if path != export_path:
                return original_get(handler, path)
            entered.set()
            try:
                return original_get(handler, path)
            finally:
                finished.set()

        slow = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        slow.settimeout(1)
        with patch.object(handler_class, 'setup', small_send_buffer), \
                patch.object(handler_class, 'acquisition_get', observed_export), \
                patch.object(self.server.acquisition, 'export', return_value=('SOFTWARE_FIXTURE_DOWNLOAD.csv', body)):
            try:
                slow.connect(self.server.server_address)
                slow.sendall(('GET ' + export_path + ' HTTP/1.1\r\nHost: ' + self.host + '\r\n\r\n').encode())
                headers = b''
                while b'\r\n\r\n' not in headers:
                    packet = slow.recv(1024)
                    self.assertTrue(packet)
                    headers += packet
                    self.assertLess(len(headers), 8192)
                self.assertIn(b' 200 ', headers.split(b'\r\n', 1)[0])
                self.assertTrue(entered.is_set())
                self.assertFalse(finished.is_set(), 'SOFTWARE_FIXTURE download must still occupy its handler')
                began = time.monotonic()
                try:
                    control = self.request('POST', '/api/v1/control', {'action': 'stop'},
                                           {'X-Control-Key': self.server.control_key}, timeout=.75)
                except (TimeoutError, OSError) as exc:
                    self.fail('Slow SOFTWARE_FIXTURE download blocked control dispatch: ' + str(exc))
                self.assertEqual(control[0], 200)
                self.assertLess(time.monotonic() - began, .5)
                self.assertFalse(finished.is_set(), 'Control must finish while download is still blocked')
                self.assertEqual(self.motor.commands[-1], {'action': 'stop'})
            finally:
                slow.close()
                self.assertTrue(finished.wait(2))


class SensorOnlyManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = AcquisitionManager(self.temp.name, Feed(), sensor_only=True)
        self.addCleanup(self.manager.close)

    def test_formal_request_rejected_even_with_coordinates(self):
        with self.assertRaisesRegex(AcquisitionError, 'sensor_only_bench_required'):
            self.manager.command({'action':'start_run', 'purpose':'room_stationary',
                                  'group_id':'FIXTURE', 'room_id':'FIXTURE',
                                  'stations':[{'station_id':'S', 'x_m':0, 'y_m':0, 'height_m':.2}]})
        self.assertIsNone(self.manager.log)

    def test_client_cannot_override_server_policy(self):
        with self.assertRaises(AcquisitionError):
            self.manager.command({'action':'start_run', 'purpose':'bench_diagnostic', 'sensor_only':False})

    def test_persisted_policy_and_disclosed_capabilities(self):
        self.manager.command({'action':'start_run', 'purpose':'bench_diagnostic'})
        self.manager.worker.join(2)
        state = self.manager.snapshot()
        self.assertEqual(state['allowed_purposes'], ['bench_diagnostic'])
        self.assertTrue(state['sensor_only'])
        self.assertEqual(self.manager.log.manifest['motion_policy'], 'sensor_only')

    def test_server_without_motor_supplies_explicit_disabled_state(self):
        server = create_server(port=0, collector=object(), acquisition_dir=self.temp.name)
        self.addCleanup(server.server_close)
        server.service.get = lambda endpoint: {'host':{'fixture':True}}
        state = server.acquisition.fetch_status(1)
        self.assertEqual(state['control'], {'enabled':False, 'armed':False, 'state':'disabled'})
        self.assertTrue(server.acquisition.sensor_only)
