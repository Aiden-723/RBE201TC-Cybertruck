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
    def test_explicit_speed_84_and_continuous_corrections_keep_safety_guards(self):
        self.mode.command(dict(action='set_mode', mode='automatic'))
        started = self.mode.command(dict(action='autonomy_start', speed=84))
        token = started['autonomy_session']
        self.assertEqual(started['autonomy']['speed'], 84)
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'], 0)
        self.heartbeat(token); self.follower.tick()
        self.assertEqual(self.motor.snapshot()['outputs'], [84]*4)
        for seq, values, expected in ((2,[1,1,0,1],[84,84,0,0]), (3,[1,0,0,0],[200,200,-200,-200])):
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
        self.assertEqual(self.motor.snapshot()['outputs'], [140]*4)
    def test_start_in_manual_is_rejected(self):
        with self.assertRaisesRegex(ControlError, 'automatic_mode_required'):
            self.mode.command(dict(action='autonomy_start'))
        self.assertFalse(self.motor.snapshot()['armed'])
    def test_left_and_right_outer_probes_give_opposite_pivots(self):
        for values, expected in (([0, 1, 1, 1], [-200, -200, 200, 200]), ([1, 1, 1, 0], [200, 200, -200, -200])):
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
    def test_real_forward_pair_survives_white_then_times_out_without_auto_resume(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        state = self.line_step(token, [1,1,1,1])
        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)
        for _ in range(73):
            state = self.line_step(token, [1,1,1,1])
            if not state['running']: break
        self.assertFalse(state['running'])
        self.assertEqual(state['last_failure']['reason'], 'line_gap_timeout')
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)
        self.assertFalse(self.motor.snapshot()['armed'])
        self.sample.update(value=[1,0,0,1], successes=self.sample['successes']+1)
        before = self.motor.snapshot()['drive_commands_sent']; self.follower.tick()
        self.assertEqual(before, self.motor.snapshot()['drive_commands_sent'])
        with self.assertRaises(ValueError): self.heartbeat(token, 1000)
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

    def test_outer_gap_inner_opposite_and_center_replace_each_other_immediately(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        before = len(self.wire.packets)
        state = self.line_step(token, [1,0,0,0])
        self.assertEqual(state['state'], 'right')
        self.assertEqual(self.wire.packets[before:], [b'$pwm:0,0,0,0#', b'$spd:0,0,0,0#', b'$stop#', b'$spd:200,200,-200,-200#'])
        state = self.line_step(token, [1,1,1,1])
        self.assertTrue(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'], [200,200,-200,-200])
        for values, expected in (([1,1,0,1],[140,140,0,0]), ([1,0,1,1],[0,0,140,140]), ([1,0,0,1],[140]*4)):
            state = self.line_step(token, values)
            self.assertTrue(state['running'])
            self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'], expected)
        self.assertTrue(self.motor.snapshot()['armed'])

    def test_visible_feedback_does_not_expire_at_a_turn_duration(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        for _ in range(100):
            state = self.line_step(token, [1,1,0,0])
            self.assertTrue(state['running'])
            self.assertEqual(self.motor.snapshot()['outputs'], [200,200,-200,-200])
        state = self.line_step(token, [1,0,0,1])
        self.assertTrue(state['running'])
        self.assertEqual(self.motor.snapshot()['outputs'], [140]*4)

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

    def test_first_reacquired_center_drives_forward_without_waiting_for_confirmation(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token, [1,0,0,0]); self.line_step(token, [1,1,1,1])
        state = self.line_step(token, [1,0,0,1])
        self.assertTrue(state['running'])
        self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'], [140]*4)
        state = self.line_step(token, [1,1,1,1])
        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)

    def test_opposite_outer_is_current_feedback_not_a_corner_fault(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token, [1,0,0,0])
        state = self.line_step(token, [0,1,1,1])
        self.assertTrue(state['running'])
        self.assertEqual(state['state'], 'left')
        self.assertEqual(self.motor.snapshot()['outputs'], [-200,-200,200,200])

    def test_gap_holds_last_real_inner_pair_not_a_new_hard_turn(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token, [1,0,0,0])
        self.line_step(token, [1,0,1,1])
        self.assertEqual(self.motor.snapshot()['outputs'], [0,0,140,140])
        state = self.line_step(token, [1,1,1,1])
        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)

    def test_actual_weak_left_white_keeps_real_pair_without_upgrading_to_pivot(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        for _ in range(2): self.line_step(token, [1,0,1,1])
        state = self.line_step(token, [1,1,1,1])
        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
        self.assertFalse(state['direction_recovery_enabled'])
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)

    def test_white_and_full_black_share_one_unlocated_budget(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token, [1,0,0,0])
        first = self.now + .02
        for index in range(75):
            values = [0,0,0,0] if index % 2 else [1,1,1,1]
            state = self.line_step(token, values)
            if not state['running']: break
            self.assertTrue(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'], [200,200,-200,-200])
        self.assertFalse(state['running'])
        self.assertEqual(state['last_failure']['reason'], 'line_gap_timeout')
        self.assertLessEqual(self.now-first,1.421)
        self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)

    def test_recorded_full_black_in_right_correction_does_not_pick_new_direction(self):
        token = self.start(); self.heartbeat(token); self.follower.tick()
        self.line_step(token,[1,0,0,0])
        state = self.line_step(token,[0,0,0,0])
        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'],[200,200,-200,-200])
        state = self.line_step(token,[1,0,1,1])
        self.assertTrue(state['running']); self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.motor.snapshot()['outputs'],[0,0,140,140])


class PhysicalWiringBridgeTests(unittest.TestCase):
    """Production default order, backed by the disarmed physical sweep."""
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
        expected = [200,200,-200,-200] if direction == 'right' else [-200,-200,200,200]
        self.assertEqual(motor.snapshot()['outputs'], expected)
        self.assertEqual(wire.packets[-1], ('$spd:'+','.join(map(str,expected))+'#').encode())
        now[0] += .005
        cached = follower.tick()
        self.assertTrue(cached['running'])
        self.assertEqual(cached['last_raw_values'], list(raw))
        self.assertEqual(cached['last_values'], list(reversed(raw)))
        self.assertEqual(motor.snapshot()['outputs'], expected)

    def test_observed_raw_0001_is_right_not_manual_left(self): self.check_wire([0,0,0,1], 'right')
    def test_mirror_raw_1000_is_left(self): self.check_wire([1,0,0,0], 'left')
    def test_observed_two_branch_raw_0101_is_right(self): self.check_wire([0,1,0,1], 'right')
    def test_mirror_two_branch_raw_1010_is_left(self): self.check_wire([1,0,1,0], 'left')


    def test_recorded_raw_pattern_order_now_countersteers_and_resumes_forward(self):
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
        frames = (([1,0,0,1],[84]*4), ([0,0,0,1],[200,200,-200,-200]),
                  ([1,1,1,1],[200,200,-200,-200]), ([0,1,1,1],[200,200,-200,-200]),
                  ([0,0,1,1],[200,200,-200,-200]), ([1,1,0,1],[0,0,84,84]),
                  ([1,0,0,1],[84]*4), ([1,0,0,1],[84]*4), ([1,1,1,1],[0]*4))
        for seq,(raw,expected) in enumerate(frames,1):
            now[0] += .05; sample.update(value=raw,monotonic=now[0],successes=seq)
            mode.command(dict(action='autonomy_heartbeat',autonomy_session=token,seq=seq))
            state = follower.tick()
            self.assertTrue(state['running'])
            self.assertEqual(motor.snapshot()['outputs'],expected)
            self.assertEqual(state['last_raw_values'],raw)
            self.assertEqual(state['last_values'],list(reversed(raw)))
            self.assertEqual(wire.packets[-1],('$spd:'+','.join(map(str,expected))+'#').encode())


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
        self.server.driving_mode.follower.tick(); self.assertEqual(self.motor.snapshot()['outputs'],[140]*4)
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
