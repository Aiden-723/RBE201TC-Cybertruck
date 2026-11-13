"""All samples/locations here are SOFTWARE FIXTURES, never physical evidence."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from rehearse_mission import run_case,fixture_plan,fixture_status,LABEL
from experiment_log import RunLog,Station,LogPersistenceError
from line_guidance import LineGuidance,OrderedCrossings
from mission_plan import validate_plan,Observation
from mission_runtime import MissionRuntime,MissionLoop,SimulatedMotion,LeasedMotorPort,PassiveCapture,inspect_run
from motor_control import MotorControl,ControlError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix=LABEL);self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_complete_two_station_chain_writes_real_durable_fixture_files(self):
        log,runtime,traces=run_case(self.root,'success')
        self.assertEqual(runtime.snapshot()['state'],'completed');self.assertFalse(runtime.motion.hardware_enabled)
        self.assertEqual([r['Status'] for r in log.records.values()],['success','success'])
        self.assertTrue(all(r['RunPurpose']=='bench_diagnostic' for r in log.records.values()))
        self.assertTrue(all(r['Trigger_source']=='automatic_station/passive_next_result' for r in log.records.values()))
        self.assertEqual(log.manifest['association_mode'],'automatic_stationary')
        self.assertTrue(log.manifest['closed_at']);self.assertEqual(log.manifest['row_count'],2)
        self.assertEqual({t['state'] for t in traces},{'following','stopping','settling','adapting','awaiting_pending','awaiting_result','completed'})
        self.assertFalse(inspect_run(log.directory)['interrupted'])
    def test_failures_keep_empty_values_and_terminate_without_resuming(self):
        for case,status in [('sensor_failure','sensor_failure'),('timeout','timeout'),('reboot','request_failure')]:
            with self.subTest(case=case):
                log,rt,_=run_case(self.root,case)
                self.assertEqual(rt.snapshot()['state'],'failed')
                self.assertEqual([r['Status'] for r in log.records.values()],[status])
                row=next(iter(log.records.values()));self.assertEqual(row['Temperature_°C'],'');self.assertEqual(row['Humidity_%RH'],'')
                before=len(rt.motion.commands);rt.tick(None,None,now=1000)
                self.assertEqual(len(rt.motion.commands),before)
    def test_no_stationarity_lost_line_and_estop_never_capture(self):
        for case,state in [('missing_stationarity','failed'),('lost_line','failed'),('emergency_stop','emergency_stopped')]:
            log,rt,_=run_case(self.root,case)
            self.assertEqual(rt.snapshot()['state'],state);self.assertEqual(len(log.records),0)
            self.assertTrue(rt.motion.stopped)
    def make_runtime(self):
        self.now=100.;self.wall=lambda:datetime(2026,11,6,tzinfo=timezone.utc)+timedelta(seconds=self.now)
        p=fixture_plan('unit');c,self.source=validate_plan(p)
        log=RunLog(self.root,association_mode='automatic_stationary',purpose='bench_diagnostic',run_id='SOFTWARE_FIXTURE_UNIT',now=self.wall)
        rt=MissionRuntime(p,log,SimulatedMotion(),LineGuidance(c,positions=(-3,-1,1,3),deadband=.5),speed=100,clock=lambda:self.now)
        return log,rt
    def observation(self,seq=1,**kw):
        data=dict(observed_at=self.now,sequence=seq,source_id=self.source,line_values=(0,1,1,0),physical_pins=(15,18,16,11),line_trackable=True,zero_command=True,stationary_confirmed=False,control_healthy=True,emergency_stop=False,marker_id=None)
        data.update(kw);return Observation(**data)
    def test_replay_observation_fails_without_second_drive(self):
        log,rt=self.make_runtime();obs=self.observation();status=fixture_status(self.now,1,self.wall())
        rt.tick(obs,status,now=self.now);rt.tick(obs,status,now=self.now)
        self.assertEqual(rt.snapshot()['state'],'failed')
        self.assertEqual(sum(c['action']=='drive' for c in rt.motion.commands),1)
    def test_mission_id_reservation_and_interrupted_inspection(self):
        log,rt=self.make_runtime();self.assertTrue(inspect_run(log.directory)['interrupted'])
        log2=RunLog(self.root,association_mode='automatic_stationary',purpose='bench_diagnostic',run_id='SOFTWARE_FIXTURE_OTHER')
        with self.assertRaises(FileExistsError):MissionRuntime(rt.planner.plan,log2,SimulatedMotion(),rt.guidance,speed=100,clock=lambda:self.now)
        self.assertFalse(inspect_run(log.directory)['resume_allowed'])
    def test_write_failure_cannot_be_reported_as_completion_or_drive(self):
        log,rt=self.make_runtime()
        with patch.object(rt,'_persist',side_effect=LogPersistenceError('fixture disk full')):
            with self.assertRaises(LogPersistenceError):rt.tick(self.observation(),fixture_status(self.now,1,self.wall()),now=self.now)
        self.assertTrue(rt.motion.stopped);self.assertTrue(rt.fault)
        self.assertNotEqual(rt.snapshot()['state'],'completed')
    def test_passive_capture_rejects_stale_current_value_not_last_good(self):
        log,rt=self.make_runtime();status=fixture_status(self.now,1,self.wall())
        capture=PassiveCapture(log,rt.planner.plan.stations[0],status,now=self.now,barrier_clock=lambda:self.now)
        self.now+=.1;new=fixture_status(self.now,2,self.wall());new['sensors']['dht11'].update(value=None,last_good={'value':{'temperature_c':99,'humidity_pct':99}})
        self.assertEqual(capture.poll(new,now=self.now,timeout=2),'sensor_failure')
        self.assertEqual(log.records[capture.event]['Temperature_°C'],'')
    def test_passive_capture_cancellation_does_not_rewrite_committed_success(self):
        log,rt=self.make_runtime();capture=PassiveCapture(log,rt.planner.plan.stations[0],fixture_status(self.now,1,self.wall()),now=self.now,barrier_clock=lambda:self.now)
        self.now+=.1;capture.poll(fixture_status(self.now,2,self.wall()),now=self.now,timeout=2)
        self.assertEqual(capture.cancel('late_cancel'),'success')
        self.assertEqual(log.records[capture.event]['Status'],'success')
    def test_loop_rejects_zero_command_as_stationarity_evidence(self):
        log,rt=self.make_runtime();snapshot=fixture_status(self.now,1,self.wall())
        snapshot['host']['hostname']=self.source
        snapshot['sensors']['line_sensors']=dict(status='live',monotonic=self.now,age_seconds=0,value=[0,1,1,0],physical_pins=[15,18,16,11])
        gate=OrderedCrossings(['M1'],confirm_seconds=.2,confirm_frames=2,clear_frames=2,max_gap=.5)
        loop=MissionLoop(rt,lambda:snapshot,lambda now:dict(confirmed=True,sampled_at=now,source_id='zero_command'),gate,clock=lambda:self.now,wait=lambda _:None,period=.1,stationary_max_age=.2)
        result=loop.step();self.assertTrue(result['closed']);self.assertIn('independent_stationarity',result['fault'])
        self.assertFalse(any(c['action']=='drive' for c in rt.motion.commands))
    def test_full_snapshot_loop_with_independent_stationarity_signal(self):
        log,rt=self.make_runtime();seq=[0];following={};stopped_since=[None]
        def fetch():
            seq[0]+=1
            state=rt.planner.state;idx=rt.planner.index
            if state in ('idle','following'):
                following[idx]=following.get(idx,0)+1
                raw=[0,1,1,0] if following[idx]<=5 else [1,1,1,1]
                stopped_since[0]=None
            else:
                raw=[1,1,1,1]
                if stopped_since[0] is None:stopped_since[0]=self.now
            data=fixture_status(self.now,seq[0],self.wall())
            data['host']['hostname']=self.source
            data['control']['outputs']=[0]*4 if rt.motion.stopped else [100]*4
            data['control']['state']='stopped' if rt.motion.stopped else 'driving'
            data['sensors']['line_sensors']=dict(status='live',monotonic=self.now,age_seconds=0,value=raw,physical_pins=[15,18,16,11])
            return data
        stationary=lambda now:dict(confirmed=stopped_since[0] is not None and now-stopped_since[0]>=.25,sampled_at=now,source_id='SOFTWARE_FIXTURE_INDEPENDENT_MOTION_OBSERVER')
        gate=OrderedCrossings([s.marker_id for s in rt.planner.plan.stations],confirm_seconds=.2,confirm_frames=3,clear_frames=2,max_gap=.5)
        loop=MissionLoop(rt,fetch,stationary,gate,clock=lambda:self.now,wait=lambda _:None,period=.1,stationary_max_age=.2)
        for i in range(200):
            self.now=100+i*.1
            loop.step()
            if rt.closed:break
        self.assertEqual(rt.snapshot()['state'],'completed',rt.snapshot())
        self.assertEqual(log.manifest['status_counts'],{'success':2})
    def test_stale_stationarity_source_prevents_motion(self):
        log,rt=self.make_runtime();data=fixture_status(self.now,1,self.wall());data['host']['hostname']=self.source
        data['sensors']['line_sensors']=dict(status='live',monotonic=self.now,age_seconds=0,value=[0,1,1,0],physical_pins=[15,18,16,11])
        gate=OrderedCrossings(['M1'],confirm_seconds=.2,confirm_frames=2,clear_frames=2,max_gap=.5)
        loop=MissionLoop(rt,lambda:data,lambda now:dict(confirmed=True,sampled_at=now-10,source_id='SOFTWARE_FIXTURE_EXTERNAL'),gate,clock=lambda:self.now,wait=lambda _:None,period=.1,stationary_max_age=.2)
        self.assertIn('stale',loop.step()['fault']);self.assertFalse(any(c['action']=='drive' for c in rt.motion.commands))
    def test_pre_cancelled_loop_does_not_drive(self):
        log,rt=self.make_runtime();gate=OrderedCrossings(['M1'],confirm_seconds=.2,confirm_frames=2,clear_frames=2,max_gap=.5)
        loop=MissionLoop(rt,lambda:None,lambda now:None,gate,clock=lambda:self.now,wait=lambda _:None,period=.1,stationary_max_age=.2)
        event=threading.Event();event.set();loop.run(event)
        self.assertFalse(any(c['action']=='drive' for c in rt.motion.commands))


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        c,_=validate_plan(fixture_plan('guidance'));self.guide=LineGuidance(c,positions=(-3,-1,1,3),deadband=.5)
    def test_correct_direction_lost_disconnected_and_crossing(self):
        for raw,result in [([1,0,0,0],'forward_left'),([0,0,0,1],'forward_right'),([0,1,1,0],'forward')]:self.assertEqual(self.guide.classify(raw).direction,result)
        for raw,kind in [([0,0,0,0],'lost'),([1,0,0,1],'ambiguous'),([1,1,1,1],'crossing')]:self.assertEqual(self.guide.classify(raw).kind,kind)
    def test_polarity_and_order_are_not_assumed(self):
        c=dict(self.guide.calibration,white_levels=[1,1,1,1],black_levels=[0,0,0,0],physical_left_to_right_order=['X4','X3','X2','X1'])
        g=LineGuidance(c,positions=(-3,-1,1,3),deadband=.5)
        self.assertEqual(g.classify([1,1,1,0]).direction,'forward_left')
    def test_uncalibrated_and_invalid_values_rejected(self):
        with self.assertRaises(ValueError):LineGuidance(dict(self.guide.calibration,polarity_verified=False),positions=(-3,-1,1,3),deadband=.5)
        with self.assertRaises(ValueError):self.guide.classify([0,0,3,0])
    def test_crossing_bounce_continuous_mark_and_rearming(self):
        g=OrderedCrossings(['M1','M2'],confirm_seconds=.15,confirm_frames=2,clear_frames=2,max_gap=.5)
        self.assertIsNone(g.update(True,1));self.assertIsNone(g.update(False,1.1));self.assertIsNone(g.update(True,1.2))
        self.assertEqual(g.update(True,1.4),'M1');self.assertEqual(g.update(True,1.5),'M1')
        self.assertEqual(g.update(False,1.6),'M1');self.assertIsNone(g.update(False,1.7))
        self.assertIsNone(g.update(True,1.8));self.assertEqual(g.update(True,2.),'M2')
    def test_gap_replay_and_extra_marker_latch(self):
        g=OrderedCrossings(['M1'],confirm_seconds=.05,confirm_frames=2,clear_frames=1,max_gap=.5)
        g.update(True,1);g.update(True,1.1);g.update(False,1.2);g.update(True,1.3)
        with self.assertRaisesRegex(ValueError,'extra_crossing'):g.update(True,1.4)
        with self.assertRaises(ValueError):g.update(False,1.5)
        g=OrderedCrossings(['M1'],confirm_seconds=.05,confirm_frames=2,clear_frames=1,max_gap=.5);g.update(False,1)
        with self.assertRaises(ValueError):g.update(False,1)


class MotorAdapterTests(unittest.TestCase):
    def setUp(self):
        class FakeSerial:
            def __init__(self):self.packets=[]
            def send(self,packet):self.packets.append(packet)
            def close(self):pass
        self.now=10.;self.serial=FakeSerial();self.motor=MotorControl(self.serial,clock=lambda:self.now,mode='speed');self.addCleanup(self.motor.close)
    def test_motion_disabled_by_default_and_no_second_serial_owner(self):
        port=LeasedMotorPort(self.motor)
        with self.assertRaisesRegex(RuntimeError,'not_commissioned'):port.drive('forward',100)
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'],0)
    def test_enabled_fake_controller_reuses_lease_release_and_stop(self):
        port=LeasedMotorPort(self.motor,allow_motion=True);port.drive('forward',100)
        self.assertEqual(self.motor.snapshot()['outputs'],[100]*4)
        port.hold();self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        port.drive('forward_right',100);self.assertEqual(self.motor.snapshot()['outputs'],[100,100,20,20])
        port.close();self.assertFalse(self.motor.snapshot()['armed'])
    def test_manual_owner_conflict_and_expired_session_cannot_rearm(self):
        session=self.motor.command({'action':'arm'})['session'];port=LeasedMotorPort(self.motor,allow_motion=True)
        with self.assertRaises(ControlError):port.drive('forward',100)
        port.close();self.assertTrue(self.motor.snapshot()['armed'])
        self.motor.command({'action':'stop'});port.drive('forward',100);self.now+=.5
        with self.assertRaises(ControlError):port.drive('forward',100)
        self.assertFalse(self.motor.snapshot()['armed'])


if __name__=='__main__':unittest.main()
