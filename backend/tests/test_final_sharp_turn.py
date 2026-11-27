import json
from pathlib import Path
import unittest

from basic_line_follower import CENTER, WHITE
from imu_heading import ImuHeading
from motor_control import MotorControl
from test_autonomous_motor_bridge import Wire
from test_basic_line_follower import Clock, Feed
from track_imu_follower import TrackImuFollower


ROUTE_PATH = Path(__file__).resolve().parents[2] / 'deploy' / 'imu_route.json'
RIGHT_BRANCH = (1, 0, 0, 0)
RIGHT_EDGE = (1, 1, 1, 0)


class FinalSharpTurnTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.feed = Feed(self.clock)
        self.imu = ImuHeading(clock=self.clock)
        self.entry_yaw = -100
        self.imu.accept((0, 0, self.entry_yaw))
        self.imu.calibrate()
        self.motor = MotorControl(Wire(), clock=self.clock, mode='pwm', startup_boost=True)
        config = json.loads(ROUTE_PATH.read_text())
        self.follower = TrackImuFollower(
            self.motor, self.feed, self.imu, config, clock=self.clock, threaded=False,
        )
        self.addCleanup(self.motor.close)
        self.addCleanup(self.follower.close)
        self.owner = self.follower.start()
        self.seq = 0
        self.follower.turn_index = 14  # Approach after the previous 14 bends.
        self.tick(CENTER)
        self.tick(CENTER)

    def tick(self, values, right_angle=0):
        self.clock.now += .03
        self.feed.publish(reversed(values))
        yaw = (self.entry_yaw - right_angle + 180) % 360 - 180
        self.imu.accept((0, 0, yaw))
        self.seq += 1
        self.follower.heartbeat(self.owner, self.seq, speed=600)
        return self.follower.tick()

    def begin_final_turn(self):
        self.assertEqual(len(self.follower.turns), 15)
        self.assertEqual(self.follower.turns[-1], 'right')
        for pattern in ((0, 0, 0, 0), RIGHT_BRANCH):
            self.assertFalse(self.tick(pattern)['next_turn_ready'])
            self.assertEqual(self.motor.snapshot()['outputs'], [600] * 4)
        self.assertFalse(self.tick(CENTER)['next_turn_ready'])
        self.assertTrue(self.tick(CENTER)['next_turn_ready'])
        for count in (1, 2):
            state = self.tick(WHITE)
            self.assertEqual(state['white_samples'], count)
            self.assertEqual(self.motor.snapshot()['outputs'], [0] * 4)
        state = self.tick(WHITE)
        self.assertEqual(state['reason'], 'route_turn_right')
        self.assertEqual(state['turn_entry_heading_deg'], 0)
        self.assertEqual(self.motor.snapshot()['outputs'], [600, 600, -600, -600])

    def test_sharp_turn_accepts_line_at_120_across_imu_wrap(self):
        self.begin_final_turn()
        for angle in (22, 56, 90, 116, 119):
            result = self.tick(RIGHT_EDGE, right_angle=angle)
            self.assertEqual(result['phase'], 'turn')
        self.assertEqual(self.tick(RIGHT_EDGE, right_angle=120)['phase'], 'align')
        self.assertEqual(self.motor.snapshot()['outputs'], [0] * 4)
        self.tick(CENTER, right_angle=120)
        state = self.tick(CENTER, right_angle=120)
        self.assertEqual(state['turn_index'], 15)
        self.assertEqual(state['phase'], 'follow')
        self.assertEqual(self.motor.snapshot()['outputs'], [600] * 4)

    def test_sharp_turn_stops_at_180_if_exit_line_is_missing(self):
        self.begin_final_turn()
        for angle in (30, 67, 101, 139, 179):
            self.assertEqual(self.tick(WHITE, right_angle=angle)['phase'], 'turn')
        state = self.tick(WHITE, right_angle=180)
        self.assertFalse(state['running'])
        self.assertEqual(state['reason'], 'turn_angle_limit')
        self.assertEqual(self.motor.snapshot()['outputs'], [0] * 4)

    def test_line_loss_at_connector_does_not_trigger_sharp_turn(self):
        self.tick(RIGHT_BRANCH)
        self.tick(CENTER)
        state = self.tick(WHITE)
        self.assertEqual(state['reason'], 'line_lost')
        self.assertEqual(self.motor.snapshot()['outputs'], [0] * 4)

    def test_ordinary_turn_retains_130_degree_limit_with_sharp_turn_configured(self):
        self.follower.turn_index = 0
        self.tick((0, 0, 0, 1))
        for _ in range(3):
            state = self.tick(WHITE)
        self.assertEqual(state['reason'], 'route_turn_left')
        for angle in (30, 64, 98, 129):
            self.assertEqual(self.tick(WHITE, right_angle=-angle)['phase'], 'turn')
        self.assertEqual(self.tick(WHITE, right_angle=-130)['reason'], 'turn_angle_limit')
