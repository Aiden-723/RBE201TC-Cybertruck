"""Trial persistence and feedback tests use fake motor wire only, never hardware."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from line_trials import LineTrials
from basic_line_follower import BasicLineFollower
from motor_control import MotorControl
from server import create_server
from test_basic_line_follower import Clock, Feed
from test_basic_line_integration import Wire


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=LineTrials(self.tmp.name, {'fixture': True});self.addCleanup(self.store.close)
        self.clock=Clock();self.feed=Feed(self.clock);self.wire=Wire()
        self.motor=MotorControl(self.wire,clock=self.clock,mode='speed',startup_boost=True)
        self.follower=BasicLineFollower(self.motor,self.feed,clock=self.clock,threaded=False)
        self.addCleanup(self.motor.close);self.addCleanup(self.follower.close)
        self.follower.event_sink=self.store.observe
    def start(self):
        self.owner=self.follower.start(20);self.seq=0;self.follower.heartbeat(self.owner,0);return self.follower.tick()
    def step(self,raw):
        self.clock.now+=.03;self.feed.publish(raw);self.seq+=1
        self.follower.heartbeat(self.owner,self.seq);return self.follower.tick()
    def records(self):self.store.flush();return self.store.snapshot()['records']
    def test_full_run_beyond_trace40_records_commands_and_label_survives_restart(self):
        self.start()
        for _ in range(70):self.step([1,0,0,1])
        self.step([1,1,1,0]);self.follower.stop()
        records=self.records();self.assertEqual(len(records),1);r=records[0]
        self.assertEqual(r['status'],'finished');self.assertIsNone(r['result'])
        self.assertGreater(r['sample_count'],40);self.assertEqual(r['stop_reason'],'operator_stop')
        full=self.store.export()['trials'][0];self.assertGreater(len(full['events']),70)
        samples=[e for e in full['events'] if e['event']=='sample'];self.assertEqual(samples[0]['motor']['outputs'],[200]*4)
        self.assertEqual(samples[-1]['raw_values'],[1,1,1,0]);self.assertEqual(samples[-1]['values'],[0,1,1,1])
        self.assertEqual(samples[-1]['motor']['requested_outputs'],[-20,-20,20,20])
        self.assertNotIn(self.owner,json.dumps(full));self.assertNotIn(self.motor.session or 'NO_SESSION_MARKER',json.dumps(full))
        note='转到一半停下 <script>literal</script>'
        self.store.annotate(dict(trial_id=r['id'],category='custom',note=note))
        self.store.close();again=LineTrials(self.tmp.name);self.addCleanup(again.close)
        self.assertEqual(again.snapshot()['records'][0]['result']['note'],note)
        self.assertEqual(len(again.export()['trials'][0]['events']),len(full['events']))
    def test_automatic_failure_then_ui_stop_keeps_first_real_reason(self):
        self.start();self.step([1,1,1,1]);self.clock.now+=.201;self.feed.publish([1]*4)
        self.seq+=1;self.follower.heartbeat(self.owner,self.seq);self.follower.tick();self.follower.stop('operator_stop')
        record=self.records()[0];self.assertEqual(record['stop_reason'],'line_gap_timeout')
        self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
    def test_white_start_is_a_zero_motion_trial_and_repeated_manual_stop_adds_none(self):
        self.feed.item['value']=[1]*4;self.start();self.follower.stop();self.follower.stop()
        records=self.records();self.assertEqual(len(records),1);self.assertEqual(records[0]['stop_reason'],'line_lost')
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'],0)
    def test_each_start_has_separate_record_and_does_not_reuse_label(self):
        self.start();self.follower.stop();first=self.records()[0]
        self.store.annotate(dict(trial_id=first['id'],category='passed',note=''))
        self.clock.now+=1;self.feed.publish([1,0,0,1]);self.start();self.follower.stop()
        records=self.records();self.assertEqual(len(records),2)
        self.assertNotEqual(records[0]['id'],records[1]['id']);self.assertIsNone(records[0]['result'])
    def test_unlabelled_run_is_recovered_as_interrupted_without_inventing_a_stop(self):
        self.start();self.step([1,0,0,1]);self.records();self.store.close()
        again=LineTrials(self.tmp.name);self.addCleanup(again.close);r=again.snapshot()['records'][0]
        self.assertEqual(r['status'],'interrupted');self.assertEqual(r['stop_reason'],'service_restart')
        self.assertIsNone(r['ended_at']);self.assertGreater(r['duration_s'],0);self.assertGreater(r['sample_count'],0)
        self.assertGreater(len(again.export()['trials'][0]['events']),0)
    def test_empty_custom_invalid_category_and_running_annotation_are_rejected(self):
        self.start();r=self.records()[0]
        for category,note in [('custom','  '),('invention','x'),('passed','x'*2001),('passed','')]:
            with self.assertRaises(ValueError):self.store.annotate(dict(trial_id=r['id'],category=category,note=note))
        self.assertIsNone(self.records()[0]['result'])
    def test_slow_disk_writer_cannot_block_control_tick_or_stop(self):
        entered,release,finished=threading.Event(),threading.Event(),threading.Event()
        original=self.store._save
        def slow(record):entered.set();release.wait(2);original(record)
        self.store._save=slow
        def run():self.start();self.follower.stop();finished.set()
        worker=threading.Thread(target=run);worker.start()
        try:self.assertTrue(entered.wait(1));self.assertTrue(finished.wait(.2));self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        finally:release.set();worker.join(2)
        self.assertEqual(self.records()[0]['status'],'finished')

    def test_disk_failure_is_reported_without_changing_motor_control(self):
        def fail(record):raise OSError('SOFTWARE_FIXTURE_DISK_FULL')
        self.store._save=fail
        self.start();state=self.step([1,0,0,1]);self.assertTrue(state['running'])
        self.follower.stop();self.store.flush()
        self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        self.assertEqual(self.store.snapshot()['error'],'record_persistence_failed')



class TrialHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.server=create_server('127.0.0.1',0,trial_dir=self.tmp.name);self.server.token='fixture'
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01});self.thread.start();self.addCleanup(self.close)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def request(self,path,body=None,authorized=True,origin=True,key=True):
        c=http.client.HTTPConnection(*self.server.server_address,timeout=3);host='%s:%s'%self.server.server_address
        headers={'Authorization':'Bearer fixture'} if authorized else {}
        if body is not None:
            headers['Content-Type']='application/json'
            if origin:headers['Origin']='http://'+host
            if key:headers['X-Control-Key']=self.server.control_key
        c.request('POST' if body is not None else 'GET',path,body=None if body is None else json.dumps(body),headers=headers)
        r=c.getresponse();data=json.loads(r.read());status=r.status;c.close();return status,data
    def test_authenticated_annotation_and_export_never_invoke_driving_commands(self):
        self.assertEqual(self.request('/api/v1/line-trials',authorized=False)[0],401)
        store=self.server.trials;store.observe(dict(event='start_armed',monotonic=10));store.observe(dict(event='stop',monotonic=11,reason='operator_stop'));store.flush()
        record=self.request('/api/v1/line-trials')[1]['data']['records'][0]
        body=dict(trial_id=record['id'],category='underturn',note='约七十度后停住')
        self.assertEqual(self.request('/api/v1/line-trials',body,origin=False)[0],403)
        self.assertEqual(self.request('/api/v1/line-trials',body,key=False)[0],403)
        def forbidden(*args):raise AssertionError('Annotation must never drive')
        self.server.driving_mode.command=forbidden
        self.assertEqual(self.request('/api/v1/line-trials',body)[0],200)
        export=self.request('/api/v1/line-trials/export')[1]['data']
        self.assertEqual(export['trials'][0]['summary']['result']['note'],body['note'])
        self.assertNotIn(self.server.control_key,json.dumps(export));self.assertNotIn(self.server.token,json.dumps(export))

class FullTrialPipelineTests(unittest.TestCase):
    def test_real_http_start_stop_and_annotation_with_fake_motor_wire(self):
        import time
        class Sensors:
            directory='SOFTWARE_FIXTURE'
            def one(self,name):
                return dict(status='live',value=[1,0,0,1],age_seconds=0,monotonic=time.monotonic(),successes=1,physical_pins=[15,18,16,11])
        with tempfile.TemporaryDirectory() as tmp:
            wire=Wire();motor=MotorControl(wire,mode='speed',startup_boost=True)
            server=create_server('127.0.0.1',0,motor=motor,sensors=Sensors(),trial_dir=tmp)
            server.driving_mode.follower.threaded=False
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
            def request(path,data=None):
                c=http.client.HTTPConnection(*server.server_address,timeout=3);headers={}
                if data is not None:headers={'Origin':'http://%s:%s'%server.server_address,'X-Control-Key':server.control_key,'Content-Type':'application/json'}
                c.request('POST' if data is not None else 'GET',path,body=json.dumps(data) if data is not None else None,headers=headers)
                r=c.getresponse();out=json.loads(r.read());self.assertEqual(r.status,200,out);c.close();return out['data']
            try:
                request('/api/v1/control',dict(action='set_mode',mode='automatic'))
                owner=request('/api/v1/control',dict(action='autonomy_start',speed=20))['autonomy_session']
                request('/api/v1/control',dict(action='autonomy_heartbeat',autonomy_session=owner,seq=1));server.driving_mode.follower.tick()
                self.assertEqual(motor.snapshot()['outputs'],[200]*4)
                request('/api/v1/control',dict(action='stop'));server.trials.flush()
                record=request('/api/v1/line-trials')['records'][0];self.assertEqual(record['status'],'finished');self.assertIsNone(record['result'])
                request('/api/v1/line-trials',dict(trial_id=record['id'],category='passed',note='软件模拟，不是实车通过'))
                exported=request('/api/v1/line-trials/export')['trials'][0]
                self.assertEqual(exported['summary']['configuration']['speed'],20)
                self.assertEqual(exported['summary']['configuration']['channel_order'],['X4','X3','X2','X1'])
                self.assertEqual(exported['summary']['result']['category'],'passed');self.assertNotIn(owner,json.dumps(exported))
                self.assertEqual(motor.snapshot()['outputs'],[0]*4)
            finally:server.shutdown();server.server_close();thread.join(2);motor.close()
