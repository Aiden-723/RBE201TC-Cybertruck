"""Mode/HTTP integration with fake wire and synthetic snapshots, no hardware."""
import copy
import http.client
import json
import threading
import time
import unittest
from basic_line_follower import BasicLineFollower
from driving_mode import DrivingMode
from motor_control import MotorControl, ControlError
from server import create_server


class Wire:
    def __init__(self): self.packets = []
    def send(self, packet): self.packets.append(packet)
    def close(self): pass


class BasicLineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.wire = Wire()
        self.motor = MotorControl(self.wire, clock=lambda: self.now, mode='speed')
        self.sample = dict(status='live', value=[1, 0, 0, 1], monotonic=self.now,
                           age_seconds=0, successes=1, physical_pins=[15, 18, 16, 11])
        # Legacy synthetic fixture values are already physical left-to-right.
        self.follower = BasicLineFollower(self.motor, lambda: copy.deepcopy(self.sample), clock=lambda: self.now, threaded=False, channel_permutation=(0,1,2,3))
        self.mode = DrivingMode(self.motor, follower=self.follower)
        self.addCleanup(self.motor.close)
        self.addCleanup(self.mode.close)
    def start(self):
        self.mode.command(dict(action='set_mode', mode='automatic'))
        return self.mode.command(dict(action='autonomy_start'))['autonomy_session']
    def heartbeat(self, token, seq=1):
        return self.mode.command(dict(action='autonomy_heartbeat', autonomy_session=token, seq=seq))
    def test_white_after_forward_or_correction_retains_actual_wire_pair_then_times_out(self):
        for values in ([1,0,0,1],[1,0,1,1],[1,1,0,1],[0,1,1,1],[1,1,1,0]):
            with self.subTest(values=values):
                self.sample.update(value=values,monotonic=self.now,successes=self.sample['successes']+1)
                token=self.start();self.heartbeat(token);self.follower.tick()
                expected=self.motor.snapshot()['outputs'];before=len(self.wire.packets)
                state=self.line_step(token,[1,1,1,1],dt=.036)
                self.assertTrue(state['running']);self.assertEqual(self.motor.snapshot()['outputs'],expected)
                self.assertEqual(self.wire.packets[before:],[('$spd:'+','.join(map(str,expected))+'#').encode()])
                before=len(self.wire.packets);limit=.4 if expected[0]*expected[2]<0 else .2
                state=self.line_step(token,[1,1,1,1],dt=limit+.001)
                self.assertEqual(state['reason'],'line_gap_timeout');self.assertFalse(state['running'])
                self.assertFalse(self.motor.snapshot()['armed']);self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
                self.assertEqual(self.wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'])
                before=len(self.wire.packets);self.sample.update(value=[1,0,0,1],successes=self.sample['successes']+1)
                self.follower.tick();self.assertEqual(len(self.wire.packets),before)
                with self.assertRaises(ValueError):self.heartbeat(token,999999)

    def test_explicit_speed_84_and_continuous_corrections_keep_safety_guards(self):
        self.mode.command(dict(action='set_mode', mode='automatic'))
        started = self.mode.command(dict(action='autonomy_start', speed=84))
        token = started['autonomy_session']
        self.assertEqual(started['autonomy']['speed'], 20)
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'], 0)
        self.heartbeat(token); self.follower.tick()
        self.assertEqual(self.motor.snapshot()['outputs'], [20]*4)
        for seq, values, expected in ((2,[1,1,0,1],[20,20,12,12]), (3,[1,0,0,0],[20,20,-20,-20])):
            self.now += .05
            self.sample.update(value=values, monotonic=self.now, successes=seq)
            self.heartbeat(token,seq); self.follower.tick()
            self.assertEqual(self.motor.snapshot()['outputs'], expected)
        self.assertEqual(self.follower.snapshot()['line_loss_limit_s'], 1.4)
        self.assertEqual(self.follower.snapshot()['period_s'], .02)


    def test_manual_arm_keeps_session_and_original_packet(self):
        owner = self.mode.command(dict(action='arm'))['session']
        state = self.mode.command(dict(action='drive', session=owner, seq=0, direction='forward', speed=200))
        self.assertEqual(state['outputs'], [200]*4)
    def test_selection_and_start_do_not_drive_before_heartbeat(self):
        token = self.start()
        self.follower.tick()
        self.assertTrue(token)
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'], 0)
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)
        self.assertNotIn('autonomy_session', self.mode.snapshot())
        self.assertNotIn('session', self.mode.snapshot())
        self.heartbeat(token)
        self.follower.tick()
        self.assertEqual(self.motor.snapshot()['outputs'], [20]*4)

    def test_start_in_manual_is_rejected(self):
        with self.assertRaisesRegex(ControlError, 'automatic_mode_required'):
            self.mode.command(dict(action='autonomy_start'))
        self.assertFalse(self.motor.snapshot()['armed'])
    def test_left_and_right_outer_probes_give_opposite_pivots(self):
        for values, expected in (([0, 1, 1, 1], [-20, -20, 20, 20]), ([1, 1, 1, 0], [20, 20, -20, -20])):
            with self.subTest(values=values):
                self.sample['value'] = values
                token = self.start()
                self.heartbeat(token)
                self.follower.tick()
                self.assertEqual(self.motor.snapshot()['outputs'], expected)
                self.mode.command(dict(action='stop'))

    def test_manual_requests_cannot_override_automatic(self):
        self.start()
        for action in ({'action': 'arm'}, dict(action='drive', session='old', seq=2, direction='forward', speed=200)):
            with self.assertRaisesRegex(ControlError, 'manual_mode_required'):
                self.mode.command(action)
    def test_mode_change_stops_and_revokes_both_owners(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.mode.command(dict(action='set_mode', mode='manual'))
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)
        self.assertFalse(self.motor.snapshot()['armed'])
        with self.assertRaises(ControlError): self.heartbeat(token, 2)
        before = self.motor.snapshot()['drive_commands_sent']; self.follower.tick()
        self.assertEqual(before, self.motor.snapshot()['drive_commands_sent'])
        self.mode.command(dict(action='arm'))

    def test_stop_discards_inflight_sensor_read_even_after_mode_switch(self):
        token = self.start(); self.heartbeat(token)
        entered, release = threading.Event(), threading.Event()
        def blocked():
            entered.set(); release.wait(2); return copy.deepcopy(self.sample)
        self.follower.line_source = blocked
        worker = threading.Thread(target=self.follower.tick); worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.mode.command(dict(action='set_mode', mode='manual'))
        finally:
            release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'], 0)
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)

    def test_all_black_preserves_forward_and_continues_past_old_ambiguity_timeout(self):
        token=self.start();self.heartbeat(token);self.follower.tick()
        for _ in range(80):
            state=self.line_step(token,[0,0,0,0])
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'],[20]*4)
        self.mode.command(dict(action='stop'));before=self.motor.snapshot()['drive_commands_sent']
        self.sample.update(value=[1,0,0,1],successes=self.sample['successes']+1);self.follower.tick()
        self.assertEqual(before,self.motor.snapshot()['drive_commands_sent'])
        with self.assertRaises(ValueError):self.heartbeat(token,1000)

    def test_sensor_expiry_stops_before_motor_lease_expiry(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.now += .401; self.sample['age_seconds'] = .401
        self.assertEqual(self.follower.tick()['state'], 'sensor_stale')
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)

    def test_request_validation_precedes_arming(self):
        self.mode.command(dict(action='set_mode', mode='automatic'))
        for data in (dict(action='autonomy_start', extra=True), dict(action='autonomy_heartbeat', seq=1)):
            with self.assertRaises(ValueError): self.mode.command(data)
        self.assertFalse(self.motor.snapshot()['armed'])

    def test_close_revokes_running_worker_before_motor_close(self):
        token = self.start(); self.heartbeat(token); self.follower.tick(); self.mode.close()
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)
        self.assertFalse(self.motor.snapshot()['armed'])
        with self.assertRaisesRegex(ControlError, 'control_closed'): self.heartbeat(token, 2)


    def line_step(self, token, values, dt=.02):
        self.now += dt
        self.sample.update(value=list(values), monotonic=self.now,
                           successes=self.sample['successes'] + 1)
        self.heartbeat(token, self.sample['successes'])
        return self.follower.tick()

    def test_outer_gap_inner_and_single_center_keep_pivot_then_two_centers_finish(self):
        token=self.start();self.heartbeat(token);self.follower.tick()
        before=len(self.wire.packets);self.line_step(token,[1,0,0,0])
        self.assertEqual(self.wire.packets[before:],[b'$spd:20,20,-20,-20#'])
        self.assertTrue(self.line_step(token,[1,1,1,1])['line_gap_active'])
        for values in ([1,1,0,1],[1,0,1,1],[1,0,0,1]):
            state=self.line_step(token,values)
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'],[20,20,-20,-20])
        self.line_step(token,[1,0,0,1]);self.assertEqual(self.motor.snapshot()['outputs'],[20]*4)
        self.assertTrue(self.motor.snapshot()['armed'])


    def test_visible_feedback_does_not_expire_at_a_turn_duration(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        for _ in range(100):
            state = self.line_step(token, [1,1,0,0])
            self.assertTrue(state['running'])
            self.assertEqual(self.motor.snapshot()['outputs'], [20,20,-20,-20])
        state = self.line_step(token, [1,0,0,1])
        self.assertEqual(self.motor.snapshot()['outputs'], [20,20,-20,-20])
        state = self.line_step(token, [1,0,0,1])
        self.assertTrue(state['running'])
        self.assertEqual(self.motor.snapshot()['outputs'], [20]*4)


    def test_manual_mode_cancels_tracking_without_late_heartbeat_revival(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token, [0,0,0,1])
        self.mode.command(dict(action='set_mode', mode='manual'))
        self.assertFalse(self.motor.snapshot()['armed'])
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)
        with self.assertRaises((ValueError, ControlError)): self.heartbeat(token, 100)
        before = self.motor.snapshot()['drive_commands_sent']; self.follower.tick()
        self.assertEqual(before, self.motor.snapshot()['drive_commands_sent'])
        owner = self.mode.command(dict(action='arm'))['session']
        state = self.mode.command(dict(action='drive', session=owner, seq=0, direction='forward', speed=200))
        self.assertEqual(state['outputs'], [200]*4)

    def test_first_center_overrides_white_and_next_all_black_uses_source_rule(self):
        token=self.start();self.heartbeat(token);self.follower.tick()
        self.line_step(token,[1,0,0,0]);self.line_step(token,[1,1,1,1])
        state=self.line_step(token,[1,0,0,1]);self.assertTrue(state['running'])
        self.assertFalse(state['line_gap_active']);self.assertEqual(self.motor.snapshot()['outputs'],[20,20,-20,-20])
        self.line_step(token,[1,0,0,1]);self.assertEqual(self.motor.snapshot()['outputs'],[20]*4)
        state=self.line_step(token,[0,0,0,0]);self.assertTrue(state['running'])
        self.assertFalse(state['line_gap_active']);self.assertEqual(self.motor.snapshot()['outputs'],[20]*4)


    def test_opposite_outer_preserves_current_turn_not_a_corner_fault(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token, [1,0,0,0])
        state = self.line_step(token, [0,1,1,1])
        self.assertTrue(state['running'])
        self.assertEqual(state['state'], 'right')
        self.assertEqual(self.motor.snapshot()['outputs'], [20,20,-20,-20])


    def test_gap_holds_last_real_inner_pair_not_a_new_hard_turn(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token, [1,0,0,0])
        self.line_step(token, [1,0,0,1]);self.line_step(token, [1,0,0,1])
        self.line_step(token, [1,0,1,1])
        self.assertEqual(self.motor.snapshot()['outputs'], [12,12,20,20])
        state = self.line_step(token, [1,1,1,1])
        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'], [12,12,20,20])


    def test_actual_weak_left_white_keeps_real_pair_without_upgrading_to_pivot(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        for _ in range(2): self.line_step(token, [1,0,1,1])
        state = self.line_step(token, [1,1,1,1])
        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
        self.assertFalse(state['direction_recovery_enabled'])
        self.assertEqual(self.motor.snapshot()['outputs'], [12,12,20,20])


    def test_all_four_previously_unlocated_masks_reach_uart_without_timeout(self):
        token=self.start();self.heartbeat(token);self.follower.tick();self.line_step(token,[0,1,1,1])
        for index in range(80):
            values=([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0])[index%4]
            state=self.line_step(token,values)
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'],[-20,-20,20,20])


    def test_recorded_full_black_in_right_correction_does_not_pick_new_direction(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token,[1,0,0,0])
        state = self.line_step(token,[0,0,0,0])
        self.assertTrue(state['running']); self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'],[20,20,-20,-20])
        state = self.line_step(token,[1,0,1,1])
        self.assertTrue(state['running']); self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'],[20,20,-20,-20])



class PhysicalWiringBridgeTests(unittest.TestCase):
    """Production order corrected from the user's explicit on-vehicle confirmation."""
    def check_wire(self, raw, direction):
        now = [200.0]; wire = Wire()
        motor = MotorControl(wire, clock=lambda: now[0], mode='speed')
        sample = dict(status='live', value=[1,0,0,1], monotonic=now[0], age_seconds=0, successes=1, physical_pins=[15,18,16,11])
        follower = BasicLineFollower(motor, lambda: copy.deepcopy(sample), clock=lambda: now[0], threaded=False)
        mode = DrivingMode(motor, follower=follower)
        self.addCleanup(motor.close); self.addCleanup(mode.close)
        mode.command(dict(action='set_mode', mode='automatic'))
        token = mode.command(dict(action='autonomy_start'))['autonomy_session']
        mode.command(dict(action='autonomy_heartbeat', autonomy_session=token, seq=1))
        follower.tick(); now[0] += .05
        sample.update(value=list(raw), monotonic=now[0], successes=2)
        mode.command(dict(action='autonomy_heartbeat', autonomy_session=token, seq=2))
        state = follower.tick()
        self.assertEqual(state['state'], direction)
        self.assertEqual(state['last_raw_values'], list(raw))
        self.assertEqual(state['last_values'], list(reversed(raw)))
        self.assertEqual(state['channel_order'], ['X4','X3','X2','X1'])
        self.assertEqual(state['raw_channel_order'], ['X1','X2','X3','X4'])
        expected = [20,20,-20,-20] if direction == 'right' else [-20,-20,20,20]
        self.assertEqual(motor.snapshot()['outputs'], expected)
        self.assertEqual(wire.packets[-1], ('$spd:'+','.join(map(str,expected))+'#').encode())
        now[0] += .005
        cached = follower.tick()
        self.assertTrue(cached['running'])
        self.assertEqual(cached['last_raw_values'], list(raw))
        self.assertEqual(cached['last_values'], list(reversed(raw)))
        self.assertEqual(motor.snapshot()['outputs'], expected)

    def test_default_channel_order_reaches_signed_uart_with_startup_assist(self):
        # User-confirmed channel locations; exercise the production follower/motor path.
        cases = [([0, 0, 0, 0], [20, 20, 20, 20]), ([1, 0, 0, 0], [-20, -20, 20, 20]), ([0, 1, 0, 0], [-20, -20, 20, 20]), ([1, 1, 0, 0], [-20, -20, 20, 20]), ([0, 0, 1, 0], [20, 20, -20, -20]), ([1, 0, 1, 0], [-20, -20, 20, 20]), ([0, 1, 1, 0], [20, 20, 20, 20]), ([1, 1, 1, 0], [-20, -20, 20, 20]), ([0, 0, 0, 1], [20, 20, -20, -20]), ([1, 0, 0, 1], [20, 20, 20, 20]), ([0, 1, 0, 1], [20, 20, -20, -20]), ([1, 1, 0, 1], [12, 12, 20, 20]), ([0, 0, 1, 1], [20, 20, -20, -20]), ([1, 0, 1, 1], [20, 20, 12, 12]), ([0, 1, 1, 1], [20, 20, -20, -20])]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                now = [200.0]; wire = Wire()
                motor = MotorControl(wire, clock=lambda:now[0], mode='speed', startup_boost=True)
                sample = dict(status='live',value=raw,monotonic=now[0],age_seconds=0,successes=1,physical_pins=[15,18,16,11])
                follower = BasicLineFollower(motor,lambda:copy.deepcopy(sample),clock=lambda:now[0],threaded=False)
                self.addCleanup(motor.close); self.addCleanup(follower.close)
                session=follower.start(speed=20)
                follower.heartbeat(session,1); state=follower.tick()
                self.assertEqual(state['last_values'],list(reversed(raw)))
                self.assertEqual(motor.snapshot()['requested_outputs'],expected)
                boost=[value*10 for value in expected]
                self.assertEqual(wire.packets[-1],('$spd:'+','.join(map(str,boost))+'#').encode())
                now[0]+=.201
                motor.check_deadline()
                self.assertEqual(motor.snapshot()['outputs'],expected)
                self.assertEqual(wire.packets[-1],('$spd:'+','.join(map(str,expected))+'#').encode())

    def test_forward_white_gap_does_not_restart_startup_assist(self):
        now=[200.0];wire=Wire()
        motor=MotorControl(wire,clock=lambda:now[0],mode='speed',startup_boost=True)
        sample=dict(status='live',value=[1,0,0,1],monotonic=now[0],age_seconds=0,successes=1,physical_pins=[15,18,16,11])
        follower=BasicLineFollower(motor,lambda:copy.deepcopy(sample),clock=lambda:now[0],threaded=False)
        self.addCleanup(motor.close);self.addCleanup(follower.close)
        owner=follower.start(20);follower.heartbeat(owner,0);follower.tick()
        self.assertEqual(motor.snapshot()['outputs'],[200]*4)
        now[0]+=.201;motor.check_deadline();self.assertEqual(motor.snapshot()['outputs'],[20]*4)
        sample.update(value=[1,1,1,1],monotonic=now[0],successes=2)
        follower.heartbeat(owner,1);state=follower.tick()
        self.assertTrue(state['running']);self.assertEqual(motor.snapshot()['outputs'],[20]*4)
        self.assertFalse(motor.snapshot()['startup_boost_active'])
        self.assertEqual(wire.packets[-1],b'$spd:20,20,20,20#')
        now[0]+=.1;sample.update(value=[1,1,1,0],monotonic=now[0],successes=3)
        follower.heartbeat(owner,2);state=follower.tick()
        self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
        self.assertEqual(motor.snapshot()['requested_outputs'],[-20,-20,20,20])
        self.assertEqual(wire.packets[-1],b'$spd:-200,-200,200,200#')

    def test_raw_0001_turns_right(self): self.check_wire([0,0,0,1], 'right')
    def test_raw_1000_turns_left(self): self.check_wire([1,0,0,0], 'left')

    def test_raw_0101_turns_right(self): self.check_wire([0,1,0,1], 'right')

    def test_raw_1010_turns_left(self): self.check_wire([1,0,1,0], 'left')



    def test_recorded_raw_correction_white_then_center_does_not_reuse_old_turn(self):
        # Actual patterns from v4_turn_then_stopped_213220.json, last trial.
        # Uniform 50ms fresh-frame timing is SYNTHETIC, not recorded GPIO timing;
        # the new outputs would change the physical trajectory (not proven here).
        now = [400.0]; wire = Wire(); motor = MotorControl(wire, clock=lambda:now[0], mode='speed')
        sample = dict(status='live',value=[1,0,0,1],monotonic=now[0],age_seconds=0,successes=0,physical_pins=[15,18,16,11])
        follower = BasicLineFollower(motor,lambda:copy.deepcopy(sample),clock=lambda:now[0],threaded=False)
        mode = DrivingMode(motor,follower=follower)
        self.addCleanup(motor.close); self.addCleanup(mode.close)
        mode.command(dict(action='set_mode',mode='automatic'))
        token = mode.command(dict(action='autonomy_start',speed=84))['autonomy_session']
        frames=(([1,0,0,1],[20]*4),([0,0,0,1],[20,20,-20,-20]),
                ([1,1,1,1],[20,20,-20,-20]),([1,0,0,1],[20,20,-20,-20]),([1,0,0,1],[20]*4),([1,1,1,1],[20]*4),([1,1,1,1],[0]*4))
        for seq,(raw,expected) in enumerate(frames,1):
            now[0]+=.201 if seq==7 else .05;sample.update(value=raw,monotonic=now[0],successes=seq)
            mode.command(dict(action='autonomy_heartbeat',autonomy_session=token,seq=seq));state=follower.tick()
            self.assertEqual(motor.snapshot()['outputs'],expected)
            self.assertEqual(state['last_raw_values'],raw);self.assertEqual(state['last_values'],list(reversed(raw)))
            if expected==[0]*4:
                self.assertFalse(state['running']);self.assertEqual(state['reason'],'line_gap_timeout')
                self.assertEqual(wire.packets[-3:],[b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'])
            else:
                self.assertTrue(state['running'])
                self.assertEqual(wire.packets[-1],('$spd:'+','.join(map(str,expected))+'#').encode())
        before=len(wire.packets)
        for seq,raw in enumerate(([0,1,1,1],[0,0,1,1],[1,1,0,1],[1,0,0,1]),8):
            now[0]+=.201 if seq==7 else .05;sample.update(value=raw,monotonic=now[0],successes=seq)
            self.assertFalse(follower.tick()['running']);self.assertEqual(motor.snapshot()['outputs'],[0]*4)
        self.assertEqual(len(wire.packets),before)



class Store:
    directory = 'SOFTWARE_FIXTURE_NO_REAL_SENSOR_FILES'
    def one(self, name):
        now = time.monotonic()
        return dict(status='live', value=[1,0,0,1], monotonic=now, age_seconds=0,
                    successes=int(now*1000000), physical_pins=[15,18,16,11])
    def snapshot(self):
        return {key: self.one(key) for key in ('line_sensors', 'camera', 'dht11')}


class BasicLineHTTPTests(unittest.TestCase):
    def setUp(self):
        self.wire = Wire(); self.motor = MotorControl(self.wire, mode='speed')
        self.server = create_server('127.0.0.1', 0, sensors=Store(), motor=self.motor)
        self.server.driving_mode.follower.threaded = False
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}); self.thread.start()
        self.addCleanup(self.close)
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2); self.motor.close()
    def request(self, data=None, headers=None):
        host, port = self.server.server_address
        h = {'Origin': 'http://%s:%s' % (host, port), 'Content-Type':'application/json', 'X-Control-Key':self.server.control_key}
        h.update(headers or {})
        c = http.client.HTTPConnection(host, port, timeout=2)
        try:
            c.request('POST' if data else 'GET', '/api/v1/control', json.dumps(data) if data else None, h)
            r = c.getresponse(); return r.status, json.loads(r.read())['data'] if r.status==200 else json.loads(r.read())
        finally: c.close()
    def test_real_envelope_start_and_owner_heartbeat_contract(self):
        self.assertEqual(self.request(dict(action='set_mode',mode='automatic'))[0],200)
        status, state = self.request(dict(action='autonomy_start'))
        self.assertEqual(status,200); token = state['autonomy_session']
        self.assertNotIn('autonomy_session',self.request()[1])
        self.server.driving_mode.follower.tick(); self.assertEqual(self.motor.snapshot()['drive_commands_sent'],0)
        self.assertEqual(self.request(dict(action='autonomy_heartbeat',autonomy_session=token,seq=1))[0],200)
        self.server.driving_mode.follower.tick(); self.assertEqual(self.motor.snapshot()['outputs'],[20]*4)
        self.request(dict(action='stop')); self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)

    def test_auth_and_origin_still_protect_automatic_actions(self):
        self.request(dict(action='set_mode',mode='automatic'))
        for headers in ({'Origin':'http://other.test'},{'X-Control-Key':'wrong'}):
            self.assertEqual(self.request(dict(action='autonomy_start'),headers)[0],403)
        self.assertFalse(self.motor.snapshot()['armed'])

    def test_stopped_waiting_start_cannot_be_activated_by_late_heartbeat(self):
        self.request(dict(action='set_mode',mode='automatic'))
        token = self.request(dict(action='autonomy_start'))[1]['autonomy_session']
        self.request(dict(action='stop'))
        self.assertNotEqual(self.request(dict(action='autonomy_heartbeat',autonomy_session=token,seq=1))[0],200)
        self.server.driving_mode.follower.tick()
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'],0)



if __name__ == '__main__':
    unittest.main()
