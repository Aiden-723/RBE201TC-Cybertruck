import unittest
from basic_line_follower import CENTER, WHITE
from track_imu_follower import TrackImuFollower
from imu_heading import ImuHeading
from motor_control import MotorControl
from test_basic_line_follower import Clock, Feed

class Hard20Tests(unittest.TestCase):
 command_mode='speed'
 cruise=20
 initial=200
 def setUp(self):
  self.clock=Clock();self.feed=Feed(self.clock);self.imu=ImuHeading(clock=self.clock);self.imu.accept((0,0,0));self.imu.calibrate()
  class Wire:
   def __init__(self):self.packets=[]
   def send(self,p):self.packets.append(p)
   def close(self):pass
  self.wire=Wire();self.motor=MotorControl(self.wire,clock=self.clock,mode=self.command_mode,startup_boost=True)
  self.f=TrackImuFollower(self.motor,self.feed,self.imu,{'turns':['left']},clock=self.clock,threaded=False)
  self.owner=self.f.start();self.seq=0;self.f.heartbeat(self.owner,0)
  self.addCleanup(self.motor.close);self.addCleanup(self.f.close);self.tick();self.tick()
 def tick(self,v=CENTER,yaw=0):
  self.clock.now+=.03;self.feed.publish(list(reversed(v)));self.imu.accept((0,0,yaw));self.seq+=1;self.f.heartbeat(self.owner,self.seq);return self.f.tick()
 def test_start_correction_and_route_turn_use_the_configured_gear(self):
  self.assertEqual(self.f.snapshot()['speed_gears'],[self.cruise])
  self.assertEqual(self.motor.snapshot()['outputs'],[self.initial]*4)
  for _ in range(8):self.tick()
  self.assertEqual(self.motor.snapshot()['outputs'],[self.cruise]*4)
  self.tick((1,1,0,1));self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
  self.tick((1,1,0,1));self.assertEqual(self.motor.snapshot()['outputs'],[self.initial]*2+[-self.initial]*2)
  for _ in range(8):self.tick((1,1,0,1))
  self.assertEqual(self.motor.snapshot()['outputs'],[self.cruise]*2+[-self.cruise]*2)
  self.tick();self.assertEqual(self.motor.snapshot()['outputs'],[self.initial]*4);self.tick()
  self.tick((0,0,0,1))
  for _ in range(self.f.white_confirmation_samples):self.tick(WHITE)
  self.assertEqual(self.f.phase,'turn')
  self.assertEqual(self.motor.snapshot()['outputs'],[-self.initial]*2+[self.initial]*2)
  for _ in range(90):self.tick(WHITE)
  self.assertEqual(self.f.phase,'turn')
  self.assertEqual(self.motor.snapshot()['outputs'],[-self.cruise]*2+[self.cruise]*2)
  self.assertFalse(self.motor.snapshot()['startup_boost_active'])
 def test_side_departure_stops_on_first_white_without_resuming(self):
  self.tick((1,1,1,0));s=self.tick(WHITE)
  self.assertFalse(s['running']);self.assertEqual(s['reason'],'line_lost');self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
  for _ in range(60):self.f.tick()
  self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
 def test_above20_gear_is_rejected(self):
  with self.assertRaises(ValueError):self.f.heartbeat(self.owner,1,speed=40)


class PWMHard20Tests(Hard20Tests):
 command_mode='pwm'
 cruise=600
 initial=600

 def test_pwm_is_used_for_manual_adjustment_and_stop(self):
  self.f.stop()
  owner=self.motor.command({'action':'arm'})['session']
  for seq,speed in enumerate((550,620,700)):
   self.motor.command(dict(action='drive',session=owner,seq=seq,direction='forward',speed=speed))
   self.assertEqual(self.wire.packets[-1],('$pwm:'+','.join([str(speed)]*4)+'#').encode())
  self.motor.command(dict(action='release',session=owner,seq=3))
  self.assertEqual(self.wire.packets[-1],b'$pwm:0,0,0,0#')
  self.assertTrue(all(p.startswith(b'$pwm:') for p in self.wire.packets))
