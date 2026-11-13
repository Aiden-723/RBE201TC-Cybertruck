"""Software fixtures ONLY: no GPIO/UART and no real room measurements."""
from datetime import datetime, timedelta, timezone
import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment_log import (COURSE_FIELDS, FIELDS, LogPersistenceError, RunLog,
                            Station, capture_station)


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def now(self):
        return datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc) + timedelta(seconds=self.value)

    def wait(self, seconds):
        self.value += seconds


class ExperimentLogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = Clock()
        self.log = RunLog(self.temporary.name, purpose='bench_diagnostic', now=self.clock.now,
                          run_id='SOFTWARE_FIXTURE_NOT_ROOM_DATA')
        self.station = Station('FIXTURE_UNLOCATED')

    def read_rows(self):
        with self.log.path.open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    def sample(self, *, mono=99, uptime=100, status='live', successes=1, failures=0):
        return {
            'host': {'is_rdk': True, 'clock_valid': True,
                     'device_time': self.clock.now().isoformat()},
            'control': {'enabled': True, 'state': 'stopped', 'fault': None, 'outputs': [0, 0, 0, 0]},
            'system': {'uptime_seconds': {'value': uptime}},
            'sensors': {'dht11': {'monotonic': mono, 'status': status,
                        'sampled_at': self.clock.now().isoformat(), 'age_seconds': 0.1,
                        'value': {'temperature_c': 24.5, 'humidity_pct': 55.0},
                        'successes': successes, 'failures': failures,
                        'error': 'fixture checksum failure' if status == 'error' else None,
                        'last_good': {'value': {'temperature_c': 99, 'humidity_pct': 99}}}},
        }

    def capture(self, fetch, timeout=1):
        return capture_station(self.log, self.station, fetch, timeout=timeout,
                               clock=self.clock.monotonic, wait=self.clock.wait)

    def sequence(self, *specs):
        iterator = iter(specs)
        last = specs[-1]
        def fetch(timeout):
            nonlocal last
            last = next(iterator, last)
            return self.sample(**last)
        return fetch

    def test_pending_is_durable_and_terminal_update_stays_one_row(self):
        point = self.log.begin(self.station)
        self.assertEqual(self.read_rows()[0]['Status'], 'pending')
        self.assertEqual(self.read_rows()[0]['Temperature_°C'], '')
        self.log.finish(point, 'success', value={'temperature_c': 24.5, 'humidity_pct': 55})
        self.assertFalse(self.log.finish(point, 'sensor_failure', explanation='late reply'))
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Status'], 'success')
        self.assertEqual(tuple(rows[0])[:8], COURSE_FIELDS)
        self.log.close()
        manifest = json.loads(self.log.manifest_path.read_text())
        self.assertEqual(manifest['status_counts'], {'success': 1})
        self.assertEqual(manifest['row_count'], 1)
        self.assertEqual(manifest['raw_csv_sha256'], hashlib.sha256(self.log.path.read_bytes()).hexdigest())
        self.assertIsNotNone(manifest['closed_at'])

    def test_backwards_recorder_clock_is_preserved_as_failure(self):
        point = self.log.begin(self.station)
        self.clock.value = -10
        self.log.finish(point, 'success', value={'temperature_c': 24, 'humidity_pct': 50})
        row = self.read_rows()[0]
        self.assertEqual(row['Status'], 'request_failure')
        self.assertEqual(row['Temperature_°C'], '')
        self.assertEqual(row['Delay_seconds'], '')
        self.assertIn('clock moved backwards', row['Explanation'])
        self.log.close()

    def test_existing_run_directory_is_never_overwritten(self):
        before = self.log.path.read_bytes()
        with self.assertRaises(FileExistsError):
            RunLog(self.temporary.name, purpose='bench_diagnostic', run_id=self.log.run_id)
        self.assertEqual(before, self.log.path.read_bytes())

    def test_formal_capture_requires_measured_coordinates_group_and_room(self):
        with self.assertRaises(ValueError):
            RunLog(self.temporary.name, purpose='room_stationary')
        formal = RunLog(self.temporary.name, purpose='room_stationary', group_id='SOFTWARE_FIXTURE',
                        room_id='NOT_A_ROOM', run_id='FORMAL_VALIDATION_FIXTURE', now=self.clock.now)
        with self.assertRaises(ValueError):
            formal.begin(self.station)
        with self.assertRaises(ValueError):
            formal.begin(Station('S', float('nan'), 1, 0.2))
        with self.assertRaises(ValueError):
            formal.begin(Station('S', 1, 1, -0.2))
        formal.close()

    def test_invalid_success_values_do_not_modify_pending(self):
        point = self.log.begin(self.station)
        for value in ({'temperature_c': float('nan'), 'humidity_pct': 50},
                      {'temperature_c': 20, 'humidity_pct': 101},
                      {'temperature_c': True, 'humidity_pct': 50}):
            with self.assertRaises(ValueError):
                self.log.finish(point, 'success', value=value)
        self.assertEqual(self.read_rows()[0]['Status'], 'pending')
        self.log.close()
        self.assertEqual(self.read_rows()[0]['Status'], 'cancelled')

    def test_next_sample_only_never_reuses_cache_or_last_good(self):
        row = self.capture(self.sequence({}, {}, {'mono': 101, 'uptime': 102, 'successes': 2}))
        self.assertEqual(row['Status'], 'success')
        self.assertEqual(row['Temperature_°C'], 24.5)
        self.assertEqual(row['Sampler_monotonic'], 101)
        self.assertGreaterEqual(row['Delay_seconds'], 0.4)

    def test_new_error_is_failure_with_blank_values_not_last_good(self):
        row = self.capture(self.sequence({}, {'mono': 101, 'uptime': 102, 'status': 'error', 'failures': 1}))
        self.assertEqual(row['Status'], 'sensor_failure')
        self.assertEqual(row['Temperature_°C'], '')
        self.assertEqual(row['Humidity_%RH'], '')
        self.assertIn('checksum', row['Explanation'])

    def test_no_new_sample_times_out_and_old_reading_is_not_success(self):
        row = self.capture(self.sequence({}))
        self.assertEqual(row['Status'], 'timeout')
        self.assertEqual(row['Temperature_°C'], '')

    def test_network_failure_is_retained_even_before_baseline(self):
        def fail(timeout):
            raise OSError('fixture offline')
        row = self.capture(fail)
        self.assertEqual(row['Status'], 'request_failure')
        self.assertEqual(len(self.read_rows()), 1)

    def test_wrong_clock_and_nonzero_motor_state_prevent_capture(self):
        for fault in ('clock', 'motion', 'disabled'):
            def fetch(timeout, fault=fault):
                snapshot = self.sample()
                if fault == 'clock':
                    snapshot['host']['device_time'] = '2000-01-01T08:00:00+08:00'
                elif fault == 'motion':
                    snapshot['control']['outputs'] = [200] * 4
                else:
                    snapshot['control']['enabled'] = False
                return snapshot
            row = self.capture(fetch)
            self.assertEqual(row['Status'], 'request_failure')
            self.assertEqual(row['Temperature_°C'], '')

    def test_mid_capture_motion_reboot_or_worker_restart_are_not_valid_results(self):
        for fault in ('motion', 'reboot', 'worker'):
            calls = 0
            def fetch(timeout, fault=fault):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return self.sample()
                sample = self.sample(mono=101, uptime=102, successes=2)
                if fault == 'motion':
                    sample['control']['outputs'][0] = 200
                elif fault == 'reboot':
                    sample['system']['uptime_seconds']['value'] = 1
                else:
                    sample['sensors']['dht11']['successes'] = 0
                return sample
            row = self.capture(fetch)
            self.assertEqual(row['Status'], 'request_failure')

    def test_late_http_response_cannot_complete_an_expired_event(self):
        calls = 0
        def fetch(timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.sample()
            self.clock.wait(2)
            return self.sample(mono=101, uptime=102, successes=2)
        row = self.capture(fetch)
        self.assertEqual(row['Status'], 'timeout')

    def test_keyboard_interrupt_preserves_cancelled_event(self):
        calls = 0
        def fetch(timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.sample()
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.capture(fetch)
        self.assertEqual(self.read_rows()[0]['Status'], 'cancelled')

    def test_disk_failure_latches_and_does_not_publish_success(self):
        point = self.log.begin(self.station)
        before = self.log.path.read_bytes()
        with patch('experiment_log.os.replace', side_effect=OSError('fixture disk full')):
            with self.assertRaises(LogPersistenceError):
                self.log.finish(point, 'success', value={'temperature_c': 25, 'humidity_pct': 50})
        self.assertEqual(before, self.log.path.read_bytes())
        self.assertEqual(self.log.records[point]['Status'], 'pending')
        with self.assertRaises(LogPersistenceError):
            self.log.close()

    def test_new_run_resets_detection_point_sequence(self):
        self.assertEqual(self.log.begin(self.station), '1')
        self.assertEqual(self.log.begin(self.station), '2')
        other = RunLog(self.temporary.name, purpose='bench_diagnostic', run_id='SECOND_FIXTURE', now=self.clock.now)
        self.assertEqual(other.begin(self.station), '1')
        other.close()


class RecorderInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'tools' / 'record_stations.py'
        spec = importlib.util.spec_from_file_location('record_stations_fixture', path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_loopback_only_without_url_credentials_or_redirects(self):
        client = self.module.StatusClient('http://127.0.0.1:8765')
        self.assertTrue(client.url.endswith(':8765'))
        for url in ('http://example.com:80', 'https://localhost:8765',
                    'http://sunrise:password@localhost:8765', 'http://localhost:8765?token=x'):
            with self.assertRaises(ValueError):
                self.module.StatusClient(url)
        with self.assertRaises(ValueError):
            self.module.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com')

    def test_header_only_or_duplicate_station_maps_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.csv'
            path.write_text('StationID,X_m,Y_m,SensorHeight_m,Notes\n')
            with self.assertRaises(ValueError):
                self.module.load_stations(path)
            path.write_text('StationID,X_m,Y_m,SensorHeight_m\nFIXTURE,0,0,0.2\nFIXTURE,0,0,0.2\n')
            with self.assertRaises(ValueError):
                self.module.load_stations(path)


class SensorOnlyBenchTests(unittest.TestCase):
    """Explicit sensor-only mode must never manufacture motor or room evidence."""
    sample = ExperimentLogTests.sample
    capture = ExperimentLogTests.capture
    read_rows = ExperimentLogTests.read_rows

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = Clock()
        self.log = RunLog(self.temporary.name, purpose='bench_diagnostic',
                          now=self.clock.now, motion_policy='sensor_only')
        self.station = Station('SOFTWARE_FIXTURE_UNLOCATED')

    def disabled_feed(self, fault=None):
        calls = 0
        def fetch(timeout):
            nonlocal calls
            calls += 1
            state = self.sample(mono=99 if calls == 1 else 101,
                                uptime=100 if calls == 1 else 102,
                                successes=1 if calls == 1 else 2)
            state['control'] = {'enabled': False, 'armed': False, 'state': 'disabled'}
            if fault:
                fault(state, calls)
            return state
        return fetch

    def test_new_reading_has_no_fabricated_motor_or_coordinates(self):
        row = self.capture(self.disabled_feed())
        self.assertEqual(row['Status'], 'success')
        self.assertEqual(row['Temperature_°C'], 24.5)
        self.assertEqual(row['X_m'], '')
        self.assertIn('actual_motion_unknown', row['Motion_evidence'])
        self.assertNotIn('zero_command', row['Motion_evidence'])
        self.assertEqual(self.log.manifest['motion_policy'], 'sensor_only')

    def test_sensor_only_cannot_be_used_for_formal_runs(self):
        with self.assertRaises(ValueError):
            RunLog(self.temporary.name, purpose='room_stationary', group_id='FIXTURE',
                   room_id='FIXTURE', motion_policy='sensor_only')

    def test_absent_control_is_not_proof_of_disabled_interface(self):
        row = self.capture(self.disabled_feed(lambda state, _: state.pop('control')))
        self.assertEqual(row['Status'], 'request_failure')
        self.assertEqual(row['Humidity_%RH'], '')

    def test_new_motor_owner_mid_capture_aborts(self):
        def fault(state, calls):
            if calls > 1:
                state['control'] = {'enabled': True, 'armed': False, 'state': 'stopped',
                                    'outputs': [0, 0, 0, 0]}
        self.assertEqual(self.capture(self.disabled_feed(fault))['Status'], 'request_failure')

    def test_disabled_flag_with_motor_outputs_is_rejected(self):
        row = self.capture(self.disabled_feed(lambda state, _: state['control'].update(outputs=[200]*4)))
        self.assertEqual(row['Status'], 'request_failure')

    def test_clock_checks_still_apply(self):
        row = self.capture(self.disabled_feed(lambda state, _: state['host'].update(clock_valid=False)))
        self.assertEqual(row['Status'], 'request_failure')

    def test_new_failed_sample_stays_blank(self):
        def fault(state, calls):
            if calls > 1:
                state['sensors']['dht11'].update(status='error', error='FIXTURE_CHECKSUM', failures=1)
        row = self.capture(self.disabled_feed(fault))
        self.assertEqual(row['Status'], 'sensor_failure')
        self.assertEqual(row['Temperature_°C'], '')
        self.assertEqual(row['Humidity_%RH'], '')
