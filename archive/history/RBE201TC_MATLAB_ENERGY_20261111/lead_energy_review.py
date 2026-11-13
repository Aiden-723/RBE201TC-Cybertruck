"""Independent SOFTWARE_FIXTURE review; no hardware or real instrument data."""
import csv
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2] / 'work/device-console-v2'
SOURCE = PROJECT / 'tools/analyze_energy.py'
spec = importlib.util.spec_from_file_location('energy_under_review', SOURCE)
energy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = energy
spec.loader.exec_module(energy)


class IndependentEnergyReview(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_LEAD_ENERGY_')
        self.root = Path(temporary.name).resolve()
        assert self.root.parent == Path(tempfile.gettempdir()).resolve()
        assert self.root.name.startswith('SOFTWARE_FIXTURE_LEAD_ENERGY_')
        self.addCleanup(temporary.cleanup)
        self.input = self.root / 'SOFTWARE_FIXTURE_samples.csv'
        self.output = self.root / 'SOFTWARE_FIXTURE_result.json'

    def samples(self, times=(0, 10, 20), powers=(12, 12, 12), origin=0):
        return [dict(EnergyRunID='SOFTWARE_FIXTURE_ONLY', InstrumentID='SOFTWARE_FIXTURE_INSTRUMENT',
                     ChannelAndMeasurementBoundary='SOFTWARE_FIXTURE_BOUNDARY',
                     RawInstrumentFileLink='SOFTWARE_FIXTURE, NOT A REAL INSTRUMENT FILE',
                     SampleNumber=i + 1, Elapsed_s=origin + t,
                     TimestampWithTZ=(datetime(2026, 11, 11, tzinfo=timezone.utc) + timedelta(seconds=t)).isoformat(),
                     DataClassMeasuredOrCalculated='calculated', Power_W=p, Voltage_V=12, Current_A=1,
                     MissingOrFailureReason='') for i, (t, p) in enumerate(zip(times, powers))]

    def write(self, rows, duplicate_power=False):
        with self.input.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(list(rows[0]) + (['Power_W'] if duplicate_power else []))
            for row in rows:
                writer.writerow(list(row.values()) + ([0] if duplicate_power else []))

    def cli(self):
        return subprocess.run([sys.executable, str(SOURCE), str(self.input), '--max-gap', '10',
                               '--output', str(self.output)], capture_output=True, text=True, timeout=10)

    def test_irregular_intervals_nonzero_elapsed_origin(self):
        self.write(self.samples((0, .1, 4.7, 10.3), (12.5, 12.5, 12.5, 12.5), origin=90))
        before = self.input.read_bytes()
        result = energy.analyze(self.input, 6)
        self.assertAlmostEqual(result['calculated_energy_Wh'], 12.5 * 10.3 / 3600)
        self.assertAlmostEqual(result['mean_power_W'], 12.5)
        self.assertEqual(before, self.input.read_bytes())

    def test_piecewise_linear_power_and_equivalent_timezone(self):
        rows = self.samples((0, 4, 10), (0, 3, 6))
        rows[1]['TimestampWithTZ'] = '2026-11-11T08:00:04+08:00'
        self.write(rows)
        self.assertAlmostEqual(energy.analyze(self.input, 6)['calculated_energy_Wh'], 33 / 3600)

    def test_power_column_remains_authoritative(self):
        rows = self.samples()
        for row in rows:
            row.update(Voltage_V='NOT_USED', Current_A='NaN')
        self.write(rows)
        self.assertAlmostEqual(energy.analyze(self.input, 10)['calculated_energy_Wh'], 240 / 3600)

    def test_voltage_current_path_and_true_zero(self):
        rows = self.samples((0, 10, 20), ('', '', ''))
        for row, amps in zip(rows, (0, .5, 1)):
            row['Current_A'] = amps
        self.write(rows)
        self.assertAlmostEqual(energy.analyze(self.input, 10)['calculated_energy_Wh'], 120 / 3600)

    def test_inconsistent_time_axes_rejected(self):
        rows = self.samples()
        rows[1]['TimestampWithTZ'] = '2026-11-11T01:00:00+00:00'
        rows[2]['TimestampWithTZ'] = '2026-11-11T02:00:00+00:00'
        self.write(rows)
        with self.assertRaises(ValueError):
            energy.analyze(self.input, 10)

    def test_duplicate_power_header_rejected(self):
        self.write(self.samples(), duplicate_power=True)
        with self.assertRaises(ValueError):
            energy.analyze(self.input, 10)

    def test_calculated_provenance_is_exposed(self):
        self.write(self.samples())
        result = energy.analyze(self.input, 10)
        def leaves(value):
            if isinstance(value, dict):
                return [item for child in value.values() for item in leaves(child)]
            if isinstance(value, list):
                return [item for child in value for item in leaves(child)]
            return [value]
        self.assertIn('calculated', leaves(result))

    def test_cli_invalid_result_does_not_leave_output(self):
        self.write(self.samples(powers=('1e309', '1e309', '1e309')))
        completed = self.cli()
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn('Traceback', completed.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_never_overwrites_existing_result(self):
        self.write(self.samples())
        self.output.write_text('SOFTWARE_FIXTURE_DO_NOT_OVERWRITE', encoding='utf-8')
        before = self.output.read_bytes()
        completed = self.cli()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.output.read_bytes(), before)

    def test_cli_valid_json_and_input_unchanged(self):
        self.write(self.samples())
        before = self.input.read_bytes()
        completed = self.cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertTrue(math.isfinite(result['calculated_energy_Wh']))
        self.assertAlmostEqual(result['calculated_energy_Wh'], 240 / 3600)
        self.assertEqual(self.input.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
