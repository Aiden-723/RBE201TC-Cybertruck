import http.client
import json
import os
import select
import sys
import threading
import time
import unittest

from motor_control import ControlError, MotorControl, SerialPWM, mix
from server import create_server


class FakeSerial:
    def __init__(self):
        self.packets = []
        self.failed = False
        self.closed = False

    def send(self, packet):
        if self.failed:
            raise OSError('unplugged')
        self.packets.append(packet)

    def close(self):
        self.closed = True


class MotorTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.serial = FakeSerial()
        self.motor = MotorControl(self.serial, clock=lambda: self.now)
        self.addCleanup(self.motor.close)

    def arm(self):
        return self.motor.command({'action': 'arm', 'wheels_raised': True})['session']

    def drive(self, session, seq=0, direction='forward', speed=130):
        return self.motor.command(dict(action='drive', session=session, seq=seq, direction=direction, speed=speed))

    def test_no_motion_at_start_or_arm_and_differential_mixing(self):
        self.assertFalse(self.motor.snapshot()['armed'])
        self.arm()
        self.assertTrue(all(x in (b'$pwm:0,0,0,0#', b'$spd:0,0,0,0#', b'$stop#') for x in self.serial.packets))
        self.assertEqual(mix('forward', 130), (130, 130, 130, 130))
        self.assertEqual(mix('backward', 130), (-130, -130, -130, -130))
        self.assertEqual(mix('left', 130), (-130, -130, 130, 130))
        self.assertEqual(mix('right', 130), (130, 130, -130, -130))
        for i in range(4):
            self.assertEqual(mix('m'+str(i+1), 130), tuple(130 if j == i else 0 for j in range(4)))

    def test_release_stops_and_old_packets_cannot_restart(self):
        session = self.arm()
        self.drive(session)
        result = self.motor.command(dict(action='release', session=session, seq=2))
        self.assertTrue(result['armed'])
        self.assertEqual(result['pwm'], [0]*4)
        with self.assertRaises(ControlError):
            self.drive(session, seq=1)
        self.assertEqual(self.serial.packets[-1], b'$stop#')

    def test_expired_lease_invalidates_session_even_before_watchdog_tick(self):
        session = self.arm()
        self.drive(session)
        self.now += .451
        with self.assertRaises(ControlError):
            self.drive(session, seq=1)
        self.assertFalse(self.motor.snapshot()['armed'])
        self.assertEqual(self.serial.packets[-1], b'$stop#')
        self.assertEqual(self.motor.snapshot()['reason'], 'heartbeat_expired')

    def test_continuous_hold_has_no_duration_limit_and_release_stops(self):
        session = self.arm()
        for seq in range(1501):
            self.now = 10 + seq*.2
            self.drive(session, seq=seq)
        self.assertTrue(self.motor.snapshot()['armed'])
        self.assertEqual(self.motor.snapshot()['pwm'], [130]*4)
        self.assertIsNone(self.motor.snapshot()['max_hold_seconds'])
        result = self.motor.command(dict(action='release', session=session, seq=1501))
        self.assertTrue(result['armed'])
        self.assertEqual(result['pwm'], [0]*4)

    def test_global_stop_revokes_session_and_cannot_resume(self):
        session = self.arm()
        self.drive(session)
        self.motor.command({'action':'stop'})
        with self.assertRaises(ControlError):
            self.drive(session, seq=1)
        self.assertNotEqual(session, self.arm())

    def test_validation_exclusive_owner_and_speed_ceiling(self):
        for command in ({}, {'action':'arm','wheels_raised':False}, {'action':'stop','extra':1}):
            with self.assertRaises(ControlError):
                self.motor.command(command)
        session = self.arm()
        with self.assertRaises(ControlError):
            self.arm()
        for speed in (-1000, 0, 79, 221, 1000, True, 130.0, '130'):
            with self.assertRaises(ControlError):
                self.drive(session, speed=speed)
        self.assertEqual(self.motor.snapshot()['pwm'], [0]*4)

    def test_session_handshake_needs_no_manual_confirmation_and_diagnostics_count_writes(self):
        state = self.motor.command({'action':'arm'})
        self.assertTrue(state['armed'])
        self.assertEqual(state['drive_commands_sent'],0)
        self.drive(state['session'])
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'],1)
        self.motor.command({'action':'stop'})
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'],1)

    def test_speed_mode_uses_the_previously_verified_course_protocol(self):
        serial = FakeSerial()
        motor = MotorControl(serial, mode='speed')
        try:
            session = motor.command({'action':'arm'})['session']
            state = motor.command(dict(action='drive',session=session,seq=1,direction='left',speed=200))
            self.assertEqual(serial.packets[-1],b'$spd:-200,-200,200,200#')
            self.assertEqual(state['outputs'],[-200,-200,200,200])
            self.assertEqual(state['command_mode'],'speed')
            self.assertIsNone(state['pwm'])
            self.assertEqual(state['speed_setpoints'],state['outputs'])
        finally:
            motor.close()
    def test_serial_failure_latches_fault_and_no_automatic_recovery(self):
        session = self.arm()
        self.serial.failed = True
        with self.assertRaises(ControlError):
            self.drive(session)
        self.assertEqual(self.motor.snapshot()['state'], 'fault')
        self.serial.failed = False
        with self.assertRaises(ControlError):
            self.arm()

    def test_reversing_crosses_a_stop(self):
        session = self.arm()
        self.drive(session)
        before = len(self.serial.packets)
        self.drive(session, seq=1, direction='backward')
        self.assertEqual(self.serial.packets[before:], [b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#',b'$pwm:-130,-130,-130,-130#'])

    def test_watchdog_runs_independently_of_http_and_shutdown_stops(self):
        serial = FakeSerial()
        motor = MotorControl(serial)
        try:
            motor.lease_seconds = .05
            motor.start()
            session = motor.command({'action':'arm','wheels_raised':True})['session']
            motor.command(dict(action='drive',session=session,seq=0,direction='forward',speed=130))
            time.sleep(.12)
            self.assertEqual(serial.packets[-1], b'$stop#')
            self.assertFalse(motor.snapshot()['armed'])
        finally:
            motor.close()
        self.assertTrue(serial.closed)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux serial transport test')
    def test_real_serial_transport_on_virtual_port_and_exclusive_lock(self):
        # Pseudo terminal only: never open /dev/ttyUSB* during tests.
        master, slave = os.openpty()
        transport = None
        try:
            port = os.ttyname(slave)
            transport = SerialPWM(port)
            transport.send(b'$pwm:0,0,0,130#')
            self.assertTrue(select.select([master], [], [], .5)[0])
            self.assertEqual(os.read(master, 100), b'$pwm:0,0,0,130#')
            with self.assertRaises(OSError):
                SerialPWM(port)
            transport.send(b'$stop#')
            self.assertTrue(select.select([master], [], [], .5)[0])
            self.assertEqual(os.read(master, 100), b'$stop#')
        finally:
            if transport:
                transport.close()
            os.close(master)
            os.close(slave)


class ControlHTTPTests(unittest.TestCase):
    def setUp(self):
        self.serial = FakeSerial()
        self.motor = MotorControl(self.serial)
        self.motor.start()
        self.server = create_server('127.0.0.1', 0, motor=self.motor)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.motor.close()

    def request(self, data=None, headers=None, path='/api/v1/control', raw=None):
        host, port = self.server.server_address
        h = {'Origin':f'http://{host}:{port}', 'Content-Type':'application/json','X-Control-Key':self.server.control_key}
        h.update(headers or {})
        client = http.client.HTTPConnection(host, port, timeout=3)
        try:
            client.request('POST' if data is not None or raw is not None else 'GET', path,
                           body=raw if raw is not None else json.dumps(data) if data is not None else None, headers=h)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_csrf_auth_host_and_strict_json(self):
        for headers in ({'Origin':'http://evil.test'}, {'X-Control-Key':'wrong'}, {'Host':'evil.test:8765'}, {'Content-Type':'text/plain'}):
            self.assertIn(self.request({'action':'arm','wheels_raised':True}, headers)[0], (400,403))
        for raw in ('[1]', 'null', '"text"', '{', '{}'):
            self.assertIn(self.request(raw=raw)[0], (400,409))
        self.server.token = 'test-token'
        self.assertEqual(self.request({'action':'stop'})[0],401)
        self.assertEqual(self.request({'action':'stop'},{'Authorization':'Bearer test-token'})[0],200)

    def test_real_http_motor_flow_with_fake_serial_only(self):
        status, body = self.request()
        self.assertEqual(status,200)
        self.assertFalse(body['data']['armed'])
        _, body = self.request({'action':'arm','wheels_raised':True})
        session = body['data']['session']
        status, body = self.request(dict(action='drive',session=session,seq=1,direction='left',speed=130))
        self.assertEqual(status,200)
        self.assertEqual(body['data']['pwm'],[-130,-130,130,130])
        self.assertNotIn('session', self.request()[1]['data'])
        self.assertEqual(self.request({'action':'stop'})[0],200)
        self.assertEqual(self.request(dict(action='drive',session=session,seq=2,direction='forward',speed=130))[0],409)
        self.assertEqual(self.request({},path='/api/v1/status')[0],405)

    def test_http_stall_does_not_hold_motors_on(self):
        import socket
        self.motor.lease_seconds = .05
        _, body = self.request({'action':'arm','wheels_raised':True})
        self.request(dict(action='drive',session=body['data']['session'],seq=1,direction='forward',speed=130))
        with socket.create_connection(self.server.server_address, timeout=2) as sock:
            sock.sendall(b'POST /api/v1/control HTTP/1.1\r\n')
            time.sleep(.15)
            self.assertEqual(self.serial.packets[-1],b'$stop#')
            self.assertFalse(self.motor.snapshot()['armed'])


class CombinedSteeringTests(unittest.TestCase):
    def test_arc_stops_inner_side_and_bounds_outer_side_in_travel_direction(self):
        self.assertEqual(mix('forward_right',200),(200,200,0,0))
        self.assertEqual(mix('forward_left',200),(0,0,200,200))
        self.assertEqual(mix('backward_right',200),(0,0,-200,-200))
        self.assertEqual(mix('backward_left',200),(-200,-200,0,0))
        for speed in (80,81,200,219,220):
            for direction in ('forward_right','forward_left','backward_right','backward_left'):
                values=mix(direction,speed)
                self.assertTrue(all(type(x) is int and 0<=abs(x)<=speed for x in values))
                self.assertEqual(values.count(0),2)
                sign=1 if direction.startswith('forward') else -1
                self.assertEqual([v for v in values if v],[sign*speed]*2)

    def test_arc_straight_transition_and_release_use_real_speed_packet_format(self):
        serial=FakeSerial();motor=MotorControl(serial,clock=lambda:10.0,mode='speed')
        self.addCleanup(motor.close)
        session=motor.command({'action':'arm'})['session'];serial.packets.clear()
        for seq,direction in enumerate(('forward','forward_right','forward')):
            motor.command({'action':'drive','session':session,'seq':seq,'direction':direction,'speed':200})
        self.assertEqual(serial.packets,[b'$spd:200,200,200,200#',b'$spd:200,200,0,0#',b'$spd:200,200,200,200#'])
        motor.command({'action':'release','session':session,'seq':3})
        self.assertEqual(motor.snapshot()['outputs'],[0]*4)
        with self.assertRaises(ControlError):
            motor.command({'action':'drive','session':session,'seq':2,'direction':'forward_right','speed':200})


class StartupBoostTests(unittest.TestCase):
    def setUp(self):
        self.now=10.0; self.wire=FakeSerial()
        self.motor=MotorControl(self.wire,clock=lambda:self.now,mode='speed',startup_boost=True)
        self.addCleanup(self.motor.close)
        self.owner=self.motor.command({'action':'arm'})['session']

    def drive(self,seq=0,direction='forward',speed=80):
        return self.motor.command(dict(action='drive',session=self.owner,seq=seq,direction=direction,speed=speed))

    def test_each_new_action_boosts_signed_nonzero_channels_then_returns_to_target(self):
        directions=('forward','backward','left','right','forward_right','forward_left',
                    'backward_right','backward_left','m1','m2','m3','m4')
        for direction in directions:
            self.motor.command({'action':'stop'});self.owner=self.motor.command({'action':'arm'})['session']
            with self.subTest(direction=direction):
                targets=mix(direction,80);boost=tuple(200 if v>0 else -200 if v<0 else 0 for v in targets)
                state=self.drive(direction=direction)
                self.assertEqual(state['outputs'],list(boost));self.assertEqual(state['requested_outputs'],list(targets))
                self.assertTrue(state['startup_boost_active'])
                self.now+=.199;self.motor.check_deadline();self.assertEqual(self.motor.snapshot()['outputs'],list(boost))
                self.now+=.002;self.motor.check_deadline();state=self.motor.snapshot()
                self.assertEqual(state['outputs'],list(targets));self.assertFalse(state['startup_boost_active'])

    def test_identical_manual_heartbeats_do_not_restart_boost(self):
        self.drive();until=self.motor._boost_until
        for seq in range(1,4):
            self.now+=.05;self.drive(seq);self.assertEqual(self.motor._boost_until,until)
        self.now=until+.001;self.motor.check_deadline()
        self.assertEqual(self.motor.snapshot()['outputs'],[80]*4)
        self.now+=.02;self.drive(4);self.assertIsNone(self.motor._boost_until)
        self.assertEqual(self.motor.snapshot()['outputs'],[80]*4)

    def test_stepdown_does_not_renew_lease_and_expiry_has_priority(self):
        self.drive();lease=self.motor.deadline
        self.now+=.201;self.motor.check_deadline()
        self.assertEqual(self.motor.deadline,lease);self.assertEqual(self.motor.snapshot()['outputs'],[80]*4)
        self.now=lease;self.motor.check_deadline();self.assertFalse(self.motor.snapshot()['armed'])
        self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        self.owner=self.motor.command({'action':'arm'})['session'];self.motor.lease_seconds=.05
        self.drive();before=len(self.wire.packets);self.now+=.201;self.motor.check_deadline()
        self.assertEqual(self.wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'])

    def test_release_and_stop_cancel_boost_without_a_late_target(self):
        for action in ('release','stop'):
            with self.subTest(action=action):
                self.motor.command({'action':'stop'});self.owner=self.motor.command({'action':'arm'})['session']
                self.drive();self.now+=.03
                request={'action':'stop'} if action=='stop' else dict(action='release',session=self.owner,seq=1)
                state=self.motor.command(request);before=len(self.wire.packets)
                self.assertEqual(state['outputs'],[0]*4);self.assertFalse(state['startup_boost_active'])
                self.now+=.3;self.motor.check_deadline();self.assertEqual(len(self.wire.packets),before)
                self.assertEqual(self.motor.snapshot()['requested_outputs'],[0]*4)

    def test_auto_changed_direction_restarts_from_new_target_without_old_timer_replay(self):
        state=self.motor.drive_sides(self.owner,0,50,0)
        self.assertEqual(state['outputs'],[200,200,0,0]);self.now+=.08
        before=len(self.wire.packets);state=self.motor.drive_sides(self.owner,1,-50,50)
        self.assertEqual(self.wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:-200,-200,200,200#'])
        until=self.motor._boost_until
        for seq in range(2,5):
            self.now+=.05;self.motor.drive_sides(self.owner,seq,-50,50)
            self.assertEqual(self.motor._boost_until,until)
        self.now=until+.001;self.motor.check_deadline()
        self.assertEqual(self.motor.snapshot()['outputs'],[-50,-50,50,50])
        self.assertEqual(self.wire.packets[-1],b'$spd:-50,-50,50,50#')

    def test_requested_speed_change_is_new_action_and_zero_never_boosts(self):
        self.drive();self.now+=.201;self.motor.check_deadline()
        state=self.drive(1,speed=220);self.assertEqual(state['outputs'],[200]*4)
        self.now+=.201;self.motor.check_deadline();self.assertEqual(self.motor.snapshot()['outputs'],[220]*4)
        state=self.motor.drive_sides(self.owner,2,0,0)
        self.assertEqual(state['outputs'],[0]*4);self.assertFalse(state['startup_boost_active'])
        before=len(self.wire.packets);self.now+=.03;self.motor.check_deadline()
        self.assertEqual(len(self.wire.packets),before)

    def test_failure_during_timed_stepdown_latches_stop_without_killing_watchdog(self):
        self.drive();self.wire.failed=True;self.now+=.201;self.motor.check_deadline()
        self.assertFalse(self.motor.snapshot()['armed']);self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        self.assertEqual(self.motor.snapshot()['fault'],'serial_stop_failed')
        self.assertIsNone(self.motor._boost_until)

    def test_mode_change_and_close_prevent_late_boost_output(self):
        from driving_mode import DrivingMode
        mode=DrivingMode(self.motor)
        self.drive();mode.command(dict(action='set_mode',mode='automatic'))
        before=len(self.wire.packets);self.now+=.3;self.motor.check_deadline()
        self.assertEqual(len(self.wire.packets),before);self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        mode.command(dict(action='set_mode',mode='manual'))
        self.owner=self.motor.command({'action':'arm'})['session'];self.drive();self.motor.close()
        before=len(self.wire.packets);self.now+=.3;self.motor.check_deadline()
        self.assertEqual(len(self.wire.packets),before)

    def test_production_watchdog_performs_stepdown_without_another_http_request(self):
        class NotifyingWire(FakeSerial):
            def __init__(self):super().__init__();self.lowered=threading.Event()
            def send(self,packet):
                super().send(packet)
                if packet==b'$spd:80,80,80,80#':self.lowered.set()
        wire=NotifyingWire();motor=MotorControl(wire,mode='speed',startup_boost=True)
        self.addCleanup(motor.close);motor.start();owner=motor.command({'action':'arm'})['session']
        started=time.monotonic();motor.command(dict(action='drive',session=owner,seq=0,direction='forward',speed=80))
        self.assertLess(time.monotonic()-started,.1)
        self.assertTrue(wire.lowered.wait(.4))
        self.assertGreaterEqual(time.monotonic()-started,.19)
        self.assertEqual(motor.snapshot()['outputs'],[80]*4)
        motor.command({'action':'stop'})

    def test_real_follower_boost_stays_bounded_during_short_correction_gap(self):
        from basic_line_follower import BasicLineFollower
        from test_basic_line_follower import Feed
        self.motor.command({'action':'stop'})
        feed=Feed(lambda:self.now)
        follower=BasicLineFollower(self.motor,feed,clock=lambda:self.now,threaded=False,channel_permutation=(0,1,2,3))
        self.addCleanup(follower.close)
        owner=follower.start(20);follower.heartbeat(owner,0);follower.tick()
        self.assertEqual(self.motor.snapshot()['outputs'],[200]*4)
        self.assertEqual(self.motor.snapshot()['requested_outputs'],[20]*4)
        until=self.motor._boost_until
        for seq in range(1,4):
            self.now+=.05;feed.publish([1,0,0,1]);follower.heartbeat(owner,seq);follower.tick()
            self.assertEqual(self.motor._boost_until,until)
        self.now=until+.001;self.motor.check_deadline()
        self.assertEqual(self.motor.snapshot()['outputs'],[20]*4)
        self.now+=.03;feed.publish([1,1,0,1]);follower.heartbeat(owner,4);follower.tick()
        self.assertEqual(self.motor.snapshot()['outputs'],[20,20,12,12])
        until=self.motor._boost_until
        self.now+=.036;feed.publish([1,1,1,1]);follower.heartbeat(owner,5)
        state=follower.tick();self.assertTrue(state['running'])
        self.assertEqual(self.motor.snapshot()['requested_outputs'],[20,20,12,12])
        self.assertEqual(self.motor._boost_until,until)  # WHITE never starts another boost.
        self.assertEqual(self.motor.snapshot()['outputs'],[20,20,12,12])
        self.now+=.17;self.motor.check_deadline()
        self.assertEqual(self.motor.snapshot()['outputs'],[20,20,12,12])
        self.now+=.04;feed.publish([1,1,1,1]);follower.heartbeat(owner,6)
        state=follower.tick();self.assertFalse(state['running'])
        self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        before=len(self.wire.packets);self.now+=.3;self.motor.check_deadline()
        self.assertEqual(len(self.wire.packets),before)


    def test_forward_to_inner_pause_clears_old_output_without_restarting_same_action(self):
        self.motor.drive_sides(self.owner,0,50,50);self.now+=.05
        before=len(self.wire.packets);state=self.motor.drive_sides(self.owner,1,50,0)
        self.assertEqual(self.wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:200,200,0,0#'])
        self.assertEqual(state['speed_transition_resets'],1)
        until=self.motor._boost_until;before=len(self.wire.packets)
        self.now+=.03;self.motor.drive_sides(self.owner,2,50,0)
        self.assertEqual(self.wire.packets[before:],[b'$spd:200,200,0,0#'])
        self.assertEqual(self.motor._boost_until,until)
        self.now=until+.001;before=len(self.wire.packets);self.motor.check_deadline()
        self.assertEqual(self.wire.packets[before:],[b'$spd:50,50,0,0#'])
        self.assertEqual(self.motor.snapshot()['speed_transition_resets'],1)

    def test_forward_to_pivot_clears_old_output_before_counterrotation_and_keeps_owner(self):
        self.motor.drive_sides(self.owner,0,50,50);before=len(self.wire.packets)
        self.now+=.03;state=self.motor.drive_sides(self.owner,1,50,-50)
        self.assertEqual(self.wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:200,200,-200,-200#'])
        self.assertEqual(self.motor.session,self.owner);self.assertEqual(state['requested_outputs'],[50,50,-50,-50])
        self.assertEqual(state['speed_transition_resets'],1)

    def test_same_direction_boost_stepdown_preserves_the_existing_startup_improvement(self):
        self.drive();before=len(self.wire.packets);self.now+=.201;self.motor.check_deadline()
        self.assertEqual(self.wire.packets[before:],[b'$spd:80,80,80,80#'])
        before=len(self.wire.packets);self.now+=.03;self.drive(1,speed=120)
        self.assertEqual(self.wire.packets[before:],[b'$spd:200,200,200,200#'])
        self.assertEqual(self.motor.snapshot()['speed_transition_resets'],0)

    def test_failed_transition_clear_never_sends_the_requested_nonzero_turn(self):
        class FailOnceClearWire(FakeSerial):
            fail_clear=False
            def send(self,packet):
                if self.fail_clear and packet==b'$pwm:0,0,0,0#':
                    self.fail_clear=False;raise OSError('SOFTWARE_FIXTURE_CLEAR_FAILED')
                super().send(packet)
        wire=FailOnceClearWire();motor=MotorControl(wire,mode='speed',startup_boost=True)
        self.addCleanup(motor.close);owner=motor.command({'action':'arm'})['session']
        motor.drive_sides(owner,0,50,50);before=len(wire.packets);wire.fail_clear=True
        with self.assertRaises(ControlError):motor.drive_sides(owner,1,50,-50)
        self.assertEqual(wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'])
        self.assertFalse(motor.snapshot()['armed']);self.assertEqual(motor.snapshot()['outputs'],[0]*4)
        self.assertFalse(motor.snapshot()['startup_boost_active'])


class PrecisionAngleOutputTests(unittest.TestCase):
    def setUp(self):
        self.now=10.;self.wire=FakeSerial()
        self.motor=MotorControl(self.wire,clock=lambda:self.now,mode='speed',startup_boost=True)
        self.addCleanup(self.motor.close)
        self.owner=self.motor.command({'action':'arm'})['session']

    def test_fine_start_and_reversal_never_get_200_assist(self):
        state=self.motor.drive_sides(self.owner,0,8,-8,precision=True)
        self.assertEqual(state['outputs'],[8,8,-8,-8]);self.assertFalse(state['startup_boost_active'])
        self.now+=.03;state=self.motor.drive_sides(self.owner,1,-6,6,precision=True)
        self.assertEqual(state['outputs'],[-6,-6,6,6]);self.assertFalse(state['startup_boost_active'])
        self.assertNotIn(b'$spd:-200,-200,200,200#',self.wire.packets)
        self.assertNotIn(b'$spd:200,200,-200,-200#',self.wire.packets)

    def test_precision_brake_cancels_existing_assist_and_cannot_replay_it(self):
        self.motor.drive_sides(self.owner,0,-20,20)
        self.assertTrue(self.motor.snapshot()['startup_boost_active'])
        self.now+=.03;self.motor.drive_sides(self.owner,1,0,0,precision=True)
        before=len(self.wire.packets);self.now+=.21;self.motor.check_deadline()
        self.assertEqual(len(self.wire.packets),before);self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
        self.motor.drive_sides(self.owner,2,5,-5,precision=True)
        self.assertEqual(self.motor.snapshot()['outputs'],[5,5,-5,-5])

    def test_manual_and_normal_autonomy_still_keep_the_requested_assist(self):
        state=self.motor.drive_sides(self.owner,0,-20,20)
        self.assertEqual(state['outputs'],[-200,-200,200,200])
        self.motor.command({'action':'stop'});owner=self.motor.command({'action':'arm'})['session']
        state=self.motor.command(dict(action='drive',session=owner,seq=0,direction='forward',speed=80))
        self.assertEqual(state['outputs'],[200]*4)

    def test_precision_still_requires_a_valid_session_sequence_and_lease(self):
        self.motor.drive_sides(self.owner,0,5,-5,precision=True)
        with self.assertRaises(ControlError):self.motor.drive_sides('wrong',1,5,-5,precision=True)
        with self.assertRaises(ControlError):self.motor.drive_sides(self.owner,0,5,-5,precision=True)
        self.now+=.451
        with self.assertRaises(ControlError):self.motor.drive_sides(self.owner,1,5,-5,precision=True)
        self.assertFalse(self.motor.snapshot()['armed']);self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
