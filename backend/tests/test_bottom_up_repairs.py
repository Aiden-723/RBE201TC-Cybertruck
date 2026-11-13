import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from experiment_log import _health
from line_trials import LineTrials
from motor_control import MotorControl
from server import create_server
from test_acquisition import Feed
from test_motor_control import FakeSerial


class BottomUpRepairs(unittest.TestCase):
    def test_sensor_only_callback_satisfies_recorder_contract(self):
        # Regression coverage: this path already worked before the audit.
        with tempfile.TemporaryDirectory() as root:
            server = create_server('127.0.0.1', 0, acquisition_dir=root)
            try:
                feed = Feed()
                server.service.get = lambda endpoint: feed(1)
                _health(server.acquisition.fetch_status(1),
                        datetime.now(timezone.utc), 'sensor_only')
            finally:
                server.server_close()

    def test_pwm_start_arm_and_stop_never_enable_speed_loop(self):
        wire = FakeSerial()
        motor = MotorControl(wire, mode='pwm')
        try:
            session = motor.command({'action': 'arm'})['session']
            motor.command(dict(action='drive', session=session, seq=0,
                               direction='forward_left', speed=200))
            motor.command({'action': 'stop'})
            self.assertTrue(all(p.startswith(b'$pwm:') for p in wire.packets),
                            wire.packets)
            self.assertEqual(wire.packets[-1], b'$pwm:0,0,0,0#')
        finally:
            motor.close()

    def test_corrupt_trial_metadata_does_not_disable_console(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            bad = directory / 'line_bad.json'
            raw = '{incomplete'
            bad.write_text(raw)
            (directory / 'line_good.json').write_text(json.dumps(dict(
                id='line_good', status='finished',
                started_at='2026-11-13T00:00:00+00:00')))
            trials = LineTrials(root)
            try:
                snapshot = trials.snapshot()
                self.assertEqual(snapshot['records'][0]['id'], 'line_good')
                self.assertEqual(bad.read_text(), raw)
                self.assertIn('line_bad.json', snapshot['load_errors'])
            finally:
                trials.close()
