import itertools
import json
from pathlib import Path
import unittest
from basic_line_follower import CENTER, WHITE
from imu_heading import ImuHeading
from imu_line_follower import ImuLineFollower
from driving_mode import DrivingMode
from test_basic_line_follower import Clock, Feed, Motor


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock();self.feed = Feed(self.clock);self.motor = Motor()
        self.imu = ImuHeading(clock=self.clock);self.imu.accept((0, 0, 0));self.imu.calibrate()
        self.f = ImuLineFollower(self.motor, self.feed, self.imu, {'turns':['left','left','right']}, clock=self.clock, threaded=False)
        self.owner = self.f.start(20);self.seq = 0
        self.f.heartbeat(self.owner, self.seq)
        self.addCleanup(self.f.close)

    def tick(self, physical=CENTER, yaw=0):
        self.clock.now += .03;self.feed.publish(list(reversed(physical)));self.imu.accept((0, 0, yaw))
        self.seq += 1;self.f.heartbeat(self.owner, self.seq)
        return self.f.tick()

    def test_outer_black_waits_until_all_white_then_imu_ignores_line(self):
        for physical in [CENTER,(1,0,0,0),(0,0,0,0),(0,0,0,1)]:
            state=self.tick(physical)
            self.assertEqual(state['phase'],'follow');self.assertEqual(self.motor.outputs,(20,20))
        self.assertEqual(self.tick(WHITE)['phase'],'turn');self.assertEqual(self.motor.outputs,(-20,20))
        for pattern in itertools.product((0,1),repeat=4):
            state=self.tick(pattern,yaw=40)
            self.assertEqual(state['phase'],'turn');self.assertEqual(self.motor.outputs,(-20,20))
        state=self.tick(CENTER,yaw=80)
        self.assertEqual(self.motor.outputs,(-10,10))
        self.assertEqual(state['turn_index'],0)
        state=self.tick(CENTER,yaw=90)
        self.assertEqual(state['turn_index'],1);self.assertEqual(self.motor.outputs,(0,0))
        self.assertEqual(self.tick(CENTER,yaw=90)['phase'],'follow')

    def test_left_left_right_accumulate_90_180_90_and_wrap(self):
        self.tick()
        for index,(target,yaws) in enumerate([(90,[45,90]),(180,[140,179,-179]),(90,[140,90])],1):
            self.tick(WHITE,yaw=0 if index==1 else 90 if index==2 else -179)
            for yaw in yaws:state=self.tick(CENTER,yaw=yaw)
            self.assertEqual(state['turn_index'],index)
            self.assertEqual(state['target_heading_deg'],target)
            self.tick(CENTER,yaw=yaws[-1])

    def test_white_after_turn_stops_without_blind_motion(self):
        self.tick();self.tick(WHITE);self.tick(WHITE,yaw=90)
        state=self.tick(WHITE,yaw=90)
        self.assertFalse(state['running']);self.assertEqual(state['reason'],'line_not_found_after_turn')
        self.assertEqual(self.motor.outputs,(0,0))

    def test_stopping_mid_turn_resumes_same_target_not_another_90(self):
        self.tick();self.tick(WHITE);self.tick(WHITE,yaw=40)
        self.f.stop();self.owner=self.f.start(20);self.seq=0;self.f.heartbeat(self.owner,0)
        state=self.tick(WHITE,yaw=45)
        self.assertEqual(state['target_heading_deg'],90);self.assertEqual(state['turn_index'],0)
        self.assertEqual(self.motor.outputs,(-20,20))

    def test_all_visible_patterns_are_handled_before_white(self):
        for pattern in itertools.product((0,1),repeat=4):
            if pattern==WHITE:continue
            state=self.tick(pattern)
            self.assertTrue(state['running']);self.assertEqual(state['phase'],'follow')
            self.assertTrue(all(v>=0 for v in self.motor.outputs))

    def test_imu_only_assists_large_error_when_line_centered(self):
        self.tick(CENTER,yaw=10);self.assertEqual(self.motor.outputs,(20,20))
        self.tick(CENTER,yaw=30);self.assertEqual(self.motor.outputs,(20,10))
        self.tick((1,0,1,1),yaw=30);self.assertEqual(self.motor.outputs,(12,20))

    def test_imu_staleness_and_initial_white_stop(self):
        self.tick();self.clock.now+=.41;self.feed.publish();state=self.f.tick()
        self.assertEqual(state['reason'],'imu_not_live');self.assertEqual(self.motor.outputs,(0,0))
        self.imu.accept((0,0,0));self.owner=self.f.start();self.seq=0;self.f.heartbeat(self.owner,0)
        self.assertEqual(self.tick(WHITE)['reason'],'line_lost')

    def test_calibration_requires_stopped_and_resets_route(self):
        mode=DrivingMode(follower=self.f,imu=self.imu)
        with self.assertRaisesRegex(Exception,'stop_before_calibration'):mode.command({'action':'imu_calibrate'})
        self.f.stop();self.f.turn_index=3;self.f.target_heading=90
        mode.command({'action':'imu_calibrate'})
        self.assertEqual(self.f.turn_index,0);self.assertEqual(self.f.target_heading,0)
        self.assertEqual(self.imu.snapshot()['heading_deg'],0)

    def test_configured_offset_keeps_forward_then_pivots(self):
        self.f.offset_s=.1;self.tick()
        self.assertEqual(self.tick(WHITE)['phase'],'offset');self.assertEqual(self.motor.outputs,(20,20))
        for _ in range(4):state=self.tick(WHITE)
        self.assertEqual(state['phase'],'turn');self.assertEqual(self.motor.outputs,(-20,20))

    def test_route_file_exactly_matches_user_order(self):
        cfg=json.loads((Path(__file__).resolve().parents[2]/'deploy/imu_route.json').read_text())
        self.assertEqual(''.join('左' if x=='left' else '右' for x in cfg['turns']),'左左右右左左右右左左右右左左')
        self.assertEqual(cfg['turn_offset_seconds'],0)
