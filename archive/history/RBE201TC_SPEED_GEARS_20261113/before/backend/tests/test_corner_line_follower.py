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
UNKNOWN = [1, 1, 1, 1]  # Only all-WHITE needs the existing gap policy.
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
        if name in ('gap', 'reacquired'): self.step(UNKNOWN)
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
        self.assertEqual(state['control_policy'], 'four_digital_continuous_turn_v1')
        self.assertEqual(state['max_correction_speed'], 20)
        self.assertEqual(state['line_loss_limit_s'], 1.4)
        self.assertIs(state['direction_recovery_enabled'], False)
        self.assertFalse(state['line_gap_active'])
        self.assertEqual(state['last_visible_values'], CENTER)
        self.assertFalse(any(key.startswith('corner_') for key in state))
        self.assertFalse(hasattr(self.worker, '_corner_active'))
        self.assertFalse(hasattr(self.worker, '_corner_gate'))

    def test_inner_samples_correct_immediately_but_active_pivot_keeps_direction(self):
        for values,expected in (([1,0,1,1],(12,20)),([1,1,0,1],(20,12)),(CENTER,(20,20))):
            self.assertEqual(self.pair(self.step(values)),expected)
        self.step(RIGHT)
        for values in (LEFT,[1,0,1,1],CENTER,RIGHT,[0,0,0,0]):
            state=self.step(values);self.assertEqual(self.pair(state),(20,-20))
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.motor.calls,[{'action':'arm'}])
        self.assertEqual([seq for seq,_,_ in self.motor.drives],list(range(len(self.motor.drives))))

    def test_real_v4_pattern_order_now_corrects_left_and_two_center_samples_finish_turn(self):
        # Observed physical pattern order, replayed at synthetic 50ms intervals;
        # NOT an assertion that these are all original raw acquisition frames.
        expected = (([1, 1, 0, 0], (20, -20)), ([1, 0, 1, 1], (20, -20)),
                    (CENTER, (20, -20)), (CENTER, (20, 20)))
        for values, pair in expected:
            self.assertEqual(self.pair(self.step(values)), pair)
        held=self.step(WHITE)
        self.assertTrue(held['running']);self.assertEqual(self.pair(held),(20,20))
        self.assertEqual(held['line_gap_limit_s'],.2)

    def test_fresh_visible_correction_can_run_far_longer_than_old_1point4_seconds(self):
        for index in range(100):
            values = RIGHT if index % 2 else LEFT
            state = self.step(values)
            self.assertTrue(state['running'])
            self.assertEqual(self.pair(state), (-20, 20))
        self.assertFalse(state['line_gap_active'])
        self.assertEqual(state['line_gap_elapsed_s'], 0)

    def test_single_outer_at_start_needs_no_center_arm_or_inner_departure(self):
        for outer, expected in ((LEFT, (-20, 20)), (RIGHT, (20, -20))):
            state = self.reset(first=outer)
            self.assertEqual(self.pair(state), expected)
            self.assertTrue(state['hold_authorized'])
            self.assertEqual(self.pair(self.step(UNKNOWN)), expected)

    def test_outer_and_split_start_either_side_then_ignore_opposing_transient(self):
        for values,expected in (([0,0,0,1],(-20,20)),([0,1,0,1],(-20,20)),
                                ([1,0,1,0],(20,-20)),([1,0,0,0],(20,-20))):
            self.reset();self.assertEqual(self.pair(self.step(values)),expected)
            self.assertEqual(self.pair(self.step(CENTER,dt=.001)),expected)
            self.assertEqual(self.pair(self.step(CENTER,dt=.02)),(20,20))

    def test_weak_actual_pair_is_retained_without_becoming_a_hard_direction_guess(self):
        for inner, expected in (([1, 0, 1, 1], (12, 20)), ([1, 1, 0, 1], (20, 12))):
            self.reset()
            for _ in range(5): self.step(inner)
            count = len(self.motor.drives)
            state = self.step(UNKNOWN)
            self.assertTrue(state['running']); self.assertTrue(state['hold_authorized'])
            self.assertEqual(self.pair(state), expected)
            self.assertEqual(len(self.motor.drives), count + 1)

    def test_gap_holds_last_actually_sent_inner_pair_not_earlier_outer_spin(self):
        for outer, inner, expected in ((RIGHT, [1, 0, 1, 1], (12, 20)),
                                       (LEFT, [1, 1, 0, 1], (20, 12))):
            self.reset(); self.step(outer); self.step(CENTER); self.step(CENTER); self.step(inner)
            last_actual = self.motor.drives[-1][1:]
            self.assertEqual(last_actual, expected)
            state = self.step(UNKNOWN)
            self.assertTrue(state['line_gap_active'])
            self.assertEqual(self.pair(state), last_actual)
            self.assertEqual(state['last_visible_values'], inner)
            self.assertEqual(self.motor.drives[-1][1:], last_actual)

    def test_two_center_samples_clear_turn_after_gap_and_remains_actual_forward_hold_authority(self):
        self.step(RIGHT); self.step(UNKNOWN)
        state = self.step(CENTER, dt=.001)
        self.assertEqual(self.pair(state), (20, -20))
        state = self.step(CENTER)
        self.assertEqual(self.pair(state), (20, 20))
        self.assertFalse(state['line_gap_active']); self.assertTrue(state['hold_authorized'])
        self.assertEqual(state['line_gap_elapsed_s'], 0)
        held=self.step(WHITE)
        self.assertTrue(held['running']);self.assertEqual(self.pair(held),(20,20))
        self.assertEqual(held['line_gap_limit_s'],.2)

    def test_reacquired_opposite_inner_clears_gap_without_reversing_active_turn(self):
        self.step(RIGHT); self.step(UNKNOWN)
        state = self.step([1, 0, 1, 1])
        self.assertEqual(self.pair(state), (20, -20))
        self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.pair(self.step(UNKNOWN)), (20, -20))

    def test_fresh_white_and_cache_never_refresh_first_gap_timestamp(self):
        self.step(RIGHT); first = self.step(UNKNOWN)
        started = self.worker._line_gap_started
        for fresh in (False, True, False, True):
            state = self.step(UNKNOWN, dt=.05, fresh=fresh)
            self.assertEqual(self.worker._line_gap_started, started)
            self.assertGreater(state['line_gap_elapsed_s'], first['line_gap_elapsed_s'])
        self.assertEqual(self.pair(state), (20, -20))

    def test_gap_clock_is_first_white_sample_mono_not_late_read_or_latest_white(self):
        self.step(RIGHT)
        self.clock.now += .3; self.feed.publish(UNKNOWN)
        self.feed.item['monotonic'] -= .2
        state = self.worker.tick()
        self.assertAlmostEqual(state['line_gap_elapsed_s'], .2)
        self.assertEqual(self.worker._line_gap_started, self.feed.item['monotonic'])

    def test_continuous_fresh_white_hits_point4_limit_and_cannot_resume(self):
        for outer in (LEFT, RIGHT):
            self.reset(); self.step(outer); self.step(UNKNOWN)
            for _ in range(3): self.assertTrue(self.step(UNKNOWN, dt=.1)['running'])
            count = len(self.motor.drives)
            state = self.step(UNKNOWN, dt=.101)
            self.assertEqual(state['reason'], 'line_gap_timeout')
            self.assertGreaterEqual(state['line_gap_elapsed_s'], .4)
            self.assertEqual(len(self.motor.drives), count)
            self.assert_stopped(state)

    def test_any_fresh_visible_line_resets_only_continuous_gap_not_total_follow_time(self):
        self.step(RIGHT); self.step(UNKNOWN)
        first = self.worker._line_gap_started
        for _ in range(3): self.step(UNKNOWN, dt=.08)
        state = self.step([1, 0, 1, 1], dt=.04)
        self.assertEqual(self.pair(state), (20, -20)); self.assertFalse(state['line_gap_active'])
        state = self.step(UNKNOWN)
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

    def test_broad_readings_clear_gap_and_respect_active_direction(self):
        for previous,pair in ((LEFT,(-20,20)),(RIGHT,(20,-20))):
            for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
                self.reset(first=previous);self.step(WHITE);state=self.step(values)
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertIsNone(state['hold_pattern']);self.assertIsNone(self.worker._line_gap_started)
                self.assertEqual(state['last_visible_values'],values);self.assertEqual(self.pair(state),pair)

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
        self.assertEqual(self.step(UNKNOWN)['reason'], 'line_lost')

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

    def test_slow_white_write_cannot_move_first_gap_timeout_anchor(self):
        self.stage('gap');self.step(WHITE,dt=.25)
        started=self.worker._line_gap_started;original=self.motor.drive_sides
        def delayed(*args):
            self.clock.now+=.15;return original(*args)
        self.motor.drive_sides=delayed
        state=self.step(WHITE,dt=.01)
        self.assertEqual(state['reason'],'line_gap_timeout')
        self.assertEqual(self.worker._line_gap_started,started);self.assert_stopped(state)

    def test_close_at_each_stage_preserves_observation_but_revokes_motion(self):
        for stage in STAGES:
            self.stage(stage); before = self.worker.snapshot()['last_values']
            self.worker.close(); state = self.worker.snapshot()
            self.assertEqual(state['state'], 'closed')
            self.assertEqual(state['last_values'], before)
            self.assertFalse(state['ready']); self.assert_stopped(state)

    def test_new_start_discards_white_hold_authority_but_preserves_fault_diagnostics(self):
        self.reset();self.step(RIGHT);self.step(WHITE);self.clock.now+=.401
        failure=self.worker.tick()['last_failure'];self.assertEqual(failure['reason'],'stale_line_sample')
        self.worker.stop();self.owner=self.worker.start(speed=84);self.seq=0;self.worker.heartbeat(self.owner,0)
        state=self.worker.snapshot();self.assertEqual(state['last_failure'],failure)
        self.assertEqual(state['last_values'],WHITE);self.assertEqual(state['last_visible_values'],RIGHT)
        self.assertFalse(state['hold_authorized']);self.assert_stopped(self.step(WHITE))

    def test_white_start_saves_raw_before_failure_and_all_other_masks_start(self):
        state=self.reset(first=WHITE)
        self.assertEqual(state['last_values'],WHITE);self.assertEqual(state['last_failure']['values'],WHITE)
        self.assert_stopped(state)
        for values in itertools.product((0,1),repeat=4):
            if values==tuple(WHITE):continue
            state=self.reset(first=values)
            self.assertTrue(state['running']);self.assertEqual(self.pair(state),steering(values,20)[:2])
            self.assertFalse(state['line_gap_active'])

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

    def test_broad_masks_have_contextual_actions_and_fresh_sample_diagnostics(self):
        for previous in (CENTER,LEFT,RIGHT,[1,1,0,1]):
            for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
                initial=self.reset(first=previous);state=self.step(values,dt=.013)
                expected=(self.pair(initial) if previous in (LEFT,RIGHT) or values in ([0,0,0,0],[0,1,1,0]) else steering(values,20)[:2])
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertEqual(self.pair(state),expected);self.assertEqual(state['last_visible_values'],values)
                for key,source in [('sample_monotonic','monotonic'),('sample_count','successes')]:
                    self.assertEqual(state[key],self.feed.item[source]);self.assertEqual(state['transition_trace'][-1][key],state[key])
                cache=self.step(dt=.01,fresh=False)
                self.assertEqual(cache['sample_count'],state['sample_count']);self.assertEqual(self.pair(cache),expected)

    def test_all_both_outer_masks_remain_visible_beyond_old_ambiguity_budget(self):
        self.reset(first=LEFT)
        for index in range(40):
            values=([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0])[index%4]
            state=self.step(values,dt=.05)
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.pair(state),(-20,20));self.assertEqual(state['last_visible_values'],values)
            self.assertTrue(self.step(dt=.005,fresh=False)['running'])


class WireMappingTests(FollowerFixture, unittest.TestCase):
    mapping = None  # Live physical-leftmost black is raw X4; physical order X4/X3/X2/X1.

    def test_default_wire_raw_and_split_masks_drive_current_physical_side(self):
        for raw, physical, pair in (([0, 0, 0, 1], [1, 0, 0, 0], (20, -20)),
                                    ([1, 0, 0, 0], [0, 0, 0, 1], (-20, 20)),
                                    ([0, 1, 0, 1], [1, 0, 1, 0], (20, -20)),
                                    ([1, 0, 1, 0], [0, 1, 0, 1], (-20, 20))):
            state = self.reset(first=raw)
            self.assertEqual(self.pair(state), pair)
            self.assertEqual(state['last_raw_values'], raw)
            self.assertEqual(state['last_values'], physical)
            self.assertEqual(state['channel_order'], ['X4', 'X3', 'X2', 'X1'])
            self.assertEqual(state['raw_channel_order'], ['X1', 'X2', 'X3', 'X4'])
            self.assertEqual(state['raw_physical_pins'], [15, 18, 16, 11])
            self.assertEqual(state['transition_trace'][-1]['raw_values'], raw)

    def test_single_black_raw_channels_have_physical_leftmost_x4_order(self):
        expected = ((20, -20), (20, 12), (12, 20), (-20, 20))
        for index in range(4):
            raw = [1, 1, 1, 1]; raw[index] = 0
            state = self.reset(first=raw)
            self.assertEqual(state['last_values'].index(0), 3-index)
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
        self.assertEqual(self.pair(self.step(fresh=False)), (20, -20))

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
        for raw, expected in (([1, 0, 1, 1], (20, 12)), ([1, 1, 0, 1], (12, 20))):
            self.reset()
            for _ in range(5): self.step(raw)
            state = self.step(UNKNOWN)
            self.assertTrue(state['running'])
            self.assertEqual(self.pair(state), expected)
            self.assertIs(state['direction_recovery_enabled'], False)

    def test_explicit_identity_is_a_logical_policy_fixture(self):
        state = self.reset(first=[0, 0, 0, 1], mapping=(0, 1, 2, 3))
        self.assertEqual(self.pair(state), (-20, 20))
        self.assertEqual(state['last_values'], state['last_raw_values'])
        self.assertEqual(state['channel_order'], ['X1', 'X2', 'X3', 'X4'])


if __name__ == '__main__':
    unittest.main()


class WhiteGapTests(FollowerFixture, unittest.TestCase):
    def test_white_after_every_visible_pair_uses_existing_point2_or_point4_limit(self):
        for visible in itertools.product((0,1),repeat=4):
            try:expected=steering(visible,20)[:2]
            except ValueError:continue
            with self.subTest(visible=visible):
                self.reset(first=visible);state=self.step(WHITE,dt=.03)
                self.assertEqual(state['last_values'],WHITE);self.assertEqual(state['last_raw_values'],WHITE)
                self.assertTrue(state['running']);self.assertEqual(self.pair(state),expected)
                limit=.4 if expected[0]*expected[1]<0 else .2
                self.assertEqual(state['line_gap_limit_s'],limit)
                state=self.step(WHITE,dt=limit+.001)
                self.assertEqual(state['reason'],'line_gap_timeout');self.assert_stopped(state)

    def test_observed_center_to_white_survives_first_frame_and_reacquires_every_black_mask(self):
        for visible in itertools.product((0,1),repeat=4):
            if visible==tuple(WHITE):continue
            with self.subTest(reacquired=visible):
                self.reset(first=CENTER)
                state=self.step(WHITE,dt=.680)  # Observed previous-center to first-white interval.
                self.assertTrue(state['running']);self.assertEqual(self.pair(state),(20,20))
                anchor=self.worker._line_gap_started
                state=self.step(WHITE,dt=.10)
                self.assertEqual(self.worker._line_gap_started,anchor)
                self.assertEqual(self.pair(state),(20,20))
                state=self.step(visible,dt=.05)
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertEqual(self.pair(state),steering(visible,20)[:2])

    def test_forward_white_cache_keeps_original_deadline_and_explicit_stop_interrupts(self):
        self.reset(first=CENTER);self.step(WHITE,dt=.03)
        anchor=self.worker._line_gap_started
        state=self.step(dt=.199,fresh=False)
        self.assertTrue(state['running']);self.assertEqual(self.worker._line_gap_started,anchor)
        self.assert_stopped(self.step(dt=.002,fresh=False))
        self.reset(first=CENTER);self.step(WHITE);self.assert_stopped(self.worker.stop())

    def test_latest_corner_inner_right_point036_then_white_keeps_exact_pair_and_reacquires(self):
        for values in ([1,0,1,1],CENTER,[1,0,1,1],CENTER,[1,1,0,1]):
            self.assertTrue(self.step(values)['running'])
        before=len(self.motor.drives);state=self.step(WHITE,dt=.036)
        self.assertTrue(state['running']);self.assertEqual(self.pair(state),(20,12))
        self.assertEqual(self.motor.drives[-1][1:],(20,12));self.assertEqual(len(self.motor.drives),before+1)
        self.assertFalse(state['direction_recovery_enabled'])
        state=self.step(CENTER,dt=.08)
        self.assertEqual(self.pair(state),(20,20));self.assertFalse(state['line_gap_active'])
        self.assertEqual(state['line_gap_limit_s'],1.4)
        held=self.step(WHITE)
        self.assertTrue(held['running']);self.assertEqual(self.pair(held),(20,20))
        self.assertEqual(held['line_gap_limit_s'],.2)

    def test_white_at_start_never_drives_and_next_start_cannot_reuse_old_direction(self):
        self.assert_stopped(self.reset(first=WHITE))
        self.assertEqual(self.motor.drives, [])
        self.reset(first=RIGHT); self.step(WHITE); self.worker.stop()
        self.owner = self.worker.start(speed=20); self.seq = 0
        self.worker.heartbeat(self.owner, 0)
        before = len(self.motor.drives)
        state = self.worker.tick()
        self.assertEqual(state['reason'], 'line_lost')
        self.assertEqual(len(self.motor.drives), before)
        self.assert_stopped(state)


    def test_repeated_white_and_cache_never_extends_short_gap_budget(self):
        self.reset(first=[1,0,1,1]);state=self.step(WHITE,dt=.03)
        anchor=self.worker._line_gap_started
        self.assertEqual(self.pair(state),(12,20))
        state=self.step(UNKNOWN,dt=.08)
        self.assertTrue(state['running']);self.assertEqual(state['line_gap_limit_s'],.2)
        state=self.step(dt=.04,fresh=False)
        self.assertEqual(self.worker._line_gap_started,anchor)
        self.assertEqual(state['line_gap_limit_s'],.2)
        state=self.step(UNKNOWN,dt=.081)
        self.assertEqual(state['reason'],'line_gap_timeout');self.assert_stopped(state)

    def test_white_hold_remains_interruptible_by_explicit_stop(self):
        self.reset(first=[1,1,0,1]);self.step(WHITE,dt=.036)
        state=self.worker.stop();count=len(self.motor.drives)
        self.assert_stopped(state)
        self.clock.now+=.3;self.worker.tick();self.assertEqual(len(self.motor.drives),count)

    def test_visible_opposite_inner_immediately_overrides_held_pair(self):
        self.reset(first=[1,1,0,1]);self.step(WHITE,dt=.036)
        state=self.step([1,0,1,1],dt=.01)
        self.assertEqual(self.pair(state),(12,20));self.assertFalse(state['line_gap_active'])
        self.assertEqual(state['line_gap_limit_s'],1.4)

    def test_slow_white_submission_cannot_bypass_short_budget(self):
        self.reset(first=[1,1,0,1]);original=self.motor.drive_sides
        def slow(*args):
            result=original(*args);self.clock.now+=.201;return result
        self.motor.drive_sides=slow
        state=self.step(WHITE,dt=.036)
        self.assertEqual(state['reason'],'line_gap_timeout');self.assert_stopped(state)


    def test_outer_pivot_keeps_direction_past_old_point2_then_stops_at_point4(self):
        for outer,pair in ((LEFT,(-20,20)),(RIGHT,(20,-20))):
            with self.subTest(outer=outer):
                self.reset(first=outer);state=self.step(WHITE,dt=.03)
                self.assertEqual(state['line_gap_limit_s'],.4)
                state=self.step(WHITE,dt=.25)
                self.assertTrue(state['running']);self.assertEqual(self.pair(state),pair)
                state=self.step(WHITE,dt=.151)
                self.assertEqual(state['reason'],'line_gap_timeout');self.assert_stopped(state)

    def test_two_fresh_centers_before_point4_finish_the_pivot(self):
        self.reset(first=RIGHT);self.step(WHITE,dt=.03)
        state=self.step(CENTER,dt=.30)
        self.assertTrue(state['running']);self.assertEqual(self.pair(state),(20,-20))
        state=self.step(CENTER,dt=.02)
        self.assertEqual(self.pair(state),(20,20))
        self.assertFalse(state['line_gap_active']);self.assertEqual(state['line_gap_limit_s'],1.4)
        held=self.step(WHITE)
        self.assertTrue(held['running']);self.assertEqual(self.pair(held),(20,20))
        self.assertEqual(held['line_gap_limit_s'],.2)

    def test_outer_pivot_budget_is_not_extended_by_white_or_cache_frames(self):
        self.reset(first=RIGHT);self.step(WHITE,dt=.03)
        anchor=self.worker._line_gap_started
        state=self.step(UNKNOWN,dt=.2)
        self.assertTrue(state['running']);self.assertEqual(state['line_gap_limit_s'],.4)
        state=self.step(dt=.05,fresh=False)
        self.assertEqual(self.worker._line_gap_started,anchor)
        state=self.step(UNKNOWN,dt=.151)
        self.assertEqual(state['reason'],'line_gap_timeout');self.assert_stopped(state)
