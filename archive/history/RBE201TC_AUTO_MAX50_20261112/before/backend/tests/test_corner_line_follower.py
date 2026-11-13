"""SOFTWARE_FIXTURE continuous feedback; legacy filename kept, no angle FSM."""
import copy
import itertools
import json
import threading
import time
import unittest
from unittest.mock import patch

from basic_line_follower import BasicLineFollower, steering
from test_basic_line_follower import Clock, Feed, Motor

CENTER = [1, 0, 0, 1]
WHITE = [1, 1, 1, 1]
LEFT = [0, 1, 1, 1]
RIGHT = [1, 1, 1, 0]
STAGES = ('center', 'outer', 'gap', 'inner', 'reacquired')


class FollowerFixture:
    mapping = (0, 1, 2, 3)  # Explicit physical policy fixture, NOT production wire.

    def setUp(self):
        self.worker = None
        self.reset()

    def reset(self, *, speed=84, first=CENTER, mapping=None):
        if self.worker is not None:
            self.worker.close()
        self.clock, self.motor = Clock(), Motor()
        self.feed = Feed(self.clock)
        self.feed.item['value'] = list(first)
        permutation = self.mapping if mapping is None else mapping
        kwargs = {} if permutation is None else {'channel_permutation': permutation}
        self.worker = BasicLineFollower(self.motor, self.feed, clock=self.clock, threaded=False, **kwargs)
        self.addCleanup(self.worker.close)
        self.owner, self.seq = self.worker.start(speed=speed), 0
        self.worker.heartbeat(self.owner, 0)
        return self.worker.tick()

    def step(self, values=None, dt=.05, *, fresh=True, heartbeat=True):
        self.clock.now += dt
        if fresh:
            self.feed.publish(values)
        if heartbeat:
            self.seq += 1
            self.worker.heartbeat(self.owner, self.seq)
        return self.worker.tick()

    def stage(self, name):
        self.reset()
        if name != 'center': self.step(RIGHT)
        if name in ('gap', 'reacquired'): self.step(WHITE)
        if name == 'inner': self.step([1, 0, 1, 1])
        if name == 'reacquired': self.step(CENTER)

    def pair(self, state):
        return state['left'], state['right']

    def assert_stopped(self, state):
        self.assertFalse(state['running'])
        self.assertEqual(self.pair(state), (0, 0))
        self.assertIsNone(self.motor.session)
        self.assertIsNone(self.worker._owner)
        self.assertIsNone(self.worker._motor_session)
        count = len(self.motor.drives)
        self.feed.publish(CENTER)
        self.worker.tick()
        self.assertEqual(len(self.motor.drives), count)
        with self.assertRaises(ValueError): self.worker.heartbeat(self.owner, self.seq + 1)


class ContinuousTests(FollowerFixture, unittest.TestCase):
    def test_policy_snapshot_has_continuous_contract_and_no_legacy_angle_state(self):
        state = self.worker.snapshot()
        self.assertEqual(state['control_policy'], 'continuous_four_digital_v6')
        self.assertEqual(state['max_correction_speed'], 100)
        self.assertEqual(state['line_loss_limit_s'], 1.4)
        self.assertIs(state['direction_recovery_enabled'], False)
        self.assertFalse(state['line_gap_active'])
        self.assertEqual(state['last_visible_values'], CENTER)
        self.assertFalse(any(key.startswith('corner_') for key in state))
        self.assertFalse(hasattr(self.worker, '_corner_active'))
        self.assertFalse(hasattr(self.worker, '_corner_gate'))

    def test_every_visible_sample_immediately_overrides_previous_side(self):
        cases = ((RIGHT, (100, -100)), ([1, 0, 1, 1], (0, 84)),
                 (LEFT, (-100, 100)), ([1, 1, 0, 1], (84, 0)), (CENTER, (84, 84)))
        for values, expected in cases * 3:
            state = self.step(values)
            self.assertTrue(state['running'])
            self.assertEqual(self.pair(state), expected)
            self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.motor.calls, [{'action': 'arm'}])
        self.assertEqual([seq for seq, _, _ in self.motor.drives], list(range(len(self.motor.drives))))

    def test_real_v4_pattern_order_now_corrects_left_and_first_center_moves(self):
        # Observed physical pattern order, replayed at synthetic 50ms intervals;
        # NOT an assertion that these are all original raw acquisition frames.
        expected = (([1, 1, 0, 0], (100, -100)), ([1, 0, 1, 1], (0, 84)),
                    (CENTER, (84, 84)), (CENTER, (84, 84)))
        for values, pair in expected:
            self.assertEqual(self.pair(self.step(values)), pair)
        held = self.step(WHITE)
        self.assertTrue(held['line_gap_active'])
        self.assertEqual(self.pair(held), (84, 84))  # Preserve actual fresh forward, not an old spin.
        self.assertFalse(any(left == right == 0 for _, left, right in self.motor.drives))

    def test_fresh_visible_correction_can_run_far_longer_than_old_1point4_seconds(self):
        for index in range(100):
            values = RIGHT if index % 2 else LEFT
            state = self.step(values)
            self.assertTrue(state['running'])
            self.assertEqual(self.pair(state), (100, -100) if values == RIGHT else (-100, 100))
        self.assertFalse(state['line_gap_active'])
        self.assertEqual(state['line_gap_elapsed_s'], 0)

    def test_single_outer_at_start_needs_no_center_arm_or_inner_departure(self):
        for outer, expected in ((LEFT, (-100, 100)), (RIGHT, (100, -100))):
            state = self.reset(first=outer)
            self.assertEqual(self.pair(state), expected)
            self.assertTrue(state['hold_authorized'])
            self.assertEqual(self.pair(self.step(WHITE)), expected)

    def test_outer_priority_including_split_is_recomputed_without_direction_latch(self):
        for values, expected in (([0, 0, 0, 1], (-100, 100)), ([0, 1, 0, 1], (-100, 100)),
                                 ([1, 0, 1, 0], (100, -100)), ([1, 0, 0, 0], (100, -100))):
            self.assertEqual(self.pair(self.step(values)), expected)
        self.assertEqual(self.pair(self.step(CENTER, dt=.001)), (84, 84))

    def test_weak_actual_pair_is_retained_without_becoming_a_hard_direction_guess(self):
        for inner, expected in (([1, 0, 1, 1], (0, 84)), ([1, 1, 0, 1], (84, 0))):
            self.reset()
            for _ in range(5): self.step(inner)
            count = len(self.motor.drives)
            state = self.step(WHITE)
            self.assertTrue(state['running']); self.assertTrue(state['hold_authorized'])
            self.assertEqual(self.pair(state), expected)
            self.assertEqual(len(self.motor.drives), count + 1)

    def test_gap_holds_last_actually_sent_inner_pair_not_earlier_outer_spin(self):
        for outer, inner, expected in ((RIGHT, [1, 0, 1, 1], (0, 84)),
                                       (LEFT, [1, 1, 0, 1], (84, 0))):
            self.reset(); self.step(outer); self.step(inner)
            last_actual = self.motor.drives[-1][1:]
            self.assertEqual(last_actual, expected)
            state = self.step(WHITE)
            self.assertTrue(state['line_gap_active'])
            self.assertEqual(self.pair(state), last_actual)
            self.assertEqual(state['last_visible_values'], inner)
            self.assertEqual(self.motor.drives[-1][1:], last_actual)

    def test_first_center_clears_gap_and_remains_actual_forward_hold_authority(self):
        self.step(RIGHT); self.step(WHITE)
        state = self.step(CENTER, dt=.001)
        self.assertEqual(self.pair(state), (84, 84))
        self.assertFalse(state['line_gap_active']); self.assertTrue(state['hold_authorized'])
        self.assertEqual(state['line_gap_elapsed_s'], 0)
        self.assertEqual(self.pair(self.step(WHITE)), (84, 84))

    def test_reacquired_opposite_inner_immediately_overrides_gap_without_zero_hold(self):
        self.step(RIGHT); self.step(WHITE)
        state = self.step([1, 0, 1, 1])
        self.assertEqual(self.pair(state), (0, 84))
        self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.pair(self.step(WHITE)), (0, 84))

    def test_fresh_white_and_cache_never_refresh_first_gap_timestamp(self):
        self.step(RIGHT); first = self.step(WHITE)
        started = self.worker._line_gap_started
        for fresh in (False, True, False, True):
            state = self.step(WHITE, dt=.1, fresh=fresh)
            self.assertEqual(self.worker._line_gap_started, started)
            self.assertGreater(state['line_gap_elapsed_s'], first['line_gap_elapsed_s'])
        self.assertEqual(self.pair(state), (100, -100))

    def test_gap_clock_is_first_white_sample_mono_not_late_read_or_latest_white(self):
        self.step(RIGHT)
        self.clock.now += .3; self.feed.publish(WHITE)
        self.feed.item['monotonic'] -= .2
        state = self.worker.tick()
        self.assertAlmostEqual(state['line_gap_elapsed_s'], .2)
        self.assertEqual(self.worker._line_gap_started, self.feed.item['monotonic'])

    def test_continuous_fresh_white_hits_1point4_limit_and_cannot_resume(self):
        for outer in (LEFT, RIGHT):
            self.reset(); self.step(outer); self.step(WHITE)
            for _ in range(3): self.assertTrue(self.step(WHITE, dt=.35)['running'])
            count = len(self.motor.drives)
            state = self.step(WHITE, dt=.351)
            self.assertEqual(state['reason'], 'line_gap_timeout')
            self.assertGreaterEqual(state['line_gap_elapsed_s'], 1.4)
            self.assertEqual(len(self.motor.drives), count)
            self.assert_stopped(state)

    def test_any_fresh_visible_line_resets_only_continuous_gap_not_total_follow_time(self):
        self.step(RIGHT); self.step(WHITE)
        first = self.worker._line_gap_started
        for _ in range(3): self.step(WHITE, dt=.35)
        state = self.step([1, 0, 1, 1], dt=.2)
        self.assertEqual(self.pair(state), (0, 84)); self.assertFalse(state['line_gap_active'])
        state = self.step(WHITE)
        self.assertGreater(self.worker._line_gap_started, first)
        self.assertLess(state['line_gap_elapsed_s'], .01)

    def test_cache_does_not_update_visible_evidence_and_forged_cache_outer_is_rejected(self):
        self.step(RIGHT)
        visible = self.worker._last_visible_values
        mono = self.worker._sample_mono
        self.step(dt=.1, fresh=False)
        self.assertIs(self.worker._last_visible_values, visible)
        self.assertEqual(self.worker._sample_mono, mono)
        self.reset(); self.feed.item['value'] = RIGHT
        state = self.step(dt=.1, fresh=False)
        self.assertEqual(state['reason'], 'inconsistent_line_replay')
        self.assertFalse(state['hold_authorized'])
        self.assert_stopped(state)

    def test_both_outer_and_all_black_retain_actual_pair_without_resetting_gap(self):
        self.stage('gap'); started = self.worker._line_gap_started
        for values in ([0, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 1, 1, 0]):
            state = self.step(values)
            self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
            self.assertEqual(state['hold_pattern'], 'both_outer')
            self.assertEqual(state['last_values'], values)
            self.assertEqual(state['last_visible_values'], RIGHT)
            self.assertEqual(self.worker._line_gap_started, started)
            self.assertEqual(self.pair(state), (100, -100)); self.assertIsNone(state['error'])

    def test_each_stage_stop_mode_change_and_cancel_revoke_without_resume(self):
        for stage in STAGES:
            for reason in ('operator_stop', 'mode_changed'):
                self.stage(stage)
                state = self.worker.stop(reason)
                self.assertEqual(state['reason'], reason)
                self.assert_stopped(state)

    def test_each_stage_owner_expiry_even_with_fresh_line_stops(self):
        for stage in STAGES:
            self.stage(stage)
            state = self.step(CENTER, dt=.801, heartbeat=False)
            self.assertEqual(state['state'], 'heartbeat_expired')
            self.assert_stopped(state)

    def test_each_stage_stale_or_forged_zero_age_stops(self):
        for stage in STAGES:
            self.stage(stage); self.feed.age_override = 0
            state = self.step(dt=.401, fresh=False)
            self.assertEqual(state['state'], 'sensor_stale')
            self.assert_stopped(state)

    def test_each_stage_future_replay_type_and_pin_faults_still_stop(self):
        for stage in STAGES:
            for kind in ('future', 'replay', 'values', 'pins'):
                self.stage(stage); self.clock.now += .05
                if kind == 'future': self.feed.item['monotonic'] = self.clock.now + .01
                elif kind == 'replay': self.feed.item['monotonic'] -= .01
                elif kind == 'values': self.feed.item['value'] = [True, 1, 1, 1]
                else: self.feed.item['physical_pins'] = [11, 16, 18, 15]
                self.feed.age_override = 0
                self.assert_stopped(self.worker.tick())

    def test_each_stage_source_or_motor_fault_stops_without_secret_leak(self):
        for stage in STAGES:
            for source_fault in (True, False):
                self.stage(stage)
                if source_fault:
                    def failed(): raise RuntimeError('SOFTWARE_FIXTURE_SECRET')
                    self.worker.line_source = failed
                else: self.motor.fail_drive = True
                state = self.worker.tick()
                self.assert_stopped(state)
                self.assertNotIn('SECRET', json.dumps(state))

    def test_stop_racing_blocked_source_at_each_stage_discards_late_data(self):
        for stage in STAGES:
            self.stage(stage)
            entered, release = threading.Event(), threading.Event()
            def blocked():
                entered.set(); release.wait(2); return self.feed()
            self.worker.line_source = blocked
            thread = threading.Thread(target=self.worker.tick); thread.start()
            try:
                self.assertTrue(entered.wait(1))
                count = len(self.motor.drives)
                began = time.monotonic(); self.worker.stop()
                self.assertLess(time.monotonic() - began, .3)
            finally:
                release.set(); thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(self.motor.drives), count)
            self.assertIsNone(self.motor.session)

    def test_old_generation_gap_read_cannot_drive_after_new_owner_start(self):
        self.stage('gap')
        entered, release = threading.Event(), threading.Event()
        def blocked():
            entered.set(); release.wait(2); return self.feed()
        self.worker.line_source = blocked
        thread = threading.Thread(target=self.worker.tick); thread.start()
        try:
            self.assertTrue(entered.wait(1)); self.worker.stop()
            count = len(self.motor.drives)
            self.owner = self.worker.start(speed=84); self.seq = 0
            self.worker.heartbeat(self.owner, 0)
        finally:
            release.set(); thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(len(self.motor.drives), count)
        self.worker.line_source = self.feed
        self.assertEqual(self.step(WHITE)['reason'], 'line_lost')

    def test_gap_stop_serializes_after_inflight_drive_and_is_last_motor_action(self):
        self.stage('gap')
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        original = self.motor.drive_sides
        def blocked(*args):
            entered.set(); release.wait(2); return original(*args)
        self.motor.drive_sides = blocked
        thread = threading.Thread(target=self.worker.tick); thread.start()
        stopper = threading.Thread(target=lambda: (self.worker.stop(), stopped.set()))
        try:
            self.assertTrue(entered.wait(1)); stopper.start()
            self.assertFalse(stopped.wait(.02))
        finally:
            release.set(); thread.join(2)
            if stopper.ident is not None: stopper.join(2)
        self.assertFalse(thread.is_alive()); self.assertFalse(stopper.is_alive())
        self.assertTrue(stopped.is_set()); self.assertEqual(self.motor.outputs, (0, 0))
        self.assertEqual(self.motor.calls[-1], {'action': 'stop'})
        count = len(self.motor.drives); self.worker.tick()
        self.assertEqual(len(self.motor.drives), count)

    def test_slow_motor_submission_cannot_bypass_sensor_or_owner_limits(self):
        for stage in ('center', 'outer', 'gap', 'inner'):
            for delay, reason in ((.401, 'stale_line_sample'), (.801, 'owner_heartbeat_expired')):
                self.stage(stage); original = self.motor.drive_sides
                def delayed(*args):
                    self.clock.now += delay; return original(*args)
                self.motor.drive_sides = delayed
                state = self.worker.tick()
                self.assertEqual(state['reason'], reason)
                self.assert_stopped(state)

    def test_slow_gap_write_cannot_move_first_white_timeout_anchor(self):
        self.stage('gap')
        for _ in range(3): self.step(WHITE, dt=.35)
        self.step(WHITE, dt=.29)
        started = self.worker._line_gap_started
        original = self.motor.drive_sides
        def delayed(*args):
            self.clock.now += .1; return original(*args)
        self.motor.drive_sides = delayed
        state = self.step(WHITE, dt=.01)
        self.assertEqual(state['reason'], 'line_gap_timeout')
        self.assertEqual(self.worker._line_gap_started, started)
        self.assert_stopped(state)

    def test_close_at_each_stage_preserves_observation_but_revokes_motion(self):
        for stage in STAGES:
            self.stage(stage); before = self.worker.snapshot()['last_values']
            self.worker.close(); state = self.worker.snapshot()
            self.assertEqual(state['state'], 'closed')
            self.assertEqual(state['last_values'], before)
            self.assertFalse(state['ready']); self.assert_stopped(state)

    def test_new_start_discards_motion_authority_but_preserves_fault_diagnostics(self):
        for unknown in (WHITE, [0, 0, 0, 0]):
            self.reset(); self.step(RIGHT); self.step([0, 0, 0, 0])
            self.clock.now += .401
            failure = self.worker.tick()['last_failure']
            self.assertEqual(failure['reason'], 'stale_line_sample')
            self.worker.stop(); self.owner = self.worker.start(speed=84); self.seq = 0
            self.worker.heartbeat(self.owner, 0)
            state = self.worker.snapshot()
            self.assertEqual(state['last_failure'], failure)
            self.assertEqual(state['last_values'], [0, 0, 0, 0])
            self.assertEqual(state['last_visible_values'], RIGHT)
            self.assertFalse(state['hold_authorized'])
            self.assert_stopped(self.step(unknown))

    def test_first_valid_white_or_ambiguous_raw_saved_before_policy_failure(self):
        for values in (WHITE, [0, 1, 1, 0]):
            state = self.reset(first=values)
            self.assertEqual(state['last_values'], values)
            self.assertEqual(state['last_failure']['values'], values)
            self.assert_stopped(state)

    def test_trace_failure_and_visible_values_are_bounded_copied_and_secret_free(self):
        for index in range(100): self.step(CENTER if index % 2 else [1, 0, 1, 1], dt=.01)
        state = self.worker.snapshot()
        self.assertEqual(len(state['transition_trace']), self.worker.trace_limit)
        self.assertNotIn(self.owner, json.dumps(state))
        self.assertNotIn('SOFTWARE_FIXTURE_MOTOR_SESSION', json.dumps(state))
        state['transition_trace'][0]['values'][0] = 99
        state['last_visible_values'][0] = 99
        fresh = self.worker.snapshot()
        self.assertNotEqual(fresh['transition_trace'][0]['values'][0], 99)
        self.assertNotEqual(fresh['last_visible_values'][0], 99)
        self.feed.item['value'] = [True, 1, 1, 1]  # Parser fault, not a legitimate unknown hold.
        self.worker.tick()
        failure = self.worker.snapshot()['last_failure']
        altered = self.worker.snapshot(); altered['last_failure']['values'][0] = 99
        self.assertEqual(self.worker.snapshot()['last_failure'], failure)

    def test_scheduler_keeps_20ms_period_subtracts_work_and_never_bursts(self):
        delays = []
        class Exit:
            def wait(self, delay):
                delays.append(delay); return len(delays) == 3
            def set(self): pass
        original_exit = self.worker._exit; self.worker._exit = Exit()
        try:
            with patch.object(self.worker, 'tick'), patch('basic_line_follower.time.monotonic',
                                                         side_effect=[10, 10.005, 20, 20.09]):
                self.worker._run()
        finally: self.worker._exit = original_exit
        self.assertAlmostEqual(delays[0], .02)
        self.assertAlmostEqual(delays[1], .015)
        self.assertEqual(delays[2], .001)

    def test_real_stop_shapes_now_hold_actual_pair_with_sample_timing_diagnostics(self):
        # Recorded pattern pairs and relative gaps, with synthetic fresh Feed
        # metadata. Not a claim to replay every raw frame from the real trials.
        for visible, unknown, dt, expected in ((CENTER, WHITE, .013, (84, 84)),
                                               ([1, 0, 0, 0], [0, 0, 0, 0], .384, (100, -100)),
                                               ([1, 1, 0, 1], WHITE, .101, (84, 0))):
            self.reset(first=visible)
            state = self.step(unknown, dt=dt)
            self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
            self.assertEqual(self.pair(state), expected)
            self.assertEqual(state['hold_pattern'], 'white' if unknown == WHITE else 'both_outer')
            self.assertEqual(state['last_visible_values'], visible)
            self.assertEqual(state['period_s'], .02)
            self.assertEqual(state['sample_monotonic'], self.feed.item['monotonic'])
            self.assertEqual(state['sample_count'], self.feed.item['successes'])
            self.assertEqual(state['transition_trace'][-1]['sample_count'], state['sample_count'])
            self.assertEqual(state['transition_trace'][-1]['sample_monotonic'], state['sample_monotonic'])
            cache = self.step(dt=.01, fresh=False)
            self.assertEqual(cache['sample_monotonic'], state['sample_monotonic'])
            self.assertEqual(cache['sample_count'], state['sample_count'])
            self.assertEqual(self.pair(cache), expected)

    def test_white_both_outer_switches_and_caches_share_one_unknown_budget(self):
        inner = [1, 0, 1, 1]
        self.reset(first=inner); self.step(WHITE)
        started = self.worker._line_gap_started
        for unknown in ([0, 0, 0, 0], WHITE, [0, 0, 1, 0], WHITE):
            state = self.step(unknown, dt=.3)
            self.assertEqual(self.worker._line_gap_started, started)
            self.assertEqual(state['last_visible_values'], inner)
            self.assertEqual(self.pair(state), (0, 84)); self.assertIsNone(state['error'])
            self.assertTrue(self.step(dt=.01, fresh=False)['running'])
        drives = len(self.motor.drives)
        state = self.step([0, 1, 1, 0], dt=.161)
        self.assertEqual(state['reason'], 'line_gap_timeout')
        self.assertGreaterEqual(state['line_gap_elapsed_s'], 1.4)
        self.assertEqual(self.worker._line_gap_started, started)
        self.assertEqual(len(self.motor.drives), drives)
        self.assert_stopped(state)


class WireMappingTests(FollowerFixture, unittest.TestCase):
    mapping = None  # Omit override: actual production default raw[3,2,1,0].

    def test_default_wire_raw_and_split_masks_drive_current_physical_side(self):
        for raw, physical, pair in (([0, 0, 0, 1], [1, 0, 0, 0], (100, -100)),
                                    ([1, 0, 0, 0], [0, 0, 0, 1], (-100, 100)),
                                    ([0, 1, 0, 1], [1, 0, 1, 0], (100, -100)),
                                    ([1, 0, 1, 0], [0, 1, 0, 1], (-100, 100))):
            state = self.reset(first=raw)
            self.assertEqual(self.pair(state), pair)
            self.assertEqual(state['last_raw_values'], raw)
            self.assertEqual(state['last_values'], physical)
            self.assertEqual(state['channel_order'], ['X4', 'X3', 'X2', 'X1'])
            self.assertEqual(state['raw_channel_order'], ['X1', 'X2', 'X3', 'X4'])
            self.assertEqual(state['raw_physical_pins'], [15, 18, 16, 11])
            self.assertEqual(state['transition_trace'][-1]['raw_values'], raw)

    def test_single_black_raw_channels_have_measured_right_to_left_order(self):
        expected = ((100, -100), (84, 0), (0, 84), (-100, 100))
        for index in range(4):
            raw = [1, 1, 1, 1]; raw[index] = 0
            state = self.reset(first=raw)
            self.assertEqual(state['last_values'].index(0), 3 - index)
            self.assertEqual(self.pair(state), expected[index])

    def test_all24_permutations_apply_once_without_source_or_pin_mutation(self):
        raw = [0, 1, 1, 1]
        for permutation in itertools.permutations(range(4)):
            state = self.reset(first=raw, mapping=permutation)
            physical = [raw[index] for index in permutation]
            self.assertEqual(state['last_values'], physical)
            self.assertEqual(self.pair(state), steering(physical, 84)[:2])
            self.assertEqual(state['channel_order'], ['X' + str(index + 1) for index in permutation])
            self.assertEqual(self.feed.reads, 1)
            self.assertEqual(self.feed.item['value'], raw)
            self.assertEqual(self.feed.item['physical_pins'], [15, 18, 16, 11])

    def test_permutation_is_strict_exact_int_bijection_with_no_constructor_activity(self):
        for permutation in (None, '3210', b'3210', range(4), set(range(4)), {}, (),
                            [3, 2, 1], [3, 2, 1, 0, 4], [3, 2, 1, 1], [4, 2, 1, 0],
                            [-1, 2, 1, 0], [True, 1, 2, 3], [3., 2, 1, 0], [3, 2, 1, []]):
            clock, motor = Clock(), Motor(); feed = Feed(clock)
            with self.assertRaisesRegex(ValueError, 'invalid_channel_permutation'):
                BasicLineFollower(motor, feed, clock=clock, threaded=False, channel_permutation=permutation)
            self.assertEqual(motor.calls, []); self.assertEqual(feed.reads, 0)

    def test_mutable_mapping_and_diagnostic_copies_cannot_change_runtime_order(self):
        permutation = [3, 2, 1, 0]
        self.reset(mapping=permutation); permutation[:] = [0, 1, 2, 3]
        state = self.step([0, 0, 0, 1])
        state['channel_permutation'][:] = [0, 1, 2, 3]
        state['last_raw_values'][0] = 99
        state['last_values'][0] = 99
        state['transition_trace'][-1]['raw_values'][0] = 99
        fresh = self.worker.snapshot()
        self.assertEqual(fresh['channel_permutation'], [3, 2, 1, 0])
        self.assertEqual(fresh['last_raw_values'], [0, 0, 0, 1])
        self.assertEqual(fresh['last_values'], [1, 0, 0, 0])
        self.assertEqual(fresh['transition_trace'][-1]['raw_values'], [0, 0, 0, 1])
        self.assertEqual(self.pair(self.step(fresh=False)), (100, -100))

    def test_asymmetric_cache_and_same_metadata_raw_change_use_independent_raw_baseline(self):
        self.step([0, 0, 0, 1]); mono = self.worker._sample_mono
        self.assertTrue(self.step(fresh=False)['running'])
        self.assertEqual(self.worker._sample_mono, mono)
        self.assertEqual(self.worker._raw_values, (0, 0, 0, 1))
        self.assertEqual(self.worker._values, (1, 0, 0, 0))
        self.feed.item['value'] = [1, 0, 0, 0]
        state = self.step(fresh=False)
        self.assertEqual(state['reason'], 'inconsistent_line_replay')
        self.assertEqual(state['last_raw_values'], [0, 0, 0, 1])
        self.assert_stopped(state)

    def test_mapping_does_not_modify_a_shared_source_dict(self):
        self.clock.now += .05; self.feed.publish([0, 0, 0, 1])
        reading = self.feed(); before = copy.deepcopy(reading)
        self.worker.line_source = lambda: reading
        self.worker.tick()
        self.assertEqual(reading, before)
        self.assertEqual(self.worker.snapshot()['last_values'], [1, 0, 0, 0])

    def test_production_raw_inner_actual_pair_is_retained_without_hard_upgrade(self):
        for raw, expected in (([1, 0, 1, 1], (84, 0)), ([1, 1, 0, 1], (0, 84))):
            self.reset()
            for _ in range(5): self.step(raw)
            state = self.step(WHITE)
            self.assertTrue(state['running'])
            self.assertEqual(self.pair(state), expected)
            self.assertIs(state['direction_recovery_enabled'], False)

    def test_explicit_identity_is_only_a_logical_fixture_override(self):
        state = self.reset(first=[0, 0, 0, 1], mapping=(0, 1, 2, 3))
        self.assertEqual(self.pair(state), (-100, 100))
        self.assertEqual(state['last_values'], state['last_raw_values'])
        self.assertEqual(state['channel_order'], ['X1', 'X2', 'X3', 'X4'])


if __name__ == '__main__':
    unittest.main()
