"""SOFTWARE_FIXTURE mock-only line follower; no hardware/source program imports."""
import copy
import itertools
import json
import threading
import time
import unittest
from unittest.mock import patch

from basic_line_follower import BasicLineFollower, steering


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Feed:
    def __init__(self, clock):
        self.clock = clock
        self.reads = 0
        self.item = dict(status='live', value=[1, 0, 0, 1], monotonic=clock(),
                         age_seconds=0.0, successes=1, failures=0,
                         physical_pins=[15, 18, 16, 11], last_good={'value': [0, 0, 0, 0]})
        self.age_override = None

    def publish(self, values=None):
        self.item['monotonic'] = self.clock()
        self.item['successes'] += 1
        if values is not None:
            self.item['value'] = list(values)

    def __call__(self):
        self.reads += 1
        item = copy.deepcopy(self.item)
        item['age_seconds'] = self.clock() - item['monotonic'] if self.age_override is None else self.age_override
        return item


class Motor:
    def __init__(self):
        self.calls, self.drives = [], []
        self.session = None
        self.sequence = -1
        self.outputs = (0, 0)
        self.fail_arm = self.fail_drive = self.fail_stop = False
        self.drive_return = None
        self.drive_event, self.stop_event = threading.Event(), threading.Event()

    def command(self, data):
        self.calls.append(dict(data))
        if data == {'action': 'stop'}:
            self.session = None
            self.outputs = (0, 0)
            self.stop_event.set()
            if self.fail_stop:
                raise RuntimeError('SOFTWARE_FIXTURE_SECRET_MUST_NOT_LEAK')
            return {'armed': False, 'fault': None}
        if data == {'action': 'arm'}:
            if self.fail_arm or self.session:
                raise RuntimeError('SOFTWARE_FIXTURE_SECRET_MUST_NOT_LEAK')
            self.session = 'SOFTWARE_FIXTURE_MOTOR_SESSION'
            self.sequence = -1
            return {'session': self.session, 'armed': True, 'fault': None}
        raise AssertionError('SOFTWARE_FIXTURE unexpected motor command')

    def drive_sides(self, session, seq, left, right):
        if self.fail_drive:
            raise RuntimeError('SOFTWARE_FIXTURE_SECRET_MUST_NOT_LEAK')
        if session != self.session or self.session is None or seq <= self.sequence:
            raise ValueError('SOFTWARE_FIXTURE_BAD_MOTOR_SESSION')
        assert type(left) is int and type(right) is int and -220 <= left <= 220 and -220 <= right <= 220
        self.sequence, self.outputs = seq, (left, right)
        self.drives.append((seq, left, right))
        self.drive_event.set()
        return self.drive_return


class SteeringTests(unittest.TestCase):
    def test_all_sixteen_patterns_have_explicit_adapted_actions_and_white_policy(self):
        expected = {
            (0, 0, 0, 0): (50, 50, 'broad_line'),
            (0, 0, 1, 0): (-50, 50, 'hard_left'),
            (0, 1, 0, 0): (50, -50, 'hard_right'),
            (0, 1, 1, 0): (50, 50, 'broad_line'),
            (0, 0, 0, 1): (-50, 50, 'hard_left'),
            (0, 0, 1, 1): (-50, 50, 'hard_left'),
            (0, 1, 0, 1): (-50, 50, 'hard_left'),
            (0, 1, 1, 1): (-50, 50, 'hard_left'),
            (1, 0, 0, 0): (50, -50, 'hard_right'),
            (1, 0, 0, 1): (50, 50, 'center'),
            (1, 0, 1, 0): (50, -50, 'hard_right'),
            (1, 0, 1, 1): (30, 50, 'inner_left'),
            (1, 1, 0, 0): (50, -50, 'hard_right'),
            (1, 1, 0, 1): (50, 30, 'inner_right'),
            (1, 1, 1, 0): (50, -50, 'hard_right'),
        }
        checked = 0
        for values in itertools.product((0, 1), repeat=4):
            with self.subTest(values=values):
                if values in expected:
                    self.assertEqual(steering(values)[:3], expected[values])
                else:
                    with self.assertRaisesRegex(ValueError, 'line_lost|ambiguous_line'):
                        steering(values)
                checked += 1
        self.assertEqual(checked, 16)

    def test_error_reports_current_digital_rule_side_not_an_angle_or_pid(self):
        for values, error in (((0, 1, 1, 1), -3), ((1, 0, 1, 1), -1),
                              ((1, 1, 0, 1), 1), ((1, 1, 1, 0), 3),
                              ((0, 0, 1, 1), -3), ((1, 0, 0, 1), 0)):
            self.assertEqual(steering(values)[3], error)

    def test_both_outers_use_inner_evidence_and_symmetric_black_has_no_side(self):
        cases=(([0,0,0,0],(50,50,'broad_line',0.0)),
               ([0,1,1,0],(50,50,'broad_line',0.0)),
               ([0,0,1,0],(-50,50,'hard_left',-3.0)),
               ([0,1,0,0],(50,-50,'hard_right',3.0)))
        for values,expected in cases:self.assertEqual(steering(values,220),expected)

    def test_every_speed_and_visible_pattern_remain_bounded_in_speed_units(self):
        for speed in range(50, 221):
            for values in itertools.product((0, 1), repeat=4):
                try:
                    left, right, _, _ = steering(values, speed)
                except ValueError:
                    continue
                bound = 50
                self.assertTrue(-bound <= left <= bound and -bound <= right <= bound)
                self.assertIs(type(left), int); self.assertIs(type(right), int)

    def test_invalid_values_and_speed_rejected(self):
        for values in (None, [], [0, 1, 1], [0, 1, 1, 2], [False, 1, 1, 1], '0111', [0., 1, 1, 1]):
            with self.assertRaises(ValueError): steering(values)
        for speed in (0, 1, 49, -1, 221, True, '100', 100.0, None):
            with self.assertRaises(ValueError): steering([1, 0, 0, 1], speed)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.feed = Feed(self.clock)
        self.motor = Motor()
        self.worker = BasicLineFollower(self.motor, self.feed, clock=self.clock, threaded=False, channel_permutation=(0, 1, 2, 3))
        self.addCleanup(self.worker.close)

    def start(self, **kwargs):
        return self.worker.start(**kwargs)

    def active(self, **kwargs):
        token = self.start(**kwargs)
        self.worker.heartbeat(token, 0)
        return token

    def test_constructor_and_snapshot_never_arm_read_start_thread_or_move(self):
        self.assertEqual(self.motor.calls, [])
        self.assertEqual(self.feed.reads, 0)
        self.assertIsNone(self.worker._thread)
        for _ in range(3):
            state = self.worker.snapshot()
            self.assertTrue(state['ready'])
            self.assertFalse(state['running'])
            self.worker.tick()
        self.assertEqual(self.motor.calls, [])
        self.assertEqual(self.feed.reads, 0)

    def test_start_returns_opaque_owner_and_only_arms_until_first_heartbeat(self):
        token = self.start()
        self.assertIsInstance(token, str); self.assertGreaterEqual(len(token), 32)
        self.assertNotEqual(token, self.motor.session)
        for _ in range(3):
            state = self.worker.tick()
            self.assertEqual(state['state'], 'waiting_heartbeat')
        self.assertEqual(self.motor.calls, [{'action': 'arm'}])
        self.assertEqual(self.motor.drives, [])
        self.assertEqual(self.feed.reads, 0)
        self.worker.heartbeat(token, 0)
        self.assertEqual(self.motor.drives, [])
        self.assertEqual(self.worker.tick()['state'], 'straight')
        self.assertEqual(self.motor.drives, [(0, 50, 50)])

    def test_snapshot_and_heartbeat_never_leak_either_session(self):
        token = self.active()
        for state in (self.worker.snapshot(), self.worker.heartbeat(token, 1), self.worker.tick()):
            raw = json.dumps(state)
            self.assertNotIn(token, raw)
            self.assertNotIn('SOFTWARE_FIXTURE_MOTOR_SESSION', raw)
            self.assertNotIn('session', raw)

    def test_wrong_owner_and_invalid_seq_do_not_refresh_owner_or_stop_valid_owner(self):
        token = self.active()
        owner_time = self.worker._owner_time
        for bad in ('wrong', '', None, True, '中文'):
            with self.assertRaises(ValueError): self.worker.heartbeat(bad, 1)
        for seq in (0, -1, True, 1.0, '1', None, 2**53):
            with self.assertRaises(ValueError): self.worker.heartbeat(token, seq)
        self.assertEqual(self.worker._owner_time, owner_time)
        self.assertTrue(self.worker.snapshot()['running'])
        self.assertEqual(self.motor.calls, [{'action': 'arm'}])
        self.worker.heartbeat(token, 1)
        self.assertEqual(self.worker.tick()['state'], 'straight')

    def test_out_of_order_heartbeats_rejected(self):
        token = self.active()
        self.worker.heartbeat(token, 20)
        with self.assertRaises(ValueError): self.worker.heartbeat(token, 19)
        with self.assertRaises(ValueError): self.worker.heartbeat(token, 20)
        self.worker.heartbeat(token, 21)

    def test_missing_first_heartbeat_expires_without_any_drive(self):
        self.start(); self.clock.now += .801
        state = self.worker.tick()
        self.assertEqual(state['state'], 'heartbeat_expired')
        self.assertFalse(state['running'])
        self.assertEqual(self.motor.drives, [])
        self.assertEqual(self.motor.calls[-1], {'action': 'stop'})
        self.assertIsNone(self.motor.session)

    def test_owner_expiry_not_revived_by_late_heartbeat(self):
        token = self.active(); self.worker.tick()
        self.clock.now += .801
        with self.assertRaisesRegex(ValueError, 'owner_heartbeat_expired'):
            self.worker.heartbeat(token, 1)
        self.assertEqual(self.worker.snapshot()['state'], 'heartbeat_expired')
        self.assertIsNone(self.motor.session)
        self.feed.publish()
        self.worker.tick()
        self.assertEqual(len(self.motor.drives), 1)
        with self.assertRaises(ValueError): self.worker.heartbeat(token, 2)

    def test_stopped_state_does_not_restart_without_explicit_start(self):
        token = self.active(); self.worker.tick(); self.worker.stop()
        for _ in range(5): self.feed.publish(); self.worker.tick()
        self.assertEqual(len(self.motor.drives), 1)
        with self.assertRaises(ValueError): self.worker.heartbeat(token, 1)
        new = self.start()
        self.assertNotEqual(new, token)
        self.worker.tick()
        self.assertEqual(len(self.motor.drives), 1)
        self.worker.heartbeat(new, 0); self.worker.tick()
        self.assertEqual(len(self.motor.drives), 2)

    def test_multiple_start_and_bad_speeds_do_not_rearm(self):
        for speed in (0, 1, 49, 221, True, 1.5, '100'):
            with self.assertRaises(ValueError): self.start(speed=speed)
        self.assertEqual(self.motor.calls, [])
        self.start()
        with self.assertRaisesRegex(ValueError, 'already_running'): self.start()
        self.assertEqual(self.motor.calls, [{'action': 'arm'}])

    def test_base_speed_boundaries_use_continuous_inner_differential_and_fixed_outer_strength(self):
        for speed in (50, 220):
            self.worker.stop(); self.active(speed=speed)
            self.feed.item = Feed(self.clock).item
            self.feed.item['value'] = [1, 0, 1, 1]
            state = self.worker.tick()
            self.assertEqual((state['left'], state['right']), (30, min(speed,50)))
        self.worker.stop(); self.active(speed=80)
        self.feed.item = Feed(self.clock).item
        self.feed.item['value'] = [0, 0, 1, 1]
        state = self.worker.tick()
        self.assertEqual((state['left'], state['right']), (-50, 50))

    def test_straight_and_left_right_strength_state_fields(self):
        cases = [([1, 0, 0, 1], 'straight', 'center'), ([1, 0, 1, 1], 'left', 'inner'),
                 ([0, 0, 1, 1], 'left', 'hard'), ([0, 1, 1, 1], 'left', 'hard'),
                 ([1, 1, 0, 1], 'right', 'inner'), ([1, 1, 0, 0], 'right', 'hard'),
                 ([1, 1, 1, 0], 'right', 'hard')]
        for values, direction, strength in cases:
            self.worker.stop(); self.active()
            self.clock.now += .1; self.feed.publish(values)
            state = self.worker.tick()
            self.assertEqual(state['state'], direction)
            self.assertEqual(state['turn_strength'], strength)
            self.assertEqual(state['last_values'], values)

    def test_all_black_continues_forward_without_station_or_sample_request(self):
        self.active();self.feed.item['value']=[0,0,0,0]
        state=self.worker.tick()
        self.assertTrue(state['running']);self.assertEqual((state['left'],state['right']),(50,50))
        self.assertFalse(state['station_detection']);self.assertFalse(state['line_gap_active'])

    def test_startup_white_without_actual_pair_stops_latches_and_ignores_last_good(self):
        token = self.active()
        self.clock.now += .1; self.feed.publish([1, 1, 1, 1])
        state = self.worker.tick()
        self.assertEqual(state['state'], 'lost_line')
        self.assertEqual(state['reason'], 'line_lost')
        self.assertFalse(state['running']); self.assertIsNone(self.motor.session)
        self.feed.publish([1, 0, 0, 1]); self.worker.tick()
        self.assertEqual(len(self.motor.drives), 0)
        with self.assertRaises(ValueError): self.worker.heartbeat(token, 1)

    def test_all_both_outer_patterns_start_without_prior_motion(self):
        for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
            with self.subTest(values=values):
                self.worker.stop();self.motor.drives.clear();self.active();self.feed.item['value']=values
                state=self.worker.tick()
                self.assertTrue(state['running']);self.assertEqual(state['reason'],steering(values,50)[2])
                self.assertEqual(self.motor.drives[-1][1:],steering(values,50)[:2])
                self.assertFalse(state['line_gap_active'])

    def test_nonlive_wrong_values_and_changed_pins_stop(self):
        damages = [{'status': 'error'}, {'status': 'stale'}, {'status': 'starting'},
                   {'value': [1, 0, 1]}, {'value': [True, 0, 0, 1]}, {'value': [1, 2, 0, 1]},
                   {'physical_pins': [11, 18, 16, 15]}, {'successes': True}, {'successes': 0}]
        for changes in damages:
            with self.subTest(changes=changes):
                self.worker.stop(); self.active(); self.feed.item = Feed(self.clock).item
                self.feed.item.update(changes)
                self.assertFalse(self.worker.tick()['running'])
                self.assertIsNone(self.motor.session)

    def test_reported_stale_and_actual_stale_even_with_forged_zero_age_stop(self):
        for age, mono_delta in ((.401, 0), (0, -.401)):
            self.worker.stop(); self.active(); self.feed.item = Feed(self.clock).item
            self.feed.item['monotonic'] += mono_delta
            self.feed.age_override = age
            state = self.worker.tick()
            self.assertEqual(state['state'], 'sensor_stale')
            self.assertIsNone(self.motor.session)

    def test_future_and_invalid_sample_clocks_stop(self):
        for changes in ({'monotonic': self.clock() + .001}, {'monotonic': True},
                        {'monotonic': float('nan')}, {'monotonic': float('inf')}, {'monotonic': -1}):
            self.worker.stop(); self.active(); self.feed.item = Feed(self.clock).item
            self.feed.item.update(changes); self.feed.age_override = 0
            self.assertFalse(self.worker.tick()['running'])
        for age in (None, True, -1, float('nan'), float('inf')):
            self.worker.stop(); self.active(); self.feed.item = Feed(self.clock).item
            self.worker.line_source = lambda age=age: {**self.feed.item, 'age_seconds': age}
            self.assertFalse(self.worker.tick()['running'])

    def test_replayed_and_metadata_inconsistent_sample_stop(self):
        for changes in ({'successes': 0}, {'monotonic': self.clock() - .1},
                        {'monotonic': self.clock() + .1}, {'successes': 2}, {'value': [0, 1, 1, 1]}):
            with self.subTest(changes=changes):
                self.worker.stop(); self.active(); self.feed.item = Feed(self.clock).item
                self.worker.tick()
                self.clock.now += .2
                self.feed.item.update(changes)
                self.assertFalse(self.worker.tick()['running'])
                self.assertIsNone(self.motor.session)

    def test_identical_fresh_cache_can_renew_motor_but_not_its_sample_timestamp(self):
        self.active(); self.worker.tick(); original = self.worker._sample_mono
        self.clock.now += .1
        self.assertTrue(self.worker.tick()['running'])
        self.assertEqual(self.worker._sample_mono, original)
        self.clock.now += .301
        self.assertEqual(self.worker.tick()['state'], 'sensor_stale')
        self.assertEqual(len(self.motor.drives), 2)

    def test_continuous_fresh_data_and_owner_heartbeat_can_run_without_total_time_cap(self):
        owner = self.active()
        for index in range(1000):
            self.clock.now += .1
            self.feed.publish()
            self.worker.heartbeat(owner, index + 1)
            self.assertTrue(self.worker.tick()['running'])
        self.assertEqual(len(self.motor.drives), 1000)
        self.assertEqual(self.motor.sequence, 999)

    def test_source_exception_stops_without_leaking_exception_secret(self):
        self.active()
        def failed_source(): raise OSError('SOFTWARE_FIXTURE_SECRET_MUST_NOT_LEAK')
        self.worker.line_source = failed_source
        state = self.worker.tick()
        self.assertEqual(state['reason'], 'line_source_failed')
        self.assertNotIn('SECRET', json.dumps(state))
        self.assertIsNone(self.motor.session)

    def test_motor_arm_failure_or_invalid_reply_stops(self):
        self.motor.fail_arm = True
        with self.assertRaisesRegex(ValueError, 'motor_arm_failed'): self.start()
        self.assertIsNone(self.motor.session)
        self.assertFalse(self.worker.snapshot()['running'])
        self.assertNotIn('SECRET', json.dumps(self.worker.snapshot()))
        self.motor.fail_arm = False
        original = self.motor.command
        def bad_arm(command): return {} if command['action'] == 'arm' else original(command)
        with patch.object(self.motor, 'command', side_effect=bad_arm):
            with self.assertRaises(ValueError): self.start()
        self.assertFalse(self.worker.snapshot()['running'])

    def test_motor_drive_fault_exception_and_fault_reply_stop_and_revoke(self):
        for kind in ('raise', 'fault', 'disarmed'):
            with self.subTest(kind=kind):
                self.motor.fail_drive = False; self.motor.drive_return = None
                self.worker.stop(); self.active()
                if kind == 'raise': self.motor.fail_drive = True
                elif kind == 'fault': self.motor.drive_return = {'fault': 'SOFTWARE_FIXTURE_ERROR'}
                else: self.motor.drive_return = {'armed': False}
                state = self.worker.tick()
                self.assertEqual(state['reason'], 'motor_drive_failed')
                self.assertFalse(state['running']); self.assertIsNone(self.motor.session)
                self.assertNotIn('SECRET', json.dumps(state))

    def test_external_motor_disarm_does_not_silently_rearm(self):
        self.active(); self.worker.tick(); self.motor.command({'action': 'stop'})
        self.assertFalse(self.worker.tick()['running'])
        self.assertEqual(sum(call['action'] == 'arm' for call in self.motor.calls), 1)

    def test_stop_is_idempotent_revokes_both_sessions_and_stops_before_start_too(self):
        self.worker.stop(); self.worker.stop()
        self.assertEqual(self.motor.calls, [{'action': 'stop'}])
        owner = self.active(); self.worker.tick()
        self.worker.stop(); calls = len(self.motor.calls); self.worker.stop()
        self.assertEqual(len(self.motor.calls), calls)
        self.assertIsNone(self.motor.session)
        self.assertEqual(self.motor.outputs, (0, 0))
        with self.assertRaises(ValueError): self.worker.heartbeat(owner, 1)

    def test_failed_stop_retries_on_later_stop_or_close_and_never_runs(self):
        self.active(); self.motor.fail_stop = True
        state = self.worker.stop()
        self.assertEqual(state['reason'], 'motor_stop_failed')
        self.assertFalse(state['running'])
        self.motor.fail_stop = False
        self.worker.stop()
        self.assertTrue(self.worker._stop_sent)
        self.assertIsNone(self.motor.session)

    def test_clock_regression_and_bad_clock_stop(self):
        self.active(); self.worker.tick(); self.clock.now -= .1
        self.assertEqual(self.worker.tick()['reason'], 'clock_regressed')
        for value in (True, None, -1, float('nan'), float('inf')):
            clock = Clock(); feed = Feed(clock); motor = Motor()
            worker = BasicLineFollower(motor, feed, clock=clock, threaded=False, channel_permutation=(0, 1, 2, 3))
            try:
                token = worker.start(); worker.heartbeat(token, 0)
                clock.now = value
                self.assertEqual(worker.tick()['reason'], 'invalid_clock')
                self.assertIsNone(motor.session)
            finally: worker.close()

    def test_source_read_that_outlives_owner_timeout_never_drives(self):
        self.active()
        def slow_source():
            self.clock.now += .9
            self.feed.publish()
            return self.feed()
        self.worker.line_source = slow_source
        self.assertEqual(self.worker.tick()['state'], 'heartbeat_expired')
        self.assertEqual(self.motor.drives, [])

    def test_stop_during_blocked_source_discards_late_read_without_waiting(self):
        self.active()
        entered, release = threading.Event(), threading.Event()
        original = self.feed
        def blocked_source():
            entered.set(); release.wait(2)
            return original()
        self.worker.line_source = blocked_source
        tick = threading.Thread(target=self.worker.tick)
        tick.start()
        try:
            self.assertTrue(entered.wait(1))
            began = time.monotonic(); self.worker.stop()
            self.assertLess(time.monotonic() - began, .3)
            self.assertIsNone(self.motor.session)
        finally:
            release.set(); tick.join(2)
        self.assertFalse(tick.is_alive())
        self.assertEqual(self.motor.drives, [])

    def test_old_read_cannot_drive_after_stop_and_new_owner_start(self):
        self.active()
        entered, release = threading.Event(), threading.Event()
        def blocked_source():
            entered.set(); release.wait(2)
            return self.feed()
        self.worker.line_source = blocked_source
        tick = threading.Thread(target=self.worker.tick); tick.start()
        try:
            self.assertTrue(entered.wait(1)); self.worker.stop()
            new_owner = self.start(); self.worker.heartbeat(new_owner, 0)
        finally:
            release.set(); tick.join(2)
        self.assertEqual(self.motor.drives, [])
        self.worker.line_source = self.feed
        self.assertTrue(self.worker.tick()['running'])
        self.assertEqual(len(self.motor.drives), 1)

    def test_stop_serializes_with_inflight_motor_call_and_is_last_action(self):
        self.active()
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        original = self.motor.drive_sides
        def blocked_drive(*args):
            entered.set(); release.wait(2)
            return original(*args)
        self.motor.drive_sides = blocked_drive
        tick = threading.Thread(target=self.worker.tick); tick.start()
        stop = threading.Thread(target=lambda: (self.worker.stop(), stopped.set()))
        try:
            self.assertTrue(entered.wait(1)); stop.start()
            self.assertFalse(stopped.wait(.05))
        finally:
            release.set(); tick.join(2)
            if stop.ident is not None: stop.join(2)
        self.assertFalse(tick.is_alive()); self.assertFalse(stop.is_alive())
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.motor.outputs, (0, 0))
        self.assertEqual(self.motor.calls[-1], {'action': 'stop'})
        self.worker.tick(); self.assertEqual(len(self.motor.drives), 1)

    def test_thread_is_created_only_on_explicit_start_and_never_drives_before_heartbeat(self):
        worker = BasicLineFollower(self.motor, self.feed, clock=self.clock, channel_permutation=(0, 1, 2, 3))
        try:
            self.assertIsNone(worker._thread)
            token = worker.start()
            self.assertTrue(worker._thread.is_alive())
            self.assertFalse(self.motor.drive_event.wait(.25))
            worker.heartbeat(token, 0)
            self.assertTrue(self.motor.drive_event.wait(1))
        finally: worker.close()
        self.assertFalse(worker._thread.is_alive())
        self.assertIsNone(self.motor.session)

    def test_close_joins_without_lock_and_discards_blocked_worker_source(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_source():
            entered.set(); release.wait(2)
            return self.feed()
        worker = BasicLineFollower(self.motor, blocked_source, clock=self.clock, channel_permutation=(0, 1, 2, 3))
        closer = None
        try:
            owner = worker.start(); worker.heartbeat(owner, 0)
            self.assertTrue(entered.wait(1))
            closer = threading.Thread(target=worker.close); closer.start()
            self.assertTrue(self.motor.stop_event.wait(1))
            release.set(); closer.join(2)
            self.assertFalse(closer.is_alive())
            self.assertFalse(worker._thread.is_alive())
            self.assertEqual(self.motor.drives, [])
            self.assertFalse(worker.snapshot()['ready'])
        finally:
            release.set(); worker.close()
            if closer is not None: closer.join(2)

    def test_close_before_start_and_repeated_close_reject_future_start(self):
        self.worker.close(); self.worker.close()
        self.assertEqual(self.worker.snapshot()['state'], 'closed')
        self.assertIsNone(self.worker._thread)
        with self.assertRaisesRegex(ValueError, 'closed'): self.start()
        self.assertEqual(self.motor.drives, [])

    def test_worker_thread_start_failure_stops_and_revokes(self):
        worker = BasicLineFollower(self.motor, self.feed, clock=self.clock, channel_permutation=(0, 1, 2, 3))
        try:
            with patch('threading.Thread.start', side_effect=RuntimeError('SOFTWARE_FIXTURE_THREAD_FAIL')):
                with self.assertRaisesRegex(ValueError, 'worker_start_failed'): worker.start()
            self.assertIsNone(self.motor.session)
            self.assertFalse(worker.snapshot()['running'])
        finally: worker.close()

    def test_unavailable_dependencies_cannot_arm(self):
        worker = BasicLineFollower(None, None, clock=self.clock, threaded=False, channel_permutation=(0, 1, 2, 3))
        self.assertFalse(worker.snapshot()['ready'])
        with self.assertRaisesRegex(ValueError, 'unavailable'): worker.start()
        worker.close()


if __name__ == '__main__':
    unittest.main()
