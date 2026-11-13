"""Failure-pattern regressions using synthetic GPIO and a fake UART only.

These verify command continuity; they do not predict the new physical trajectory.
"""
import itertools
import unittest

from basic_line_follower import BasicLineFollower, steering
from motor_control import MotorControl
from test_basic_line_follower import Clock, Feed
from test_basic_line_integration import Wire

CENTER = [1, 0, 0, 1]
WHITE = [1, 1, 1, 1]
LEFT = [0, 1, 1, 1]
RIGHT = [1, 1, 1, 0]


class DrivingRegressions(unittest.TestCase):
    def setUp(self):
        self.clock, self.wire = Clock(), Wire()
        self.motor = MotorControl(self.wire, clock=self.clock, mode='speed', startup_boost=True)
        self.feed = Feed(self.clock)
        self.follower = BasicLineFollower(self.motor, self.feed, clock=self.clock,
                                          threaded=False, channel_permutation=(0, 1, 2, 3))
        self.addCleanup(self.motor.close)
        self.addCleanup(self.follower.close)
        self.owner = self.follower.start(20)
        self.seq = 0
        self.follower.heartbeat(self.owner, self.seq)
        self.follower.tick()

    def step(self, values, dt=.03, fresh=True):
        self.clock.now += dt
        if fresh:
            self.feed.publish(values)
        self.seq += 1
        self.follower.heartbeat(self.owner, self.seq)
        return self.follower.tick()

    def outputs(self):
        return self.motor.snapshot()['outputs']

    def test_minor_steering_after_start_never_reboosts_or_clears_the_driver(self):
        self.step(CENTER, .21)
        before = len(self.wire.packets)
        for _ in range(30):
            for values, expected in (([1,0,1,1],[12,12,20,20]),
                                     (CENTER,[20]*4),([1,1,0,1],[20,20,12,12])):
                self.step(values)
                self.assertEqual(self.outputs(), expected)
                self.assertFalse(self.motor.snapshot()['startup_boost_active'])
        self.assertEqual(self.motor.snapshot()['speed_transition_resets'], 0)
        self.assertTrue(all(packet.startswith(b'$spd:') for packet in self.wire.packets[before:]))
        self.assertTrue(all(0 < value <= 20 for value in self.outputs()))

    def test_initial_assist_keeps_differential_ratio_and_original_deadline(self):
        until = self.motor._boost_until
        self.step([1,0,1,1], .05)
        self.assertEqual(self.outputs(), [120,120,200,200])
        self.assertEqual(self.motor._boost_until, until)
        self.step([1,1,0,1], .05)
        self.assertEqual(self.outputs(), [200,200,120,120])
        self.assertEqual(self.motor._boost_until, until)
        self.step([1,1,0,1], .101)
        self.assertEqual(self.outputs(), [20,20,12,12])
        self.assertIsNone(self.motor._boost_until)

    def test_mirrored_turns_keep_direction_through_split_and_opposite_samples(self):
        for outer, pair in ((LEFT,[-20,-20,20,20]),(RIGHT,[20,20,-20,-20])):
            self.follower.stop()
            self.owner = self.follower.start(20)
            self.seq = 0
            self.step(outer)
            self.step(outer, .21)
            for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0],
                           LEFT,RIGHT,[1,0,1,1],[1,1,0,1],CENTER,outer):
                state = self.step(values)
                self.assertEqual(self.outputs(), pair)
                self.assertTrue(state['running'])
            self.step(CENTER)
            self.assertEqual(self.outputs(), pair)
            self.step(CENTER)
            self.assertEqual(self.motor.snapshot()['requested_outputs'], [20]*4)
            self.assertEqual(self.follower.snapshot()['turn_direction'], 0)

    def test_cached_center_cannot_finish_a_turn(self):
        self.step(RIGHT)
        state = self.step(CENTER)
        self.assertEqual(state['center_samples'], 1)
        for _ in range(5):
            state = self.step(CENTER, fresh=False)
            self.assertEqual(state['center_samples'], 1)
            self.assertEqual(state['turn_direction'], 1)
            self.assertEqual(self.motor.snapshot()['requested_outputs'],[20,20,-20,-20])
        self.step(CENTER)
        self.assertEqual(self.follower.snapshot()['turn_direction'], 0)

    def test_white_breaks_center_confirmation_without_choosing_a_new_direction(self):
        self.step(LEFT)
        self.step(CENTER)
        state = self.step(WHITE)
        self.assertEqual(state['center_samples'], 0)
        self.assertEqual(state['line_gap_limit_s'], .4)
        state = self.step(CENTER)
        self.assertEqual(state['turn_direction'], -1)
        self.step(CENTER)
        self.assertEqual(self.follower.snapshot()['turn_direction'], 0)

    def test_observed_isolated_centers_no_longer_insert_forward_boosts_mid_turn(self):
        # 13:36:30 failure class: right pivot -> one CENTER -> right/broad again.
        self.step(CENTER, .21)
        self.step(RIGHT)
        resets = self.motor.speed_transition_resets
        for _ in range(15):
            for values in (RIGHT,CENTER,[1,0,1,1],[0,0,0,0],RIGHT):
                self.step(values)
                self.assertEqual(self.motor.snapshot()['requested_outputs'], [20,20,-20,-20])
                self.assertEqual(self.motor.speed_transition_resets, resets)
        self.assertFalse(self.motor.snapshot()['startup_boost_active'])

    def test_turn_can_stop_and_restart_without_old_confirmation_or_direction(self):
        self.step(LEFT)
        self.step(CENTER)
        self.follower.stop()
        self.assertEqual(self.outputs(), [0]*4)
        self.owner = self.follower.start(20)
        self.seq = 0
        state = self.step(CENTER)
        self.assertEqual(state['turn_direction'], 0)
        self.assertEqual(state['center_samples'], 0)
        self.assertEqual(self.motor.snapshot()['requested_outputs'], [20]*4)

    def test_symmetric_broad_black_keeps_weak_pair_instead_of_forcing_right(self):
        self.step(CENTER, .21)
        self.step([1,0,1,1])
        for values in ([0,0,0,0],[0,1,1,0])*20:
            state = self.step(values)
            self.assertEqual(self.outputs(), [12,12,20,20])
            self.assertEqual(state['state'], 'left')
            self.assertEqual(state['turn_direction'], 0)
            self.assertFalse(state['line_gap_active'])

    def test_all_visible_rules_are_mirror_symmetric_and_white_is_explicit(self):
        for values in itertools.product((0,1), repeat=4):
            if values == tuple(WHITE):
                with self.assertRaisesRegex(ValueError,'line_lost'):steering(values,20)
                continue
            left,right,_,error = steering(values,20)
            rleft,rright,_,rerror = steering(values[::-1],20)
            self.assertEqual((left,right),(rright,rleft))
            self.assertEqual(error,-rerror)
            self.assertTrue(-20 <= left <= 20 and -20 <= right <= 20)
            self.assertTrue(left or right)

    def test_true_reversal_keeps_clear_and_200_startup_improvement(self):
        self.step(CENTER, .21)
        before=len(self.wire.packets)
        self.step(LEFT)
        self.assertEqual(self.wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:-200,-200,200,200#'])
        until=self.motor._boost_until
        self.step([0,0,0,0], .1)
        self.assertEqual(self.motor._boost_until,until)
        self.step(LEFT,.101)
        self.assertEqual(self.outputs(),[-20,-20,20,20])
        self.follower.stop()
        self.assertEqual(self.outputs(),[0]*4)
        count=len(self.wire.packets)
        self.clock.now+=.3
        self.motor.check_deadline()
        self.assertEqual(len(self.wire.packets),count)

    def test_no_corner_or_pid_parameters_are_required_from_the_operator(self):
        state=self.follower.snapshot()
        self.assertEqual(state['channel_permutation'],[0,1,2,3])
        self.assertEqual(state['center_confirmation_samples'],2)
        self.assertFalse(state['station_detection'])
        self.assertEqual(self.motor.snapshot()['command_mode'],'speed')
        self.assertEqual(state['motion_feedback'],'unverified')

    def test_all_three_gears_have_scaled_inner_and_pivot_outputs(self):
        for speed,inner in ((10,6),(20,12),(40,24)):
            self.assertEqual(steering([1,0,1,1],speed)[:2],(inner,speed))
            self.assertEqual(steering([1,1,0,1],speed)[:2],(speed,inner))
            self.assertEqual(steering(RIGHT,speed)[:2],(speed,-speed))
            self.assertEqual(steering(LEFT,speed)[:2],(-speed,speed))
        self.assertEqual(steering(CENTER,220)[:2],(40,40))

    def test_runtime_gear_changes_keep_owner_and_never_reboost_forward(self):
        self.step(CENTER,.21)
        owner=self.follower._motor_session
        before=len(self.wire.packets)
        for speed in (10,40,20,10):
            self.seq+=1
            self.follower.heartbeat(self.owner,self.seq,speed=speed)
            state=self.follower.tick()
            self.assertEqual(state['speed'],speed)
            self.assertEqual(self.outputs(),[speed]*4)
            self.assertEqual(self.follower._motor_session,owner)
            self.assertFalse(self.motor.snapshot()['startup_boost_active'])
        self.assertTrue(all(x.startswith(b'$spd:') for x in self.wire.packets[before:]))
        changes=[e for e in self.follower.snapshot()['transition_trace'] if e['event']=='speed_change']
        self.assertEqual([e['speed'] for e in changes],[10,40,20,10])

    def test_runtime_gear_applies_to_white_hold_without_resetting_gap(self):
        self.step(RIGHT);self.step(RIGHT,.21);self.step(WHITE)
        anchor=self.follower._line_gap_started
        for speed in (10,40,20):
            self.seq+=1
            self.follower.heartbeat(self.owner,self.seq,speed=speed)
            state=self.follower.tick()
            self.assertEqual(self.outputs(),[speed,speed,-speed,-speed])
            self.assertEqual(self.follower._line_gap_started,anchor)
            self.assertEqual(state['line_gap_limit_s'],.4)
            self.assertEqual(state['turn_direction'],1)
        self.step(WHITE,.401)
        self.assertEqual(self.outputs(),[0]*4)
        self.assertEqual(self.follower.snapshot()['reason'],'line_gap_timeout')

    def test_runtime_gear_keeps_turn_and_one_center_confirmation(self):
        self.step(LEFT);self.step(LEFT,.21);self.step(CENTER)
        self.seq+=1
        self.follower.heartbeat(self.owner,self.seq,speed=10)
        state=self.follower.tick()
        self.assertEqual(state['center_samples'],1)
        self.assertEqual(state['turn_direction'],-1)
        self.assertEqual(self.outputs(),[-10,-10,10,10])
        state=self.step(CENTER)
        self.assertEqual(state['turn_direction'],0)
        self.assertEqual(self.motor.snapshot()['requested_outputs'],[10]*4)

    def test_invalid_gear_or_owner_never_updates_speed_or_heartbeat(self):
        old_time=self.follower._owner_time;old_seq=self.follower._owner_seq
        for speed in (0,9,11,30,41,True,10.0,'10'):
            with self.assertRaisesRegex(ValueError,'invalid_speed'):
                self.follower.heartbeat(self.owner,self.seq+1,speed=speed)
            self.assertEqual(self.follower.snapshot()['speed'],20)
            self.assertEqual(self.follower._owner_time,old_time)
            self.assertEqual(self.follower._owner_seq,old_seq)
        with self.assertRaises(ValueError):self.follower.heartbeat('another-page',self.seq+1,speed=40)
        with self.assertRaises(ValueError):self.follower.heartbeat(self.owner,self.seq,speed=40)
        self.assertEqual(self.follower.snapshot()['speed'],20)
