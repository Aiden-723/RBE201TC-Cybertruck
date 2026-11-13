"""Software fixtures only. These levels are NOT measurements or a calibration."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
import unittest

from line_calibration import (black_in_physical_order, build_calibration,
                              validate_reference)


def reference(surface, levels):
    start = datetime(2026, 11, 4, tzinfo=timezone.utc)
    return {'reference': surface, 'user_confirmed_ready': True, 'errors': [],
            'host': {'is_rdk': True, 'model': 'D-Robotics RDK X5 V1.0',
                     'hostname': 'SOFTWARE_FIXTURE_NOT_REAL_BOARD'},
            'samples': [{'value': list(levels), 'physical_pins': [15, 18, 16, 11],
                         'monotonic': 100 + i * 0.15, 'successes': i + 1,
                         'age_seconds': 0.05,
                         'sampled_at': (start + timedelta(seconds=i * 0.15)).isoformat()}
                        for i in range(20)]}


class LineCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.white = reference('white_paper', [0, 0, 0, 0])
        self.black = reference('black_tape', [1, 1, 1, 1])

    def test_summary_fields_are_not_trusted_over_samples(self):
        self.white['stable_levels_X1_to_X4'] = [1, 1, 1, 1]
        self.assertEqual(validate_reference(self.white, 'white_paper'), [0, 0, 0, 0])

    def test_no_contrast_is_never_accepted_as_a_black_line(self):
        black = reference('black_tape', [0, 0, 0, 0])
        with self.assertRaises(ValueError):
            build_calibration(self.white, black)

    def test_black_white_alone_never_implies_order_or_live_activation(self):
        result = build_calibration(self.white, self.black)
        self.assertTrue(result['polarity_verified'])
        self.assertFalse(result['configuration_complete'])
        self.assertFalse(result['order_verified'])
        self.assertFalse(result['applied_to_live_service'])
        self.assertFalse(result['autonomous_route_verified'])
        with self.assertRaises(ValueError):
            black_in_physical_order([1, 0, 0, 0], result)

    def test_measured_order_is_used_instead_of_assuming_x1_is_leftmost(self):
        result = build_calibration(self.white, self.black, ['X4', 'X2', 'X1', 'X3'])
        self.assertEqual(black_in_physical_order([1, 0, 0, 0], result), [False, False, True, False])

    def test_per_channel_mixed_polarities_are_supported(self):
        white = reference('white_paper', [1, 0, 1, 0])
        black = reference('black_tape', [0, 1, 0, 1])
        result = build_calibration(white, black, ['X1', 'X2', 'X3', 'X4'])
        self.assertEqual(black_in_physical_order([0, 0, 0, 0], result), [True, False, True, False])

    def test_unstable_stale_or_cached_reference_is_rejected(self):
        for kind in ('unstable', 'stale', 'duplicate_mono', 'duplicate_sequence', 'wrong_pin', 'boolean'):
            damaged = copy.deepcopy(self.white)
            sample = damaged['samples'][10]
            if kind == 'unstable': sample['value'][0] = 1
            elif kind == 'stale': sample['age_seconds'] = 2.1
            elif kind == 'duplicate_mono': sample['monotonic'] = damaged['samples'][9]['monotonic']
            elif kind == 'duplicate_sequence': sample['successes'] = damaged['samples'][9]['successes']
            elif kind == 'wrong_pin': sample['physical_pins'][0] = 99
            else: sample['value'][0] = False
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_reference(damaged, 'white_paper')

    def test_missing_setup_errors_short_trace_and_wrong_surface_are_rejected(self):
        for field, value in [('user_confirmed_ready', False), ('errors', ['fixture offline']),
                             ('samples', self.white['samples'][:3]), ('reference', 'hand')]:
            damaged = copy.deepcopy(self.white)
            damaged[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_reference(damaged, 'white_paper')

    def test_missing_or_duplicate_physical_channel_order_is_rejected(self):
        for order in (['X1', 'X2', 'X3', 'X3'], ['X1', 'X2'], ['X1', 'X2', 'X3', 'X5']):
            with self.assertRaises(ValueError):
                build_calibration(self.white, self.black, order)

    def test_references_from_different_hosts_are_not_combined(self):
        self.black['host']['hostname'] = 'OTHER_FIXTURE'
        with self.assertRaises(ValueError):
            build_calibration(self.white, self.black)

    def test_timestamp_must_include_timezone(self):
        self.white['samples'][0]['sampled_at'] = '2026-11-04T12:00:00'
        with self.assertRaises(ValueError):
            validate_reference(self.white, 'white_paper')


class LineCalibrationCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.script = Path(__file__).resolve().parents[2] / 'tools' / 'line_calibration.py'
        self.white = self.directory / 'SOFTWARE_FIXTURE_white.json'
        self.black = self.directory / 'SOFTWARE_FIXTURE_black.json'
        self.white.write_text(json.dumps(reference('white_paper', [0, 0, 0, 0])))
        self.black.write_text(json.dumps(reference('black_tape', [1, 1, 1, 1])))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(self.script), *args],
                              text=True, capture_output=True, timeout=5)

    def test_offline_check_never_claims_polarity_from_one_surface(self):
        result = self.run_cli('check', '--surface', 'white_paper', '--input', str(self.white))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['stable_levels_X1_to_X4'], [0, 0, 0, 0])
        self.assertFalse(data['polarity_verified'])

    def test_build_stages_config_without_inventing_order_or_applying_it(self):
        out = self.directory / 'SOFTWARE_FIXTURE_staged_config.json'
        result = self.run_cli('build', '--white', str(self.white), '--black', str(self.black), '--output', str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text())
        self.assertFalse(data['configuration_complete'])
        self.assertFalse(data['applied_to_live_service'])
        self.assertIsNone(data['physical_left_to_right_order'])
        self.assertIn('sha256', data['reference_files']['white'])

    def test_existing_output_and_missing_black_are_never_overwritten_or_filled(self):
        out = self.directory / 'SOFTWARE_FIXTURE_existing.json'
        out.write_text('KEEP_SOFTWARE_FIXTURE')
        result = self.run_cli('build', '--white', str(self.white), '--black', str(self.black), '--output', str(out))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'KEEP_SOFTWARE_FIXTURE')
        missing = self.directory / 'NO_BLACK_REFERENCE.json'
        new_out = self.directory / 'must_not_exist.json'
        result = self.run_cli('build', '--white', str(self.white), '--black', str(missing), '--output', str(new_out))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(new_out.exists())
