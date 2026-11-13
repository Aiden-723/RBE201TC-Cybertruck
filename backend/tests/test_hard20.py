import unittest
from basic_line_follower import CENTER, WHITE
from track_imu_follower import TrackImuFollower
from imu_heading import ImuHeading
from motor_control import MotorControl
from test_basic_line_follower import Clock, Feed

class Hard20Tests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock();self.feed=Feed(self.clock);self.imu=ImuHeading(clock=self.clock);self.imu.accept((0,0,0));self.imu.calibrate()
  class Wire:
   def __init__(self):self.packets=[]
   def send(self,p):self.packets.append(p)
   def close(self):pass
  self.wire=Wire();self.motor=MotorControl(self.wire,clock=self.clock,mode='speed',startup_boost=True)
  self.f=TrackImuFollower(self.motor,self.feed,self.imu,{'turns':['left']},clock=self.clock,threaded=False)
  self.owner=self.f.start();self.seq=0;self.f.heartbeat(self.owner,0)
  self.addCleanup(self.motor.close);self.addCleanup(self.f.close);self.tick();self.tick()
 def tick(self,v=CENTER,yaw=0):
  self.clock.now+=.03;self.feed.publish(list(reversed(v)));self.imu.accept((0,0,yaw));self.seq+=1;self.f.heartbeat(self.owner,self.seq);return self.f.tick()
 def test_all_wire_outputs_including_start_correction_reverse_turn_and_stall_are_at_most20(self):
  self.assertEqual(self.motor.snapshot()['outputs'],[20]*4)
  self.tick((1,1,0,1));self.assertEqual(self.motor.snapshot()['outputs'],[20,20,4,4]);self.tick();self.tick()
  for _ in range(10):self.tick(WHITE)
  self.assertEqual(self.f.phase,'reverse')
  for _ in range(90):self.tick(WHITE)
  self.assertEqual(self.f.phase,'turn')
  for p in self.wire.packets:
   if p.startswith(b'$spd:'):self.assertTrue(all(abs(int(v))<=20 for v in p[5:-1].split(b',')),p)
  self.assertFalse(self.motor.snapshot()['startup_boost_active'])
 def test_side_departure_stops_on_first_white_without_resuming(self):
  self.tick((1,1,1,0));s=self.tick(WHITE)
  self.assertFalse(s['running']);self.assertEqual(s['reason'],'line_lost');self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
  for _ in range(60):self.f.tick()
  self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
 def test_above20_gear_is_rejected(self):
  with self.assertRaises(ValueError):self.f.heartbeat(self.owner,1,speed=40)
