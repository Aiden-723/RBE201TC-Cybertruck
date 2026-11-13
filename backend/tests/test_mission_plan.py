"""SOFTWARE_FIXTURE ONLY: invented parameters below are NOT physical evidence.

No motor/sensor/network/production files. This exercises only inert reducer data.
"""
import ast
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
import unittest

import mission_plan
from mission_plan import (CalibrationEvidence, Intent, MissionError, MissionPlan,
                          MissionPlanner, Observation, StationPlan, Timing, validate_plan)


SOFTWARE_FIXTURE_SOURCE = 'SOFTWARE_FIXTURE_NOT_REAL_BOARD'


def software_fixture_reference(surface, values):
    start = datetime(2026, 11, 4, tzinfo=timezone.utc)
    return {'reference': surface, 'user_confirmed_ready': True, 'errors': [],
            'host': {'is_rdk': True, 'model': 'D-Robotics RDK X5 V1.0',
                     'hostname': SOFTWARE_FIXTURE_SOURCE},
            'samples': [{'value': list(values), 'physical_pins': [15, 18, 16, 11],
                         'monotonic': 10 + i * .125, 'successes': i + 1,
                         'age_seconds': .125,
                         'sampled_at': (start + timedelta(seconds=i * .125)).isoformat()}
                        for i in range(20)]}


def software_fixture_plan(stations=1):
    # These arbitrary test numbers and channel order MUST NOT be copied into a
    # real mission configuration. There is deliberately no production sample.
    calibration = CalibrationEvidence(
        software_fixture_reference('white_paper', [0, 0, 0, 0]),
        software_fixture_reference('black_tape', [1, 1, 1, 1]),
        ('X4', 'X2', 'X1', 'X3'), 'SOFTWARE_FIXTURE_ORDER_NOT_MEASURED')
    return MissionPlan('SOFTWARE_FIXTURE_MISSION', 'SOFTWARE_FIXTURE_GROUP',
                       'SOFTWARE_FIXTURE_ROOM',
                       tuple(StationPlan('SOFTWARE_FIXTURE_S' + str(i + 1),
                                         'SOFTWARE_FIXTURE_M' + str(i + 1),
                                         i * 2.0, i * 3.0, .25) for i in range(stations)),
                       Timing(.25, .5, 1.0, .25, .5, 1.5, 10.0), calibration,
                       'SOFTWARE_FIXTURE_ROUTE_NOT_VALIDATED',
                       'SOFTWARE_FIXTURE_MARKERS_NOT_VALIDATED',
                       'SOFTWARE_FIXTURE_STATIONARITY_NOT_MEASURED',
                       'SOFTWARE_FIXTURE_TIMING_NOT_MEASURED')


class MissionFixtureTests(unittest.TestCase):
    def setUp(self):
        self.now, self.sequence, self.stopped = 100.0, 0, False
        self.plan = software_fixture_plan()
        self.machine = MissionPlanner(self.plan)

    def observation(self, *, dt=.125, **changes):
        self.now += dt
        self.sequence += 1
        values = dict(observed_at=self.now, sequence=self.sequence,
                      source_id=SOFTWARE_FIXTURE_SOURCE, line_values=(0, 1, 0, 0),
                      physical_pins=(15, 18, 16, 11), line_trackable=True,
                      zero_command=self.stopped, stationary_confirmed=self.stopped,
                      control_healthy=True, emergency_stop=False, marker_id=None)
        values.update(changes)
        return Observation(**values)

    def start(self, stations=1):
        self.plan = software_fixture_plan(stations)
        self.machine = MissionPlanner(self.plan)
        intents = self.machine.start(self.observation(), now=self.now)
        self.assertEqual(self.kinds(intents), ['follow_line'])
        return intents

    @staticmethod
    def kinds(intents):
        return [intent.kind for intent in intents]

    def step(self, **changes):
        observed = self.observation(**changes)
        return self.machine.advance(observed, now=self.now)

    def request_capture(self, *, start=True, stations=1):
        if start:
            self.start(stations)
        marker = self.plan.stations[self.machine.index].marker_id
        self.assertEqual(self.kinds(self.step(marker_id=marker)), ['hold_stop'])
        self.assertEqual(self.machine.state, 'stopping')
        self.stopped = True
        self.step(marker_id=marker)
        self.assertEqual(self.machine.state, 'settling')
        self.step(marker_id=marker)
        self.step(marker_id=marker)
        self.assertEqual(self.machine.state, 'adapting')
        for _ in range(3):
            self.assertEqual(self.kinds(self.step(marker_id=marker)), ['hold_stop'])
        intents = self.step(marker_id=marker)
        self.assertEqual(self.kinds(intents), ['hold_stop', 'request_capture'])
        self.assertEqual(self.machine.state, 'awaiting_pending')
        self.assertEqual(intents[1].station_id, self.plan.stations[self.machine.index].station_id)
        return self.machine.snapshot()['request_id']

    def pending(self, *, event_id='SOFTWARE_FIXTURE_EVENT_1', durable=True):
        return self.machine.confirm_pending(self.machine.snapshot()['request_id'], event_id,
                                            self.observation(), now=self.now, durable=durable)

    def result(self, status='success', *, request_id=None, event_id=None,
               station_id=None, durable=True, **changes):
        state = self.machine.snapshot()
        return self.machine.accept_result(request_id or state['request_id'],
                                          event_id or state['event_id'],
                                          station_id or state['station_id'], status,
                                          self.observation(**changes), now=self.now, durable=durable)

    def assert_latched(self, state, intents):
        self.assertEqual(self.machine.state, state)
        self.assertIn('hold_stop', self.kinds(intents))
        for _ in range(3):
            self.assertEqual(self.kinds(self.step()), ['hold_stop'])
            self.assertEqual(self.machine.state, state)
        with self.assertRaisesRegex(MissionError, 'cannot_restart'):
            self.machine.start(self.observation(), now=self.now)

    def test_default_without_explicit_plan_refuses_start(self):
        machine = MissionPlanner()
        self.assertEqual(machine.snapshot()['state'], 'idle')
        with self.assertRaisesRegex(MissionError, 'explicit_plan_required'):
            machine.start(self.observation(), now=self.now)
        self.assertEqual(machine.state, 'idle')

    def test_missing_black_reference_or_physical_order_refuses_start(self):
        for field, value in (('black_reference', None), ('white_reference', {}),
                             ('physical_left_to_right_order', None),
                             ('physical_order_evidence_id', '')):
            with self.subTest(field=field):
                evidence = replace(self.plan.calibration, **{field: value})
                machine = MissionPlanner(replace(self.plan, calibration=evidence))
                with self.assertRaises(MissionError):
                    machine.start(self.observation(), now=self.now)
                self.assertEqual(machine.state, 'idle')

    def test_summary_calibration_flags_cannot_replace_raw_references(self):
        summary = {'configuration_complete': True, 'polarity_verified': True, 'order_verified': True}
        with self.assertRaises(MissionError):
            validate_plan(replace(self.plan, calibration=summary))

    def test_references_are_revalidated_for_contrast_freshness_and_source(self):
        for damage in ('contrast', 'stale', 'replayed', 'source', 'wrong_pin', 'numeric_overflow'):
            plan = copy.deepcopy(self.plan)
            sample = plan.calibration.black_reference['samples'][5]
            if damage == 'contrast':
                for entry in plan.calibration.black_reference['samples']:
                    entry['value'] = [0, 0, 0, 0]
            elif damage == 'stale': sample['age_seconds'] = 2
            elif damage == 'replayed': sample['successes'] = 1
            elif damage == 'source': plan.calibration.black_reference['host']['hostname'] = 'SOFTWARE_FIXTURE_OTHER'
            elif damage == 'numeric_overflow': sample['monotonic'] = 10 ** 1000
            else: sample['physical_pins'] = [15, 18, 16, 99]
            with self.subTest(damage=damage), self.assertRaises(MissionError):
                validate_plan(plan)

    def test_invalid_or_duplicate_physical_order_refused(self):
        for order in (('X1', 'X1', 'X3', 'X4'), ('X1', 'X2'),
                      ('X1', 'X2', 'X3', []), ['X1', 'X2', 'X3', 'X4']):
            with self.subTest(order=order), self.assertRaises(MissionError):
                validate_plan(replace(self.plan, calibration=replace(
                    self.plan.calibration, physical_left_to_right_order=order)))

    def test_actual_plan_identifiers_and_all_evidence_declarations_required(self):
        for field in ('mission_id', 'group_id', 'room_id', 'route_evidence_id',
                      'marker_evidence_id', 'stationary_evidence_id', 'timing_evidence_id'):
            with self.subTest(field=field), self.assertRaises(MissionError):
                validate_plan(replace(self.plan, **{field: ''}))

    def test_no_station_or_implicit_station_mapping_is_permitted(self):
        for stations in ((), [], ('SOFTWARE_FIXTURE_S1',), self.plan.stations * 101):
            with self.subTest(stations_type=type(stations)), self.assertRaises(MissionError):
                validate_plan(replace(self.plan, stations=stations))

    def test_duplicate_station_and_marker_identifiers_refused(self):
        station = self.plan.stations[0]
        for second in (replace(station, marker_id='SOFTWARE_FIXTURE_OTHER'),
                       replace(station, station_id='SOFTWARE_FIXTURE_OTHER')):
            with self.assertRaisesRegex(MissionError, 'duplicate'):
                validate_plan(replace(self.plan, stations=(station, second)))

    def test_finite_measured_coordinates_required_without_filling_missing_values(self):
        for field in ('x_m', 'y_m', 'height_m'):
            for value in (None, True, '0', float('nan'), float('inf'), 10 ** 1000):
                with self.subTest(field=field, kind=type(value)), self.assertRaises(MissionError):
                    station = replace(self.plan.stations[0], **{field: value})
                    validate_plan(replace(self.plan, stations=(station,)))
        with self.assertRaises(MissionError):
            validate_plan(replace(self.plan, stations=(replace(self.plan.stations[0], height_m=-1),)))

    def test_every_timing_threshold_is_explicit_finite_and_positive(self):
        for field in Timing.__dataclass_fields__:
            for value in (None, False, 0, -1, '1', float('nan'), float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(MissionError):
                    validate_plan(replace(self.plan, timing=replace(self.plan.timing, **{field: value})))

    def test_start_requires_trackable_clear_marker_and_healthy_control(self):
        for changes in ({'line_trackable': False}, {'control_healthy': False},
                        {'marker_id': 'SOFTWARE_FIXTURE_M1'}):
            machine = MissionPlanner(self.plan)
            with self.subTest(changes=changes), self.assertRaises(MissionError):
                machine.start(self.observation(**changes), now=self.now)
            self.assertEqual(machine.state, 'idle')

    def test_start_does_not_execute_or_mark_live_integration(self):
        intents = self.start()
        self.assertIsInstance(intents, tuple)
        self.assertTrue(all(isinstance(intent, Intent) for intent in intents))
        state = self.machine.snapshot()
        self.assertFalse(state['integrated'])
        self.assertFalse(state['executes_hardware'])
        self.assertFalse(state['writes_records'])
        self.assertEqual(state['outcomes'], [])

    def test_expected_marker_stops_before_any_capture_intent(self):
        self.start()
        intents = self.step(marker_id='SOFTWARE_FIXTURE_M1')
        self.assertEqual(self.kinds(intents), ['hold_stop'])
        self.assertEqual(self.machine.state, 'stopping')
        self.assertIsNone(self.machine.snapshot()['request_id'])

    def test_zero_command_alone_is_not_physical_stationarity(self):
        self.start(); self.step(marker_id='SOFTWARE_FIXTURE_M1')
        for _ in range(4):
            intents = self.step(zero_command=True, stationary_confirmed=False)
            self.assertEqual(self.kinds(intents), ['hold_stop'])
            self.assertEqual(self.machine.state, 'stopping')

    def test_missing_physical_stationarity_times_out_without_capture(self):
        self.start(); self.step(marker_id='SOFTWARE_FIXTURE_M1')
        for _ in range(8):
            intents = self.step(zero_command=True, stationary_confirmed=False)
        self.assertEqual(self.machine.reason, 'stop_timeout')
        self.assert_latched('failed', intents)
        self.assertIsNone(self.machine.snapshot()['request_id'])

    def test_settle_then_adapt_both_complete_before_single_request(self):
        request = self.request_capture()
        self.assertEqual(request, 'SOFTWARE_FIXTURE_MISSION:1')
        for _ in range(3):
            self.assertEqual(self.kinds(self.step(marker_id='SOFTWARE_FIXTURE_M1')), ['hold_stop'])
        self.assertEqual(self.machine.state, 'awaiting_pending')
        self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_loss_of_stop_during_settle_or_adapt_is_terminal_not_a_restart(self):
        for phase in ('settling', 'adapting'):
            with self.subTest(phase=phase):
                self.setUp(); self.start(); self.step(marker_id='SOFTWARE_FIXTURE_M1')
                self.stopped = True; self.step()
                if phase == 'adapting':
                    self.step(); self.step()
                self.assertEqual(self.machine.state, phase)
                intents = self.step(stationary_confirmed=False)
                self.assertEqual(self.machine.reason, 'stationary_hold_lost')
                self.assert_latched('failed', intents)

    def test_changed_station_marker_while_stopped_aborts_association(self):
        self.request_capture()
        intents = self.step(marker_id='SOFTWARE_FIXTURE_UNEXPECTED')
        self.assertEqual(self.machine.reason, 'station_marker_changed')
        self.assertIn('cancel_capture', self.kinds(intents))
        self.assert_latched('failed', intents)

    def test_pending_ack_is_required_and_must_be_durable(self):
        self.request_capture()
        intents = self.pending(durable=False)
        self.assertEqual(self.machine.reason, 'invalid_pending_acknowledgement')
        self.assertIn('cancel_capture', self.kinds(intents))
        self.assert_latched('failed', intents)

    def test_result_before_pending_ack_never_releases_movement(self):
        self.request_capture()
        intents = self.result(event_id='SOFTWARE_FIXTURE_EVENT_1')
        self.assertEqual(self.machine.reason, 'pending_not_confirmed')
        self.assert_latched('failed', intents)
        self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_duplicate_pending_ack_is_idempotent(self):
        self.request_capture(); self.pending()
        self.assertEqual(self.kinds(self.pending()), ['hold_stop'])
        self.assertEqual(self.machine.state, 'awaiting_result')
        self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_one_station_matching_durable_success_completes_while_stopped(self):
        self.request_capture(); self.pending()
        intents = self.result()
        self.assert_latched('completed', intents)
        self.assertEqual(self.machine.snapshot()['outcomes'][0]['status'], 'success')
        self.assertEqual(len(self.machine.snapshot()['outcomes']), 1)

    def test_two_station_mission_advances_only_after_durable_success(self):
        first_request = self.request_capture(stations=2); self.pending()
        self.assertEqual(self.kinds(self.result()), ['follow_line'])
        self.assertEqual(self.machine.index, 1)
        self.step()  # Explicit marker-clear observation arms the next station.
        second_request = self.request_capture(start=False)
        self.assertNotEqual(first_request, second_request)
        self.pending(event_id='SOFTWARE_FIXTURE_EVENT_2')
        intents = self.result()
        self.assertEqual(self.kinds(intents), ['hold_stop'])
        self.assertEqual(self.machine.state, 'completed')
        self.assertEqual([outcome['station_id'] for outcome in self.machine.snapshot()['outcomes']],
                         ['SOFTWARE_FIXTURE_S1', 'SOFTWARE_FIXTURE_S2'])

    def test_duplicate_sticky_marker_does_not_create_second_capture(self):
        self.request_capture(stations=2); self.pending(); self.result()
        for _ in range(4):
            intents = self.step(marker_id='SOFTWARE_FIXTURE_M1')
            self.assertEqual(self.kinds(intents), ['follow_line'])
            self.assertEqual(self.machine.index, 1)
        self.assertEqual(len(self.machine.snapshot()['outcomes']), 1)
        self.assertEqual(self.machine.state, 'following')

    def test_next_marker_without_clear_is_refused(self):
        self.request_capture(stations=2); self.pending(); self.result()
        intents = self.step(marker_id='SOFTWARE_FIXTURE_M2')
        self.assertEqual(self.machine.reason, 'marker_not_cleared')
        self.assert_latched('failed', intents)

    def test_old_marker_reappearing_after_clear_is_not_a_new_station(self):
        self.request_capture(stations=2); self.pending(); self.result(); self.step()
        intents = self.step(marker_id='SOFTWARE_FIXTURE_M1')
        self.assertEqual(self.machine.reason, 'unexpected_marker')
        self.assert_latched('failed', intents)

    def test_unexpected_marker_or_line_loss_latches_stop(self):
        for changes, reason in (({'marker_id': 'SOFTWARE_FIXTURE_UNKNOWN'}, 'unexpected_marker'),
                                ({'line_trackable': False}, 'line_not_trackable')):
            self.setUp(); self.start()
            intents = self.step(**changes)
            self.assertEqual(self.machine.reason, reason)
            self.assert_latched('failed', intents)

    def test_success_does_not_resume_if_next_route_not_trackable(self):
        self.request_capture(stations=2); self.pending()
        intents = self.result(line_trackable=False)
        self.assertEqual(self.machine.reason, 'line_not_trackable')
        self.assert_latched('failed', intents)
        self.assertEqual(len(self.machine.snapshot()['outcomes']), 1)

    def test_sticky_marker_does_not_bypass_trackability(self):
        self.request_capture(stations=2); self.pending(); self.result()
        intents = self.step(marker_id='SOFTWARE_FIXTURE_M1', line_trackable=False)
        self.assertEqual(self.machine.reason, 'line_not_trackable')
        self.assert_latched('failed', intents)

    def test_each_failed_terminal_is_retained_as_status_not_retried_into_success(self):
        for status in ('sensor_failure', 'request_failure', 'timeout', 'cancelled'):
            with self.subTest(status=status):
                self.setUp(); self.request_capture(stations=2); self.pending()
                intents = self.result(status)
                self.assert_latched('failed', intents)
                state = self.machine.snapshot()
                self.assertEqual(state['outcomes'], [{'station_id': 'SOFTWARE_FIXTURE_S1',
                    'request_id': 'SOFTWARE_FIXTURE_MISSION:1',
                    'event_id': 'SOFTWARE_FIXTURE_EVENT_1', 'status': status}])
                self.assertEqual(state['station_index'], 0)
                self.assertEqual(self.result(), ())
                self.assertEqual(self.machine.snapshot()['outcomes'], state['outcomes'])

    def test_nondurable_or_nonterminal_result_never_allows_follow(self):
        for status, durable in (('success', False), ('pending', True), ('unknown', True), ([], True)):
            with self.subTest(status=status, durable=durable):
                self.setUp(); self.request_capture(stations=2); self.pending()
                intents = self.result(status, durable=durable)
                self.assertEqual(self.machine.reason, 'invalid_or_nondurable_terminal')
                self.assert_latched('failed', intents)
                self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_wrong_station_or_event_for_active_request_aborts(self):
        for changes in ({'station_id': 'SOFTWARE_FIXTURE_WRONG'}, {'event_id': 'SOFTWARE_FIXTURE_WRONG'}):
            self.setUp(); self.request_capture(); self.pending()
            intents = self.result(**changes)
            self.assertEqual(self.machine.reason, 'result_correlation_mismatch')
            self.assert_latched('failed', intents)

    def test_old_request_result_is_ignored_without_refreshing_liveness(self):
        self.request_capture(); self.pending()
        previous = self.machine._updated
        intents = self.result(request_id='SOFTWARE_FIXTURE_OTHER_MISSION:1')
        self.assertEqual(intents, ())
        self.assertEqual(self.machine._updated, previous)
        self.assertEqual(self.machine.state, 'awaiting_result')
        self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_duplicate_and_late_success_cannot_complete_a_later_station(self):
        first = self.request_capture(stations=2); self.pending(); self.result()
        self.assertEqual(self.result(request_id=first, event_id='SOFTWARE_FIXTURE_EVENT_1',
                                     station_id='SOFTWARE_FIXTURE_S1'), ())
        self.step()
        self.request_capture(start=False)
        self.pending(event_id='SOFTWARE_FIXTURE_EVENT_2')
        self.assertEqual(self.result(request_id=first, event_id='SOFTWARE_FIXTURE_EVENT_1',
                                     station_id='SOFTWARE_FIXTURE_S1'), ())
        self.assertEqual(self.machine.state, 'awaiting_result')
        self.assertEqual(len(self.machine.snapshot()['outcomes']), 1)

    def test_event_identifier_cannot_be_reused_for_another_station(self):
        self.request_capture(stations=2); self.pending(); self.result(); self.step()
        self.request_capture(start=False)
        intents = self.pending(event_id='SOFTWARE_FIXTURE_EVENT_1')
        self.assertEqual(self.machine.reason, 'invalid_pending_acknowledgement')
        self.assert_latched('failed', intents)

    def test_capture_timeout_cancels_outstanding_request_and_stays_failed(self):
        self.request_capture(); self.pending()
        for _ in range(11):
            intents = self.step()
        self.assertEqual(self.machine.reason, 'capture_timeout')
        self.assertIn('cancel_capture', self.kinds(intents))
        self.assert_latched('failed', intents)
        self.assertEqual(self.result(), ())
        self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_result_at_capture_deadline_is_late_even_if_success(self):
        self.request_capture(); self.pending()
        for _ in range(10):
            self.step()
        intents = self.result()
        self.assertEqual(self.machine.reason, 'capture_timeout')
        self.assert_latched('failed', intents)

    def test_mission_timeout_never_automatically_restarts_following(self):
        self.plan = replace(self.plan, timing=replace(self.plan.timing, mission_timeout=.5))
        self.machine = MissionPlanner(self.plan)
        self.machine.start(self.observation(), now=self.now)
        for _ in range(4):
            intents = self.step()
        self.assertEqual(self.machine.reason, 'mission_timeout')
        self.assert_latched('failed', intents)

    def test_cancel_before_or_after_pending_latches_and_late_result_is_ignored(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                self.setUp(); self.request_capture()
                if pending: self.pending()
                intents = self.machine.cancel()
                self.assertEqual(self.kinds(intents), ['hold_stop', 'cancel_capture'])
                self.assert_latched('cancelled', intents)
                self.assertEqual(self.result(event_id='SOFTWARE_FIXTURE_EVENT_1'), ())
                self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_cancel_during_following_cannot_be_resumed_by_new_observation(self):
        self.start()
        self.assert_latched('cancelled', self.machine.cancel())

    def test_estop_method_needs_no_fresh_sensor_and_never_auto_recovers(self):
        self.request_capture(); self.pending()
        self.now += 100
        intents = self.machine.emergency_stop()
        self.assertEqual(self.kinds(intents), ['hold_stop', 'cancel_capture'])
        self.assert_latched('emergency_stopped', intents)
        self.assertEqual(self.result(), ())

    def test_estop_input_and_control_fault_always_stop(self):
        for changes, state in (({'emergency_stop': True}, 'emergency_stopped'),
                               ({'control_healthy': False}, 'failed')):
            self.setUp(); self.request_capture(); self.pending()
            intents = self.step(**changes)
            self.assertIn('cancel_capture', self.kinds(intents))
            self.assert_latched(state, intents)

    def test_stationarity_or_zero_command_lost_while_waiting_cancels_capture(self):
        for changes in ({'zero_command': False}, {'stationary_confirmed': False}):
            self.setUp(); self.request_capture(); self.pending()
            intents = self.step(**changes)
            self.assertEqual(self.machine.reason, 'stationary_hold_lost')
            self.assertIn('cancel_capture', self.kinds(intents))
            self.assert_latched('failed', intents)

    def test_stale_future_replayed_and_reordered_observations_fail_closed(self):
        for kind in ('stale', 'future', 'sequence', 'time', 'gap', 'clock_backwards'):
            with self.subTest(kind=kind):
                self.setUp(); self.start()
                observed = self.observation()
                now = self.now
                if kind == 'stale': observed = replace(observed, observed_at=now - .25)
                elif kind == 'future': observed = replace(observed, observed_at=now + .125)
                elif kind == 'sequence': observed = replace(observed, sequence=self.machine._sequence)
                elif kind == 'time': observed = replace(observed, observed_at=self.machine._sampled)
                elif kind == 'gap': now += .5; observed = replace(observed, observed_at=now)
                else: now -= .25; observed = replace(observed, observed_at=now)
                intents = self.machine.advance(observed, now=now)
                self.assert_latched('failed', intents)

    def test_source_pin_boolean_and_malformed_line_changes_fail_closed(self):
        for changes in ({'source_id': 'SOFTWARE_FIXTURE_OTHER'}, {'physical_pins': (15, 18, 16, 99)},
                        {'line_values': (True, 0, 0, 0)}, {'line_values': (0, 1)},
                        {'sequence': True}, {'zero_command': 0}, {'control_healthy': None}):
            with self.subTest(changes=changes):
                self.setUp(); self.start()
                self.assert_latched('failed', self.step(**changes))

    def test_start_estop_does_not_emit_follow(self):
        intents = self.machine.start(self.observation(emergency_stop=True), now=self.now)
        self.assert_latched('emergency_stopped', intents)

    def test_reference_mutation_after_start_cannot_change_cached_calibration(self):
        self.start()
        self.plan.calibration.white_reference['samples'][0]['value'] = [1, 1, 1, 1]
        self.assertEqual(self.kinds(self.step()), ['follow_line'])
        with self.assertRaises(MissionError):
            validate_plan(self.plan)

    def test_snapshot_copies_outcomes_and_cannot_rewrite_terminal_state(self):
        self.request_capture(); self.pending(); self.result()
        state = self.machine.snapshot()
        state['outcomes'][0]['status'] = 'SOFTWARE_FIXTURE_MUTATION'
        state['outcomes'].clear()
        state['state'] = 'following'
        self.assertEqual(self.machine.state, 'completed')
        self.assertEqual(self.machine.snapshot()['outcomes'][0]['status'], 'success')

    def test_idle_cancel_is_safe_even_without_valid_plan(self):
        machine = MissionPlanner()
        self.assertEqual(self.kinds(machine.cancel()), ['hold_stop'])
        self.assertEqual(machine.snapshot()['state'], 'cancelled')

    def test_matching_success_with_stale_observation_does_not_release_movement(self):
        self.request_capture(stations=2); self.pending()
        intents = self.result(observed_at=self.now - .25)
        self.assertEqual(self.machine.reason, 'stale_or_future_observation')
        self.assertIn('cancel_capture', self.kinds(intents))
        self.assert_latched('failed', intents)
        self.assertEqual(self.machine.snapshot()['outcomes'], [])

    def test_missing_pending_ack_times_out_without_fabricating_a_row(self):
        self.request_capture()
        for _ in range(12):
            intents = self.step()
        self.assertEqual(self.machine.reason, 'capture_timeout')
        self.assertEqual(self.kinds(intents), ['hold_stop', 'cancel_capture'])
        self.assertEqual(self.machine.snapshot()['outcomes'], [])
        self.assertEqual(self.pending(), ())
        self.assert_latched('failed', intents)

    def test_cancel_after_accepted_terminal_does_not_rewrite_completed_outcome(self):
        self.request_capture(); self.pending(); self.result()
        before = self.machine.snapshot()
        self.assertEqual(self.kinds(self.machine.cancel()), ['hold_stop'])
        self.assertEqual(self.result(), ())
        self.assertEqual(self.machine.snapshot(), before)

    def test_invalid_monotonic_numbers_fail_closed_without_uncaught_errors(self):
        for value in (False, None, float('nan'), float('inf'), 10 ** 1000):
            with self.subTest(kind=type(value)):
                self.setUp(); self.start()
                observed = self.observation()
                intents = self.machine.advance(observed, now=value)
                self.assertEqual(self.machine.reason, 'invalid_now')
                self.assert_latched('failed', intents)

    def test_module_imports_only_pure_data_libraries(self):
        tree = ast.parse(inspect.getsource(mission_plan))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported, {'dataclasses', 'math', 're', 'line_calibration'})
        self.assertFalse({'speed', 'pwm', 'uart', 'gpio', 'temperature_c', 'humidity_pct'} &
                         set(Intent.__dataclass_fields__))


if __name__ == '__main__':
    unittest.main()
