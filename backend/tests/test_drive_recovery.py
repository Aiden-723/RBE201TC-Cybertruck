"""Regressions for the active hard-20 controller, using a fake serial port."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from basic_line_follower import CENTER, WHITE
from imu_heading import ImuHeading
from motor_control import MotorControl, SerialPWM
from test_autonomous_motor_bridge import Wire
from test_basic_line_follower import Clock, Feed
from track_imu_follower import TrackImuFollower


class DriveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.feed = Feed(self.clock)
        self.imu = ImuHeading(clock=self.clock)
        self.imu.accept((0, 0, 0))
        self.imu.calibrate()
        self.wire = Wire()
        self.motion = MotorControl(self.wire, clock=self.clock, mode='speed', startup_boost=False)
        self.follower = TrackImuFollower(
            self.motion, self.feed, self.imu, {'turns': ['left']},
            clock=self.clock, threaded=False,
        )
        self.addCleanup(self.motion.close)
        self.addCleanup(self.follower.close)
        self.session = self.follower.start()
        self.seq = 0
        self.follower.heartbeat(self.session, self.seq)

    def tick(self, values=CENTER, yaw=0, dt=.03):
        self.clock.now += dt
        self.feed.publish(reversed(values))
        self.imu.accept((0, 0, yaw))
        self.seq += 1
        self.follower.heartbeat(self.session, self.seq)
        return self.follower.tick()

    def begin_left_turn(self, start_yaw=0):
        self.tick(yaw=start_yaw)
        self.tick(yaw=start_yaw)
        self.tick((0, 0, 0, 1), yaw=start_yaw)
        for _ in range(self.follower.white_confirmation_samples):
            state = self.tick(WHITE, yaw=start_yaw)
        self.assertEqual(state['phase'], 'turn')
        self.assertEqual(state['turn_entry_heading_deg'], start_yaw)
        self.assertEqual(self.motion.snapshot()['outputs'], [-20, -20, 20, 20])

    def test_ordinary_deviation_stops_forward_then_pivots_on_either_side(self):
        left_patterns = ((0, 0, 0, 1), (0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1))
        for turn_left in (True, False):
            self.tick(yaw=25)
            self.assertEqual(self.motion.snapshot()['outputs'], [20] * 4)
            patterns = left_patterns if turn_left else tuple(p[::-1] for p in left_patterns)
            pivot = [-20, -20, 20, 20] if turn_left else [20, 20, -20, -20]
            self.tick(patterns[0], yaw=50)
            self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)
            for pattern in patterns:
                self.tick(pattern, yaw=50)
                self.assertEqual(self.motion.snapshot()['outputs'], pivot)
        self.tick(yaw=50)
        self.assertEqual(self.motion.snapshot()['outputs'], [20] * 4)

    def test_return_to_center_clears_a_corner_before_white(self):
        self.tick()
        self.tick()
        self.assertTrue(self.tick((0, 0, 0, 1))['next_turn_ready'])
        self.assertTrue(self.tick((0, 0, 0, 0))['next_turn_ready'])
        self.assertFalse(self.tick()['next_turn_ready'])
        state = self.tick(WHITE)
        self.assertEqual(state['reason'], 'line_lost')
        self.assertEqual(state['turn_index'], 0)
        self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)

    def test_opposite_widening_and_lateral_exit_do_not_advance_the_route(self):
        self.tick()
        self.tick()
        self.assertFalse(self.tick((1, 0, 0, 0))['next_turn_ready'])
        self.tick()
        self.assertTrue(self.tick((0, 0, 0, 1))['next_turn_ready'])
        self.assertTrue(self.tick((0, 0, 0, 0))['next_turn_ready'])
        for pattern in ((1, 0, 0, 0), (1, 1, 0, 0), (1, 1, 1, 0)):
            self.assertFalse(self.tick(pattern)['next_turn_ready'])
        state = self.tick(WHITE)
        self.assertEqual(state['reason'], 'line_lost')
        self.assertEqual(state['turn_index'], 0)
        self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)

    def test_turn_uses_widening_history_even_without_the_planned_three_black_mask(self):
        self.tick()
        self.tick()
        # Recorded bend: the probe array crosses its full width immediately
        # after seeing only the outer edge on the opposite side.
        for pattern in ((1, 0, 0, 0), (1, 1, 0, 0), (1, 1, 1, 0)):
            self.assertFalse(self.tick(pattern)['next_turn_ready'])
        self.assertTrue(self.tick((0, 0, 0, 0))['next_turn_ready'])
        for count in (1, 2):
            state = self.tick(WHITE)
            self.assertEqual(state['phase'], 'follow')
            self.assertEqual(state['white_samples'], count)
            self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)
        state = self.tick(WHITE, yaw=15)
        self.assertEqual(state['phase'], 'turn')
        self.assertEqual(state['target_heading_deg'], 105)

    def test_brief_widening_does_not_turn_on_gradual_side_exit(self):
        self.tick()
        self.tick()
        self.assertTrue(self.tick((0, 0, 0, 1))['next_turn_ready'])
        self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)
        self.tick((0, 0, 0, 1))
        self.assertFalse(self.tick((0, 0, 1, 1))['next_turn_ready'])
        self.assertFalse(self.tick((0, 1, 1, 1))['next_turn_ready'])
        self.assertEqual(self.tick(WHITE)['reason'], 'line_lost')

    def test_sustained_widening_survives_narrowing_or_one_center_sample(self):
        widening = (0, 0, 0, 1)
        edge = (0, 1, 1, 1)
        cases = (
            (7, ((0, 1, 0, 1), (0, 0, 1, 1), (0, 0, 1, 1), edge), True),
            (3, (CENTER, widening, edge, edge), True),
            (8, (edge,), True),
            (8, (CENTER,), True),
            (8, (CENTER, CENTER), False),
            (8, ((1, 0, 0, 0), (1, 1, 1, 0)), False),
            (1, (edge,), False),
        )
        for wide_frames, departure, expect_turn in cases:
            with self.subTest(wide_frames=wide_frames, departure=departure):
                self.follower.stop()
                self.session = self.follower.start()
                self.tick()
                self.tick()
                for _ in range(wide_frames):
                    self.tick(widening)
                for pattern in departure:
                    self.tick(pattern)
                crossing = self.tick(WHITE)
                if expect_turn:
                    self.assertEqual(crossing['white_samples'], 1)
                    self.assertEqual(self.tick(WHITE)['white_samples'], 2)
                    self.assertEqual(self.tick(WHITE)['reason'], 'route_turn_left')
                else:
                    self.assertEqual(crossing['reason'], 'line_lost')
                    self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)

    def test_full_width_corner_survives_until_three_consecutive_white_samples(self):
        self.tick()
        self.tick()
        for pattern in ((0, 0, 0, 1), (0, 0, 0, 0), (0, 0, 0, 1), (0, 0, 1, 1)):
            self.assertTrue(self.tick(pattern)['next_turn_ready'])
        edge = (0, 1, 1, 1)
        for _ in range(20):
            self.assertTrue(self.tick(edge)['next_turn_ready'])
        self.assertEqual(self.tick(WHITE)['white_samples'], 1)
        self.assertEqual(self.tick(WHITE)['white_samples'], 2)
        self.assertEqual(self.tick(edge)['white_samples'], 0)
        self.assertEqual(self.tick(WHITE)['white_samples'], 1)
        self.assertEqual(self.tick(WHITE)['white_samples'], 2)
        state = self.tick(WHITE, yaw=15)
        self.assertEqual(state['reason'], 'route_turn_left')
        self.assertEqual(state['turn_entry_heading_deg'], 15)
        self.assertEqual(self.motion.snapshot()['outputs'], [-20, -20, 20, 20])

    def test_turn_ignores_early_black_and_reacquires_at_a_relative_angle(self):
        self.begin_left_turn(start_yaw=100)
        edge = (0, 1, 1, 1)
        self.assertEqual(self.tick(edge, yaw=159)['phase'], 'turn')
        self.assertEqual(self.tick(WHITE, yaw=-170)['phase'], 'turn')
        state = self.tick(edge, yaw=-165)
        self.assertEqual(state['phase'], 'align')
        self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)
        self.assertEqual(self.tick(edge, yaw=-165)['phase'], 'align')
        self.assertEqual(self.motion.snapshot()['outputs'], [-20, -20, 20, 20])
        for _ in range(2):
            state = self.tick(CENTER, yaw=-165)
        self.assertEqual(state['turn_index'], 1)
        self.assertEqual(state['phase'], 'follow')

    def test_turn_stops_at_130_degrees_without_a_line(self):
        self.begin_left_turn()
        self.assertEqual(self.tick(WHITE, yaw=129)['phase'], 'turn')
        state = self.tick(WHITE, yaw=130)
        self.assertEqual(state['reason'], 'turn_angle_limit')
        self.assertFalse(state['running'])
        self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)
        self.assertEqual(state['turn_index'], 0)

    def test_repeat_target_refreshes_without_blocking_fresh_steering_or_stop(self):
        self.tick()
        start = len(self.wire.packets)
        for _ in range(4):
            self.tick(dt=.02)
        self.assertEqual(self.wire.packets[start:], [])
        self.tick(dt=.03)
        self.assertEqual(self.wire.packets[start:], [b'$spd:20,20,20,20#'])
        self.tick((1, 0, 1, 1), dt=.02)
        self.assertEqual(self.wire.packets[-1], b'$spd:0,0,0,0#')
        self.tick((1, 0, 1, 1), dt=.02)
        self.assertEqual(self.wire.packets[-1], b'$spd:-20,-20,20,20#')
        self.tick(WHITE, dt=.02)
        self.assertFalse(self.motion.snapshot()['armed'])
        self.assertEqual(self.motion.snapshot()['outputs'], [0] * 4)

    def test_brief_corner_branch_remains_visible_with_the_real_uart_spacing(self):
        # Actual SerialPWM.send supplies the 30 ms spacing. The simulated
        # probe source publishes every 22 ms independently of motor writes.
        transport = SerialPWM.__new__(SerialPWM)
        transport.last_packet_at = 0.0

        def record_write(packet):
            self.wire.send(packet)
            return len(packet)

        transport.serial = SimpleNamespace(write=record_write, close=lambda: None)

        def wait(seconds):
            self.clock.now += seconds

        with patch.object(self.motion, 'transport', transport), patch(
                'motor_control.time', SimpleNamespace(monotonic=self.clock, sleep=wait)):
            start = self.clock()
            previous_frame = None
            for _ in range(9):
                tick_started = self.clock()
                frame = int((tick_started - start + 1e-9) / .022)
                if frame != previous_frame:
                    if frame < 3:
                        pattern = CENTER
                    elif frame == 3:
                        pattern = (0, 0, 0, 1)
                    elif frame == 4:
                        pattern = (0, 1, 0, 1)
                    else:
                        pattern = WHITE
                    self.feed.publish(reversed(pattern))
                    previous_frame = frame
                self.imu.accept((0, 0, 0))
                state = self.follower.tick()
                if not state['running']:
                    break
                self.clock.now = max(tick_started + .02, self.clock() + .001)
        self.assertTrue(state['running'], state['reason'])
        self.assertEqual(state['reason'], 'route_turn_left')
        self.assertEqual(self.motion.snapshot()['outputs'], [-20, -20, 20, 20])
