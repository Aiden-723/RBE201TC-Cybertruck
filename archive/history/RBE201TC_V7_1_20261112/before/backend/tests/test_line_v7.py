"""SOFTWARE_FIXTURE: v6 surge counterexamples, bounded pivot and wire dedup."""
import unittest
from test_corner_line_follower import FollowerFixture, CENTER, WHITE, RIGHT
from basic_line_follower import steering

class V7Tests(FollowerFixture,unittest.TestCase):
    def test_all_unknown_patterns_brake_forward_and_inner_immediately(self):
        for prior in (CENTER,[1,0,1,1],[1,1,0,1]):
            for unknown in (WHITE,[0,0,0,0],[0,1,1,0],[0,0,1,0],[0,1,0,0]):
                self.reset(first=prior);before=len(self.motor.drives)
                s=self.step(unknown,dt=.001)
                self.assertEqual(self.motor.drives[-1][1:],(0,0))
                self.assertEqual(len(self.motor.drives),before+1)
                self.assertEqual(s['gap_motion'],'brake');self.assertTrue(s['running'])
                self.assertEqual(self.pair(self.step(CENTER,dt=.001)),(84,84))
    def test_no_past_pivot_can_revive_after_visible_forward_then_unknown(self):
        self.step(RIGHT);self.step(CENTER);self.step(WHITE)
        for frame in (WHITE,[0,0,0,0],WHITE):
            self.assertEqual(self.pair(self.step(frame)),(0,0))
    def test_existing_symmetric_pivot_only_is_bounded_and_never_reverses_on_unknown(self):
        self.step(RIGHT)
        for i in range(7):
            s=self.step(WHITE if i%2 else [0,0,0,0],dt=.2)
            self.assertEqual(self.pair(s),(200,-200));self.assertEqual(s['gap_motion'],'pivot')
        s=self.step(WHITE,dt=.201);self.assert_stopped(s);self.assertEqual(s['reason'],'line_gap_timeout')
    def test_same_pair_is_deduplicated_but_renewed_before_lease(self):
        first=len(self.motor.drives)
        for _ in range(9):self.step(CENTER,dt=.02)
        self.assertEqual(len(self.motor.drives),first)
        self.step(CENTER,dt=.03);self.assertEqual(len(self.motor.drives),first+1)
        self.assertEqual(self.motor.drives[-1][1:],(84,84))
    def test_every_change_and_explicit_stop_bypasses_dedup(self):
        for frame,pair in (([1,0,1,1],(0,84)),(RIGHT,(200,-200)),(WHITE,(200,-200)),(CENTER,(84,84)),(WHITE,(0,0))):
            self.assertEqual(self.pair(self.step(frame,dt=.001)),pair)
        self.worker.stop();self.assertEqual(self.motor.calls[-1],{'action':'stop'})
    def test_expired_lease_is_never_renewed_by_a_late_tick(self):
        count=len(self.motor.drives);s=self.step(CENTER,dt=.451)
        self.assert_stopped(s);self.assertEqual(s['reason'],'motor_lease_expired')
        self.assertEqual(len(self.motor.drives),count)
    def test_external_stop_detected_during_dedup_window(self):
        self.motor.command({'action':'stop'});s=self.step(CENTER,dt=.001)
        self.assert_stopped(s);self.assertEqual(s['reason'],'motor_drive_failed')
    def test_nonzero_lower_bound_same_speed_units_and_zero_remains_zero(self):
        for speed in (80,84,140,200,220):
            for frame in (CENTER,[1,0,1,1],[1,1,0,1],RIGHT):
                pair=steering(frame,speed)[:2]
                self.assertTrue(all(v==0 or 80<=abs(v)<=220 for v in pair))
        for speed in (0,40,79,221,True,80.0):
            with self.assertRaises(ValueError):steering(CENTER,speed)
        s=self.worker.snapshot();self.assertEqual(s['min_active_command'],80)
        self.assertEqual(s['command_refresh_s'],.2)
