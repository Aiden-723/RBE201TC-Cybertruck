import itertools
import json
from pathlib import Path
import unittest
from basic_line_follower import CENTER, WHITE
from route_line_follower import RouteLineFollower
from test_basic_line_follower import Clock, Feed, Motor

class RouteLineTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.feed=Feed(self.clock);self.motor=Motor()
        self.f=RouteLineFollower(self.motor,self.feed,{'turns':['left','left','right','right']},clock=self.clock,threaded=False)
        self.addCleanup(self.f.close)
        self.owner=self.f.start(20);self.seq=0;self.f.heartbeat(self.owner,0)
        self.tick();self.tick()
    def tick(self, values=CENTER, dt=.03, fresh=True):
        self.clock.now+=dt
        if fresh:self.feed.publish(list(reversed(values)))
        self.seq+=1;self.f.heartbeat(self.owner,self.seq)
        return self.f.tick()
    def trigger(self):
        for _ in range(10):state=self.tick(WHITE)
        return state
    def test_tenth_white_triggers_and_black_resets(self):
        for i in range(1,10):
            s=self.tick(WHITE);self.assertEqual(s['white_samples'],i);self.assertEqual(s['phase'],'follow')
        self.assertEqual(self.tick((1,1,1,0))['white_samples'],0)
        for _ in range(9):self.assertEqual(self.tick(WHITE)['phase'],'follow')
        s=self.tick(WHITE);self.assertEqual(s['phase'],'turn');self.assertEqual(self.motor.outputs,(-20,20))
        self.assertEqual(s['turn_index'],0)
    def test_cached_white_does_not_supply_ten_samples(self):
        self.tick(WHITE)
        for _ in range(10):
            s=self.tick(fresh=False,dt=.01);self.assertEqual(s['white_samples'],1);self.assertEqual(s['phase'],'follow')
        for _ in range(8):self.tick(WHITE)
        self.assertEqual(self.f.snapshot()['phase'],'follow')
        self.assertEqual(self.tick(WHITE)['phase'],'turn')
    def test_partial_line_is_followed_and_center_completes_corner(self):
        self.trigger()
        for _ in range(10):
            s=self.tick(WHITE);self.assertEqual(s['phase'],'turn');self.assertEqual(s['turn_index'],0)
        s=self.tick((0,1,1,1));self.assertEqual(s['phase'],'reacquire');self.assertEqual(self.motor.outputs,(0,20))
        s=self.tick((1,0,1,1));self.assertEqual(self.motor.outputs,(12,20));self.assertEqual(s['turn_index'],0)
        self.assertEqual(self.tick()['phase'],'reacquire')
        s=self.tick();self.assertEqual(s['phase'],'follow');self.assertEqual(s['turn_index'],1)
        self.assertEqual(self.motor.outputs,(20,20))
    def test_lost_during_reacquisition_keeps_current_corner(self):
        self.trigger();self.tick((0,1,1,1));self.tick()
        s=self.tick(WHITE);self.assertEqual(s['phase'],'turn');self.assertEqual(s['turn_index'],0)
        self.assertEqual(self.motor.outputs,(-20,20))
        self.tick();self.assertEqual(self.tick()['turn_index'],1)
    def test_cached_center_cannot_complete_corner(self):
        self.trigger();self.tick()
        for _ in range(5):
            s=self.tick(fresh=False);self.assertEqual(s['phase'],'reacquire');self.assertEqual(s['turn_index'],0)
            self.assertEqual(self.motor.outputs,(20,20))
        self.assertEqual(self.tick()['turn_index'],1)
    def test_full_14_corner_user_route_with_straights(self):
        self.f.turns=tuple(json.loads((Path(__file__).resolve().parents[2]/'deploy/imu_route.json').read_text())['turns'])
        for i,direction in enumerate(self.f.turns):
            for _ in range(25):
                s=self.tick();self.assertEqual(s['phase'],'follow');self.assertEqual(s['turn_index'],i)
            self.assertEqual(self.trigger()['phase'],'turn')
            self.assertEqual(self.motor.outputs,(-20,20) if direction=='left' else (20,-20))
            self.tick((0,1,1,1) if direction=='left' else (1,1,1,0));self.tick();s=self.tick()
            self.assertEqual(s['phase'],'follow');self.assertEqual(s['turn_index'],i+1)
        s=self.tick(WHITE);self.assertFalse(s['running']);self.assertEqual(s['reason'],'route_complete')
        self.assertEqual(self.motor.outputs,(0,0))
    def test_all_16_patterns_have_actions(self):
        for pattern in itertools.product((0,1),repeat=4):
            self.assertTrue(self.tick(pattern)['running']);self.assertTrue(all(v>=0 for v in self.motor.outputs))
        self.trigger()
        for pattern in itertools.product((0,1),repeat=4):
            s=self.tick(pattern);self.assertTrue(s['running']);self.assertEqual(s['turn_index'],0)
    def test_initial_white_does_not_invent_turn(self):
        self.f.stop();self.owner=self.f.start();self.seq=0;self.f.heartbeat(self.owner,0)
        s=self.tick(WHITE);self.assertFalse(s['running']);self.assertEqual(s['reason'],'line_lost')
    def test_stale_sensor_and_expired_owner_stop(self):
        self.trigger();self.clock.now+=.41;s=self.f.tick()
        self.assertEqual(s['reason'],'stale_line_sample');self.assertEqual(self.motor.outputs,(0,0))
        self.owner=self.f.start();self.seq=0;self.f.heartbeat(self.owner,0);self.clock.now+=.81
        s=self.f.tick();self.assertEqual(s['reason'],'owner_heartbeat_expired');self.assertEqual(self.motor.outputs,(0,0))
    def test_new_start_resets_without_imu_calibration(self):
        self.trigger();self.tick();self.tick();self.f.stop()
        self.owner=self.f.start();self.seq=0;self.f.heartbeat(self.owner,0)
        s=self.f.snapshot();self.assertTrue(s['ready']);self.assertFalse(s['imu_required'])
        self.assertEqual(s['phase'],'follow');self.assertEqual(s['turn_index'],0)
    def test_runtime_gear_change_updates_pivot(self):
        self.trigger();self.seq+=1;self.f.heartbeat(self.owner,self.seq,speed=40)
        s=self.tick(WHITE);self.assertEqual((s['left'],s['right']),(-40,40))

class LineSelectionTests(unittest.TestCase):
    def make_server(self,imu=None):
        from server import create_server
        from motor_control import MotorControl
        class Wire:
            def __init__(self):self.packets=[]
            def send(self,p):self.packets.append(p)
            def close(self):pass
        clock=Clock();feed=Feed(clock)
        class Sensors:
            directory='SOFTWARE_FIXTURE_ONLY'
            def one(self,name):return feed()
        motor=MotorControl(Wire(),clock=clock,mode='speed',startup_boost=True);self.addCleanup(motor.close)
        server=create_server('127.0.0.1',0,sensors=Sensors(),motor=motor,imu=imu,route_config={'controller':'line','turns':['left']})
        self.addCleanup(server.server_close)
        server.driving_mode.follower.threaded=False;server.driving_mode.follower.clock=clock
        return server,clock,feed,motor
    def test_no_imu_can_start_and_drive_real_motor_bridge(self):
        server,clock,feed,motor=self.make_server();f=server.driving_mode.follower;mode=server.driving_mode
        self.assertIsInstance(f,RouteLineFollower);self.assertTrue(f.snapshot()['ready'])
        mode.command({'action':'set_mode','mode':'automatic'})
        owner=mode.command({'action':'autonomy_start','speed':20})['autonomy_session']
        for i in range(12):
            clock.now+=.03;feed.publish(CENTER if i<2 else WHITE)
            mode.command({'action':'autonomy_heartbeat','autonomy_session':owner,'seq':i});f.tick()
        self.assertEqual(f.snapshot()['phase'],'turn');self.assertEqual(motor.snapshot()['outputs'],[-200,-200,200,200])
        mode.command({'action':'stop'});self.assertFalse(motor.snapshot()['armed'])
    def test_bad_or_uncalibrated_imu_does_not_block_start_or_change_outputs(self):
        class Imu:
            status='unavailable';heading=None
            def snapshot(self):return {'status':self.status,'calibrated':False,'heading_deg':self.heading}
            def close(self):pass
        imu=Imu();server,clock,feed,motor=self.make_server(imu);mode=server.driving_mode;f=mode.follower
        mode.command({'action':'set_mode','mode':'automatic'})
        owner=mode.command({'action':'autonomy_start','speed':20})['autonomy_session']
        for i,(status,heading) in enumerate([('unavailable',None),('live',1e9),('live',-1e9)]):
            imu.status,imu.heading=status,heading;clock.now+=.03;feed.publish(CENTER)
            mode.command({'action':'autonomy_heartbeat','autonomy_session':owner,'seq':i});f.tick()
            s=mode.snapshot();self.assertTrue(s['autonomy']['running']);self.assertFalse(s['autonomy']['imu_required'])
            self.assertEqual(s['outputs'],[200]*4)
