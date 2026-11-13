import http.client
import json
import threading
import unittest
from driving_mode import DrivingMode
from motor_control import MotorControl, ControlError
from server import create_server

class Wire:
    def __init__(self): self.packets=[];self.fail=False
    def send(self,packet):
        if self.fail: raise OSError('simulated disconnect')
        self.packets.append(packet)
    def close(self): pass

class ModeTests(unittest.TestCase):
    def setUp(self):
        self.wire=Wire();self.motor=MotorControl(self.wire,mode='speed');self.mode=DrivingMode(self.motor)
        self.addCleanup(self.motor.close)
    def drive(self):
        session=self.mode.command({'action':'arm'})['session']
        self.mode.command(dict(action='drive',session=session,seq=1,direction='forward',speed=200))
        return session
    def test_switch_stops_and_revokes_before_ack(self):
        session=self.drive();s=self.mode.command({'action':'set_mode','mode':'automatic'})
        self.assertEqual(s['outputs'],[0]*4);self.assertFalse(s['armed']);self.assertFalse(s['autonomy']['ready'])
        for data in [{'action':'arm'},dict(action='drive',session=session,seq=2,direction='forward',speed=200)]:
            with self.assertRaisesRegex(ControlError,'manual_mode_required'):self.mode.command(data)
        self.mode.command({'action':'set_mode','mode':'manual'})
        with self.assertRaisesRegex(ControlError,'not_armed'):
            self.mode.command(dict(action='drive',session=session,seq=3,direction='forward',speed=200))
        self.assertEqual(self.motor.snapshot()['outputs'],[0]*4);self.drive()
    def test_same_mode_is_idempotent(self):
        self.drive();self.assertEqual(self.mode.command({'action':'set_mode','mode':'manual'})['outputs'],[200]*4)
    def test_no_executor_and_stop_always_works(self):
        self.mode.command({'action':'set_mode','mode':'automatic'});count=len(self.wire.packets)
        with self.assertRaisesRegex(ControlError,'autonomy_not_configured'):self.mode.command({'action':'autonomy_start'})
        self.assertEqual(len(self.wire.packets),count)
        self.assertEqual(self.mode.command({'action':'stop'})['driving_mode'],'automatic')
    def test_stop_failure_rejects_switch(self):
        self.drive();self.wire.fail=True
        with self.assertRaisesRegex(ControlError,'mode_stop_failed'):self.mode.command({'action':'set_mode','mode':'automatic'})
        self.assertEqual(self.mode.snapshot()['driving_mode'],'manual');self.assertIsNotNone(self.mode.snapshot()['fault'])
    def test_selection_without_motor_never_claims_ready(self):
        m=DrivingMode();s=m.command({'action':'set_mode','mode':'automatic'})
        self.assertFalse(s['enabled']);self.assertFalse(s['autonomy']['ready']);self.assertEqual(m.command({'action':'stop'})['outputs'],[0]*4)
        m.command({'action':'set_mode','mode':'manual'})
        with self.assertRaisesRegex(ControlError,'motor_unavailable'):m.command({'action':'arm'})
    def test_invalid_modes_are_rejected(self):
        for v in ['auto',None,1,True,[],{}]:
            with self.assertRaises(ValueError):self.mode.command({'action':'set_mode','mode':v})
        with self.assertRaises(ValueError):self.mode.command({'action':'set_mode','mode':'automatic','extra':True})
        self.assertEqual(self.mode.snapshot()['driving_mode'],'manual')
    def test_manual_packet_queued_behind_switch_cannot_drive(self):
        session=self.drive();entered=threading.Event();proceed=threading.Event();errors=[];original=self.motor.command
        def stop(data):
            if data['action']=='stop':entered.set();proceed.wait(2)
            return original(data)
        self.motor.command=stop
        switch=threading.Thread(target=lambda:self.mode.command({'action':'set_mode','mode':'automatic'}));switch.start()
        self.assertTrue(entered.wait(2))
        def stale():
            try:self.mode.command(dict(action='drive',session=session,seq=2,direction='forward',speed=200))
            except ControlError as exc:errors.append(str(exc))
        request=threading.Thread(target=stale);request.start();proceed.set();switch.join(2);request.join(2)
        self.assertEqual(errors,['manual_mode_required']);self.assertEqual(self.motor.snapshot()['drive_commands_sent'],1)

class ModeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server=create_server('127.0.0.1',0);self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start();self.addCleanup(self.close)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def request(self,data=None,headers=None):
        host,port=self.server.server_address
        h={'Origin':f'http://{host}:{port}','Content-Type':'application/json','X-Control-Key':self.server.control_key};h.update(headers or {})
        c=http.client.HTTPConnection(host,port,timeout=3)
        try:
            c.request('POST' if data else 'GET','/api/v1/control',json.dumps(data) if data else None,h)
            r=c.getresponse();return r.status,json.loads(r.read())
        finally:c.close()
    def test_mode_shared_without_motor(self):
        self.assertEqual(self.request({'action':'set_mode','mode':'automatic'})[0],200)
        self.assertEqual(self.request()[1]['data']['driving_mode'],'automatic');self.assertEqual(self.request({'action':'arm'})[0],409)
        self.assertEqual(self.request({'action':'set_mode','mode':'manual'})[0],200)
    def test_auth_and_origin_protection(self):
        command={'action':'set_mode','mode':'automatic'}
        for h in [{'Origin':'http://other.test'},{'X-Control-Key':'wrong'},{'Host':'other.test:8765'}]:self.assertEqual(self.request(command,h)[0],403)
        self.server.token='local-test';self.assertEqual(self.request(command)[0],401)
        self.assertEqual(self.request(command,{'Authorization':'Bearer local-test'})[0],200)
    def test_invalid_request_no_side_effect(self):
        self.assertEqual(self.request({'action':'set_mode','mode':'typo'})[0],400)
        self.assertEqual(self.request()[1]['data']['driving_mode'],'manual')
