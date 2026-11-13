"""SOFTWARE_FIXTURE: source-derived truth table plus real arbiter/fake UART."""
import copy,json,unittest
from pathlib import Path
from source_line_follower import SourceLineFollower,source_decision,SOURCE_COMMIT
from motor_control import MotorControl,ControlError
from driving_mode import DrivingMode
from test_basic_line_follower import Clock,Feed,Motor

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.feed=Feed(self.clock);self.motor=Motor()
        self.f=SourceLineFollower(self.motor,self.feed,clock=self.clock,threaded=False,channel_permutation=(0,1,2,3));self.addCleanup(self.f.close)
        self.owner=self.f.start(84);self.seq=0;self.f.heartbeat(self.owner,0)
    def step(self,values,dt=.02,heartbeat=True):
        self.clock.now+=dt;self.feed.publish(values)
        if heartbeat:self.seq+=1;self.f.heartbeat(self.owner,self.seq)
        return self.f.tick()
    def test_all16_actions_and_dwell_match_independently_extracted_source_conditions(self):
        data=json.loads((Path(__file__).parent/'fixtures/yahboom_truth.json').read_text())
        self.assertEqual(data['commit'],SOURCE_COMMIT);self.assertEqual(len(data['rows']),16)
        labels={'spin_right':'hard_right','spin_left':'hard_left','left':'inner_left','right':'inner_right','run':'center'}
        for row in data['rows']:
            d,delay,action=source_decision(row['physical_input'],84)
            self.assertEqual(action,row['github']['action']);self.assertEqual(delay,row['github']['hold_s'])
            if action=='retain_previous':self.assertIsNone(d)
            else:self.assertEqual(d[2],labels[action])
    def test_mixed_priority_allblack_is_source_right_not_custom_ambiguity(self):
        s=self.step([0,0,0,0]);self.assertEqual((s['left'],s['right']),(200,-200))
        self.assertFalse(s['line_gap_active']);self.assertEqual(s['source_mixed_dwell_s'],.08)
    def test_special_branch_holds80ms_then_immediately_uses_current_center(self):
        s=self.step([1,0,0,0]);self.assertEqual(s['band'],'hard_right')
        for _ in range(3):self.assertEqual(self.step([1,0,0,1])['band'],'hard_right')
        s=self.step([1,0,0,1],dt=.021);self.assertEqual((s['left'],s['right']),(84,84))
    def test_dwell_starts_after_slow_motor_submission(self):
        original=self.motor.drive_sides
        def delayed(*args):self.clock.now+=.06;return original(*args)
        self.motor.drive_sides=delayed;s=self.step([1,0,0,0])
        self.assertAlmostEqual(s['source_dwell_remaining_s'],.08)
        self.motor.drive_sides=original
        self.assertEqual(self.step([1,0,0,1],dt=.06)['band'],'hard_right')
        self.assertEqual(self.step([1,0,0,1],dt=.021)['band'],'center')
    def test_simple_outer_has_no_new_dwell_or_angle_lock(self):
        self.step([1,1,1,0]);s=self.step([1,0,0,1],dt=.001)
        self.assertEqual(s['band'],'center');self.assertEqual(s['source_dwell_remaining_s'],0)
    def test_white_never_forwards_and_can_reacquire_without_new_start(self):
        self.step([1,0,0,1]);s=self.step([1,1,1,1],dt=.001)
        self.assertEqual((s['left'],s['right']),(0,0));self.assertTrue(s['running'])
        self.assertEqual(self.step([1,0,0,1])['band'],'center')
    def test_white_pivot_budget_and_mixed_black_recovery(self):
        self.step([1,0,0,0]);s=self.step([1,1,1,1]);self.assertEqual(s['gap_motion'],'pivot')
        s=self.step([0,0,0,0]);self.assertFalse(s['line_gap_active'])
        self.assertEqual((s['left'],s['right']),(200,-200))
    def test_stop_during80ms_dwell_is_immediate_and_no_late_motion(self):
        self.step([1,0,0,0]);self.f.stop();count=len(self.motor.drives)
        self.step([1,0,0,1],heartbeat=False);self.assertEqual(len(self.motor.drives),count);self.assertIsNone(self.motor.session)
    def test_stale_data_interrupts_source_dwell(self):
        self.step([1,0,0,0]);self.feed.age_override=1
        s=self.step([1,0,0,0],dt=.001);self.assertFalse(s['running']);self.assertEqual(s['state'],'sensor_stale')
    def test_owner_expiry_and_restart_never_keep_old_source_pulse(self):
        self.step([1,0,0,0]);s=self.step([1,0,0,1],dt=.801,heartbeat=False)
        self.assertFalse(s['running']);self.owner=self.f.start(84);self.seq=0;self.f.heartbeat(self.owner,0)
        self.assertEqual(self.step([1,0,0,1])['band'],'center')
    def test_production_factory_uses_source_adapter_with_single_motor_owner(self):
        class Wire:
            def __init__(self):self.packets=[]
            def send(self,p):self.packets.append(p)
            def close(self):pass
        wire=Wire();m=MotorControl(wire,clock=self.clock,mode='speed');self.addCleanup(m.close)
        mode=DrivingMode(m,line_source=self.feed);self.addCleanup(mode.close)
        self.assertIsInstance(mode.follower,SourceLineFollower)
        mode.follower.threaded=False;mode.follower.clock=self.clock
        mode.command({'action':'set_mode','mode':'automatic'})
        token=mode.command({'action':'autonomy_start','speed':84})['autonomy_session']
        mode.command({'action':'autonomy_heartbeat','autonomy_session':token,'seq':1})
        self.feed.publish([0,0,0,0]);s=mode.follower.tick()
        self.assertEqual(s['control_policy'],'yahboom_four_digital_v8')
        self.assertEqual(wire.packets[-1],b'$spd:200,200,-200,-200#')
        mode.command({'action':'stop'});self.assertFalse(m.snapshot()['armed'])

    def test_white_timeout_and_cache_do_not_extend_budget(self):
        self.step([1,0,0,1]);first=self.step([1,1,1,1]);anchor=self.f._line_gap_started
        for i in range(7):
            s=self.step([1,1,1,1],dt=.2)
            self.assertEqual(self.f._line_gap_started,anchor)
        self.assertFalse(s['running']);self.assertEqual(s['reason'],'line_gap_timeout')
        count=len(self.motor.drives);self.step([1,0,0,1],heartbeat=False)
        self.assertEqual(len(self.motor.drives),count)
