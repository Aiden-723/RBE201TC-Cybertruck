"""Analytic SOFTWARE_FIXTURE ONLY; no real instrument, battery or energy claim."""
import contextlib
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_energy
from analyze_energy import analyze


FIELDS = ('EnergyRunID', 'SampleNumber', 'TimestampWithTZ', 'Elapsed_s',
          'Voltage_V', 'Current_A', 'Power_W', 'AccumulatedEnergy_Wh',
          'ChannelAndMeasurementBoundary', 'DataClassMeasuredOrCalculated',
          'InstrumentID', 'RawInstrumentFileLink', 'MissingOrFailureReason', 'Notes')


class EnergyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_ENERGY_')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'SOFTWARE_FIXTURE_samples.csv'
        self.output = Path(self.tmp.name) / 'SOFTWARE_FIXTURE_result.json'
        self.script = Path(analyze_energy.__file__).resolve()

    def rows(self, powers=(12, 12, 12), *, times=None):
        start = datetime(2026, 11, 11, tzinfo=timezone.utc)
        if times is None:
            times = [i * 10 for i in range(len(powers))]
        return [dict(EnergyRunID='SOFTWARE_FIXTURE_RUN', SampleNumber=str(i + 1),
                     TimestampWithTZ=(start + timedelta(seconds=elapsed)).isoformat(),
                     Elapsed_s=str(elapsed), Voltage_V='12', Current_A='1', Power_W=str(power),
                     AccumulatedEnergy_Wh='', ChannelAndMeasurementBoundary='SOFTWARE_FIXTURE_BOUNDARY',
                     DataClassMeasuredOrCalculated='calculated', InstrumentID='SOFTWARE_FIXTURE_INSTRUMENT',
                     RawInstrumentFileLink='SOFTWARE_FIXTURE_RAW_ONLY', MissingOrFailureReason='',
                     Notes='SOFTWARE_FIXTURE_NOT_PHYSICAL_MEASUREMENTS')
                for i, (power, elapsed) in enumerate(zip(powers, times))]

    def write_rows(self, rows, *, fields=FIELDS):
        with self.path.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return rows

    def write(self, powers=(12, 12, 12), **changes):
        rows = self.rows(powers)
        if len(rows) > 1:
            rows[1].update(changes)
        return self.write_rows(rows)

    def malformed(self, rows):
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerows(rows)
        self.path.write_text(stream.getvalue(), encoding='utf-8')

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', '-W', 'error', str(self.script), str(self.path),
                               '--max-gap', '10', '--output', str(self.output), *args],
                              text=True, capture_output=True, timeout=5)

    def assert_rejected(self, *, gap=10, **kwargs):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            analyze(self.path, gap, **kwargs)
        self.assertEqual(self.path.read_bytes(), before)

    def test_constant_power_wh_and_original_untouched(self):
        self.write(); before = self.path.read_bytes()
        result = analyze(self.path, 10)
        self.assertAlmostEqual(result['calculated_energy_Wh'], 12 * 20 / 3600)
        self.assertEqual(result['mean_power_W'], 12)
        self.assertEqual(result['duration_s'], 20)
        self.assertEqual(result['input_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(before, self.path.read_bytes())

    def test_linear_ramp_and_valid_zero(self):
        self.write([0, 10, 20])
        self.assertAlmostEqual(analyze(self.path, 10)['calculated_energy_Wh'], 200 / 3600)

    def test_voltage_current_used_only_when_power_absent(self):
        self.write(['', '', ''])
        result = analyze(self.path, 10)
        self.assertEqual(result['power_sources'], ['voltage_times_current'])
        self.assertEqual(result['mean_power_W'], 12)

    def test_invalid_missing_negative_or_nonfinite_cannot_estimate(self):
        for changes in ({'Power_W': 'NaN'}, {'Power_W': '-1'}, {'Power_W': '', 'Current_A': ''},
                        {'RawInstrumentFileLink': ''}, {'MissingOrFailureReason': 'disconnect'}):
            with self.subTest(changes=changes):
                self.write(**changes)
                self.assert_rejected()

    def test_gaps_duplicate_time_and_mixed_boundary_rejected(self):
        for changes in ({'Elapsed_s': '0'}, {'SampleNumber': '1'}, {'InstrumentID': 'SOFTWARE_FIXTURE_OTHER'},
                        {'TimestampWithTZ': '2026-11-11T00:00:10'}):
            with self.subTest(changes=changes):
                self.write(**changes)
                self.assert_rejected()
        self.write([12, 12]); self.assert_rejected(gap=9)

    def test_one_sample_cannot_produce_energy(self):
        self.write([12]); self.assert_rejected()

    def test_zero_power_is_not_missing_and_does_not_fall_back_to_vi(self):
        self.write([0, 0, 0])
        result = analyze(self.path, 10)
        self.assertEqual(result['calculated_energy_Wh'], 0)
        self.assertEqual(result['mean_power_W'], 0)
        self.assertEqual(result['peak_sampled_power_W'], 0)
        self.assertEqual(result['power_sources'], ['input_power_w'])

    def test_zero_vi_is_valid(self):
        rows = self.rows(['', '', ''])
        for row in rows: row['Current_A'] = '0'
        self.write_rows(rows)
        self.assertEqual(analyze(self.path, 10)['calculated_energy_Wh'], 0)

    def test_power_wins_over_invalid_unused_vi_and_accumulator(self):
        rows = self.rows()
        for row in rows:
            row.update(Voltage_V='NaN', Current_A='not used', AccumulatedEnergy_Wh='NOT_USED')
        self.write_rows(rows)
        self.assertEqual(analyze(self.path, 10)['mean_power_W'], 12)

    def test_present_invalid_power_never_falls_back_to_valid_vi(self):
        for value in ('NaN', 'Infinity', 'bad', '-.01'):
            self.write(Power_W=value); self.assert_rejected()

    def test_irregular_analytic_trapezoids_and_nonzero_elapsed_origin(self):
        self.write_rows(self.rows([2, 6, 10], times=[100, 101, 104]))
        result = analyze(self.path, 3)
        self.assertAlmostEqual(result['calculated_energy_Wh'], 28 / 3600)
        self.assertEqual(result['mean_power_W'], 7)
        self.assertEqual(result['duration_s'], 4)

    def test_fractional_elapsed_exact_time_differences_are_supported(self):
        self.write_rows(self.rows([1, 1, 1], times=[.1, .2, .3]))
        self.assertAlmostEqual(analyze(self.path, .1)['calculated_energy_Wh'], .2 / 3600)

    def test_elapsed_and_timestamp_durations_must_agree(self):
        rows = self.rows([12, 12])
        rows[1]['TimestampWithTZ'] = '2026-11-11T00:00:20+00:00'
        self.write_rows(rows); self.assert_rejected()

    def test_timezone_offsets_compared_as_instants_not_local_clock(self):
        rows = self.rows([12, 12])
        rows[0]['TimestampWithTZ'] = '2026-11-11T08:00:00+08:00'
        rows[1]['TimestampWithTZ'] = '2026-11-11T01:00:10+01:00'
        self.write_rows(rows)
        result = analyze(self.path, 10)
        self.assertEqual(result['duration_s'], 10)
        self.assertEqual(result['coverage']['start_timestamp_utc'], '2026-11-11T00:00:00+00:00')
        self.assertEqual(result['coverage']['end_timestamp_utc'], '2026-11-11T00:00:10+00:00')

    def test_explicit_clock_tolerance_is_not_a_power_gap_allowance(self):
        rows = self.rows([12, 12])
        rows[1]['TimestampWithTZ'] = '2026-11-11T00:00:10.100000+00:00'
        self.write_rows(rows)
        self.assertEqual(analyze(self.path, 10, time_tolerance=.1)['duration_s'], 10)
        self.assert_rejected(time_tolerance=.099)
        self.assert_rejected(gap=9.99, time_tolerance=1)

    def test_timestamp_tolerance_checks_global_and_adjacent_drift(self):
        rows = self.rows()
        rows[1]['TimestampWithTZ'] = '2026-11-11T00:00:10.100000+00:00'
        rows[2]['TimestampWithTZ'] = '2026-11-11T00:00:19.900000+00:00'
        self.write_rows(rows); self.assert_rejected(time_tolerance=.1)
        rows[2]['TimestampWithTZ'] = '2026-11-11T00:00:20.200000+00:00'
        self.write_rows(rows); self.assert_rejected(time_tolerance=.1)

    def test_timestamp_precision_date_only_and_invalid_offset_rejected(self):
        for stamp in ('2026-11-11', '2026-11-11T00:00:10', '2026-11-11T00:00:10.0000001Z',
                      '2026-11-11T00:00:10+25:00', 'not-time'):
            with self.subTest(stamp=stamp):
                self.write(TimestampWithTZ=stamp); self.assert_rejected()

    def test_backwards_or_duplicate_absolute_time_rejected(self):
        for stamp in ('2026-11-11T08:00:00+08:00', '2026-11-10T23:59:59Z'):
            self.write(TimestampWithTZ=stamp); self.assert_rejected()

    def test_sample_numbers_require_exact_increasing_integer_order(self):
        for numbers in (['3', '2', '1'], ['1', '1', '2'], ['1', '2.0', '3'],
                        ['1', '2e0', '3'], ['1', '0', '2'], ['1', 'True', '3']):
            with self.subTest(numbers=numbers):
                rows = self.rows()
                for row, number in zip(rows, numbers): row['SampleNumber'] = number
                self.write_rows(rows); self.assert_rejected()

    def test_contiguous_excerpt_numbering_does_not_claim_full_run(self):
        rows = self.rows()
        for row, number in zip(rows, (20, 21, 22)): row['SampleNumber'] = str(number)
        self.write_rows(rows)
        coverage = analyze(self.path, 10)['coverage']
        self.assertEqual(coverage['first_sample_number'], 20)
        self.assertEqual(coverage['last_sample_number'], 22)
        self.assertFalse(coverage['full_run_verified'])

    def test_numbering_gaps_allow_downsampling_but_never_claim_complete_coverage(self):
        rows = self.rows()
        for row, number in zip(rows, (1, 3, 9)): row['SampleNumber'] = str(number)
        self.write_rows(rows)
        result = analyze(self.path, 10)
        self.assertEqual(result['coverage']['sample_number_gap_intervals'], 2)
        self.assertFalse(result['coverage']['full_run_verified'])
        self.assertEqual(result['sample_count'], 3)
        self.assertEqual(result['mean_power_W'], 12)
        self.assert_rejected(gap=9)

    def test_numbering_above_float_precision_does_not_collapse_distinct_samples(self):
        rows = self.rows()
        for row, number in zip(rows, (9007199254740992, 9007199254740993, 9007199254740994)):
            row['SampleNumber'] = str(number)
        self.write_rows(rows)
        self.assertEqual(analyze(self.path, 10)['sample_count'], 3)

    def test_missing_required_header_rejected_even_when_field_would_be_empty(self):
        rows = self.rows()
        for row in rows: row.pop('MissingOrFailureReason')
        self.write_rows(rows, fields=tuple(field for field in FIELDS if field != 'MissingOrFailureReason'))
        self.assert_rejected()

    def test_duplicate_header_rejected(self):
        rows = self.rows()
        headers = list(FIELDS) + ['Power_W']
        self.malformed([headers] + [[row[field] for field in headers] for row in rows])
        self.assert_rejected()

    def test_unknown_header_rejected_instead_of_ignoring_misspelled_columns(self):
        rows = self.rows()
        headers = list(FIELDS) + ['SOFTWARE_FIXTURE_Unknown']
        self.malformed([headers] + [[row.get(field, 'extra') for field in headers] for row in rows])
        self.assert_rejected()

    def test_extra_and_truncated_rows_rejected_even_in_unused_tail(self):
        for damage in ('extra', 'short'):
            with self.subTest(damage=damage):
                rows = self.rows()
                values = [[row[field] for field in FIELDS] for row in rows]
                if damage == 'extra': values[1].append('SOFTWARE_FIXTURE_EXTRA')
                else: values[1].pop()
                self.malformed([FIELDS] + values)
                self.assert_rejected()

    def test_unterminated_quote_rejected_in_unused_notes_field(self):
        self.write()
        raw = self.path.read_text(encoding='utf-8-sig')
        self.path.write_text(raw.rsplit(',', 1)[0] + ',"SOFTWARE_FIXTURE_UNTERMINATED', encoding='utf-8')
        self.assert_rejected()

    def test_junk_after_closing_quote_rejected(self):
        self.write()
        raw = self.path.read_text(encoding='utf-8-sig')
        self.path.write_text(raw.rsplit(',', 1)[0] + ',"SOFTWARE_FIXTURE"junk\n', encoding='utf-8')
        self.assert_rejected()

    def test_blank_data_row_is_not_silently_skipped(self):
        self.write()
        raw = self.path.read_bytes()
        self.path.write_bytes(raw + b'\r\n')
        self.assert_rejected()

    def test_quoted_commas_newlines_and_unicode_metadata_preserved(self):
        rows = self.rows()
        for row in rows:
            row['ChannelAndMeasurementBoundary'] = 'SOFTWARE_FIXTURE, 边界 "甲"\n第二行'
            row['RawInstrumentFileLink'] = 'SOFTWARE_FIXTURE, RAW\n无外部访问'
        self.write_rows(rows)
        result = analyze(self.path, 10)
        self.assertEqual(result['MeasurementBoundary'], rows[0]['ChannelAndMeasurementBoundary'])
        self.assertEqual(result['raw_instrument_file_links'], [rows[0]['RawInstrumentFileLink']])

    def test_legacy_core_headers_without_optional_notes_accumulator_supported(self):
        rows = self.rows()
        for row in rows:
            row.pop('AccumulatedEnergy_Wh'); row.pop('Notes')
        self.write_rows(rows, fields=tuple(field for field in FIELDS if field not in ('AccumulatedEnergy_Wh', 'Notes')))
        self.assertEqual(analyze(self.path, 10)['sample_count'], 3)

    def test_whitespace_only_missing_power_uses_vi(self):
        self.write([' ', '\t', ''])
        self.assertEqual(analyze(self.path, 10)['power_sources'], ['voltage_times_current'])

    def test_vi_overflow_does_not_return_infinite_energy(self):
        self.write(['', '', ''], Voltage_V='1e308', Current_A='1e308')
        self.assert_rejected()

    def test_vi_underflow_does_not_claim_zero_power(self):
        self.write(['', '', ''], Voltage_V='1e-300', Current_A='1e-300')
        self.assert_rejected()

    def test_integration_overflow_is_rejected(self):
        self.write(['1e308', '1e308', '1e308'])
        self.assert_rejected()

    def test_high_finite_power_avoids_spurious_intermediate_addition_overflow(self):
        self.write_rows(self.rows(['1e308', '1e308'], times=[0, .125]))
        result = analyze(self.path, .125)
        self.assertEqual(result['mean_power_W'], 1e308)
        self.assertAlmostEqual(result['calculated_energy_Wh'] / 1e308, .125 / 3600)

    def test_elapsed_duration_overflow_is_rejected(self):
        self.write([12, 12], Elapsed_s='1e308')
        self.assert_rejected(gap=1e308)

    def test_invalid_gap_tolerance_and_programmatic_boolean_rejected(self):
        self.write()
        for gap in (0, -1, 'NaN', float('inf'), True):
            with self.subTest(gap=gap): self.assert_rejected(gap=gap)
        for tolerance in (-1, True, float('nan'), float('inf')):
            with self.subTest(tolerance=tolerance): self.assert_rejected(time_tolerance=tolerance)

    def test_input_data_class_and_derived_result_class_are_explicit(self):
        for declared in ('measured', 'calculated'):
            rows = self.rows()
            for row in rows: row['DataClassMeasuredOrCalculated'] = declared
            self.write_rows(rows)
            result = analyze(self.path, 10)
            self.assertEqual(result['input_data_class'], declared)
            self.assertEqual(result['input_data_class_counts'], {declared: 3})
            self.assertEqual(result['result_data_class'], 'calculated')
            self.assertFalse(result['input_measurement_verified'])

    def test_mixed_classes_and_multiple_raw_links_are_not_labeled_measured(self):
        rows = self.rows()
        rows[0]['DataClassMeasuredOrCalculated'] = 'measured'
        rows[1]['RawInstrumentFileLink'] = 'SOFTWARE_FIXTURE_RAW_2'
        self.write_rows(rows)
        result = analyze(self.path, 10)
        self.assertEqual(result['input_data_class'], 'mixed')
        self.assertEqual(result['input_data_class_counts'], {'calculated': 2, 'measured': 1})
        self.assertEqual(result['result_data_class'], 'calculated')
        self.assertEqual(result['raw_instrument_file_links'], ['SOFTWARE_FIXTURE_RAW_2', 'SOFTWARE_FIXTURE_RAW_ONLY'])

    def test_mixed_power_sources_are_reported(self):
        self.write(Power_W='')
        result = analyze(self.path, 10)
        self.assertEqual(result['power_sources'], ['input_power_w', 'voltage_times_current'])
        self.assertEqual(result['mean_power_W'], 12)

    def test_invalid_data_class_and_missing_identity_are_rejected(self):
        for changes in ({'DataClassMeasuredOrCalculated': 'estimated'}, {'EnergyRunID': ''},
                        {'InstrumentID': ''}, {'ChannelAndMeasurementBoundary': ''}, {'RawInstrumentFileLink': ''}):
            self.write(**changes); self.assert_rejected()

    def test_cli_success_json_is_complete_and_input_unchanged(self):
        self.write(); before = self.path.read_bytes()
        process = self.run_cli()
        self.assertEqual(process.returncode, 0, process.stderr)
        saved = json.loads(self.output.read_bytes())
        self.assertEqual(json.loads(process.stdout), saved)
        self.assertEqual(saved['result_data_class'], 'calculated')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.output.parent.iterdir()),
                         [self.output.name, self.path.name])

    def test_cli_accepts_explicit_time_tolerance(self):
        self.write([12, 12], TimestampWithTZ='2026-11-11T00:00:10.100000Z')
        process = self.run_cli('--time-tolerance', '.1')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(self.output.read_bytes())['time_tolerance_s'], .1)

    def test_cli_invalid_input_never_creates_output(self):
        self.write(Power_W='NaN'); before = self.path.read_bytes()
        process = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertNotIn('Traceback', process.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_malformed_csv_reports_clean_failure(self):
        self.write()
        self.path.write_bytes(self.path.read_bytes() + b',"SOFTWARE_FIXTURE_UNTERMINATED')
        process = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertNotIn('Traceback', process.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_existing_output_is_not_overwritten(self):
        self.write()
        self.output.write_bytes(b'SOFTWARE_FIXTURE_KEEP_EXISTING')
        process = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.output.read_bytes(), b'SOFTWARE_FIXTURE_KEEP_EXISTING')

    def test_cli_output_equal_input_and_symlink_are_not_overwritten(self):
        self.write(); before = self.path.read_bytes()
        process = self.run_cli('--output', str(self.path))
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.output.symlink_to(self.path)
        process = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.path.read_bytes(), before)

    def test_serialization_failure_never_leaves_partial_output(self):
        self.write()
        argv = [str(self.script), str(self.path), '--max-gap', '10', '--output', str(self.output)]
        with patch.object(sys, 'argv', argv), patch.object(analyze_energy, 'analyze', return_value={'invalid': float('inf')}), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                analyze_energy.main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.output.exists(), 'strict JSON failure must not publish a partial result')

    def test_nonserializable_result_fails_cleanly_without_output(self):
        self.write()
        argv = [str(self.script), str(self.path), '--max-gap', '10', '--output', str(self.output)]
        with patch.object(sys, 'argv', argv), patch.object(analyze_energy, 'analyze', return_value={'invalid': object()}), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                analyze_energy.main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.output.exists())

    def test_nonzero_elapsed_decimal_origin_does_not_create_fake_clock_drift(self):
        self.write_rows(self.rows([2, 4, 8, 10], times=[90, 90.1, 94.7, 100.3]))
        result = analyze(self.path, 6)
        expected_joules = 3 * .1 + 6 * 4.6 + 9 * 5.6
        self.assertAlmostEqual(result['calculated_energy_Wh'], expected_joules / 3600)
        self.assertEqual(result['duration_s'], 10.3)
        self.assertEqual(result['time_tolerance_s'], 0)

    def test_unescaped_quote_in_unquoted_metadata_rejected(self):
        self.write()
        text = self.path.read_text(encoding='utf-8-sig')
        self.path.write_text(text.rsplit(',', 1)[0] + ',SOFTWARE_FIXTURE_BAD"QUOTE\n', encoding='utf-8')
        self.assert_rejected()

    def test_z_suffix_is_normalized_for_older_python_iso_parser(self):
        original_datetime = analyze_energy.datetime
        observed = []

        class LegacyISOParser:
            @staticmethod
            def fromisoformat(text):
                observed.append(text)
                if text.endswith(('Z', 'z')):
                    raise ValueError('SOFTWARE_FIXTURE_PRE_311_PARSER')
                return original_datetime.fromisoformat(text)

        for suffix in ('Z', 'z'):
            self.write([12, 12], TimestampWithTZ='2026-11-11T00:00:10' + suffix)
            with patch.object(analyze_energy, 'datetime', LegacyISOParser):
                self.assertEqual(analyze(self.path, 10)['duration_s'], 10)
        self.assertTrue(observed)
        self.assertTrue(all(not text.endswith(('Z', 'z')) for text in observed))

    def test_invalid_timezone_minutes_are_not_silently_normalized(self):
        self.write([12, 12], TimestampWithTZ='2026-11-11T01:39:10+00:99')
        self.assert_rejected()

    def test_negative_vi_and_nonzero_subnormal_conversion_fail_closed(self):
        for changes in ({'Power_W': '', 'Voltage_V': '-1'}, {'Power_W': '', 'Current_A': '-1'},
                        {'Power_W': '1e-10000'}):
            self.write(**changes); self.assert_rejected()

    def test_cli_invalid_encoding_header_only_missing_input_and_bad_options(self):
        for raw in (b'\xff\xfe\x00', (','.join(FIELDS) + '\n').encode()):
            self.path.write_bytes(raw)
            process = self.run_cli()
            self.assertEqual(process.returncode, 2)
            self.assertNotIn('Traceback', process.stderr)
            self.assertFalse(self.output.exists())
        missing = Path(self.tmp.name) / 'SOFTWARE_FIXTURE_MISSING.csv'
        process = subprocess.run([sys.executable, '-B', str(self.script), str(missing),
                                  '--max-gap', '10', '--output', str(self.output)],
                                 text=True, capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 2)
        self.assertNotIn('Traceback', process.stderr)
        self.write()
        for args in (('--max-gap', 'NaN'), ('--time-tolerance', '-1')):
            process = self.run_cli(*args)
            self.assertEqual(process.returncode, 2)
            self.assertFalse(self.output.exists())

    def test_publication_race_preserves_competing_output_and_cleans_stage(self):
        self.write(); before = self.path.read_bytes()

        def competing_link(source, target):
            Path(target).write_bytes(b'SOFTWARE_FIXTURE_COMPETING_WRITER')
            raise FileExistsError('SOFTWARE_FIXTURE_RACE')

        with patch('os.link', side_effect=competing_link):
            with self.assertRaises(FileExistsError):
                analyze_energy.save_new_json(self.output, analyze(self.path, 10))
        self.assertEqual(self.output.read_bytes(), b'SOFTWARE_FIXTURE_COMPETING_WRITER')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()),
                         [self.output.name, self.path.name])

    def test_unsupported_atomic_publication_fails_closed(self):
        self.write()
        with patch('os.link', side_effect=OSError('SOFTWARE_FIXTURE_NO_HARDLINK')):
            with self.assertRaises(OSError):
                analyze_energy.save_new_json(self.output, analyze(self.path, 10))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_dangling_output_symlink_is_not_followed_or_replaced(self):
        self.write()
        missing = Path(self.tmp.name) / 'SOFTWARE_FIXTURE_DO_NOT_CREATE'
        self.output.symlink_to(missing)
        process = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(missing.exists())

    def test_fsync_failure_does_not_publish_partial_output(self):
        self.write()
        argv = [str(self.script), str(self.path), '--max-gap', '10', '--output', str(self.output)]
        with patch.object(sys, 'argv', argv), patch('os.fsync', side_effect=OSError('SOFTWARE_FIXTURE_FSYNC_ERROR')), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                analyze_energy.main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == '__main__':
    unittest.main()
