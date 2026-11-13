"""Replay observed drift exits and test the actual controller-to-UART bridge."""
import itertools
import json
from pathlib import Path
import unittest

from basic_line_follower import CENTER, WHITE
from imu_heading import ImuHeading
from track_imu_follower import TrackImuFollower
from test_basic_line_follower import Clock, Feed
from test_imu_line_follower import AngleMotor


class TrackTurnTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.feed = Feed(self.clock)
        self.imu = ImuHeading(clock=self.clock)
        self.imu.accept((0, 0, 0)); self.imu.calibrate()
        self.motor = AngleMotor()
        self.f = TrackImuFollower(self.motor, self.feed, self.imu,
                                  {'turns': ['left', 'left', 'right']},
                                  clock=self.clock, threaded=False)
        self.owner = self.f.start(20); self.seq = 0
        self.f.heartbeat(self.owner, 0)
        self.addCleanup(self.f.close)
        self.tick(); self.tick()

    def tick(self, values=CENTER, yaw=0, *, dt=.03):
        self.clock.now += dt
        self.feed.publish(list(reversed(values)))
        self.imu.accept((0, 0, yaw))
        self.seq += 1
        self.f.heartbeat(self.owner, self.seq)
        return self.f.tick()

    def trigger(self, yaw=0, *, finish_reverse=True):
        for _ in range(10): state = self.tick(WHITE, yaw)
        if state['phase'] == 'reverse' and finish_reverse:
            state = self.finish_reverse(yaw)
        return state

    def finish_reverse(self, yaw=0):
        for _ in range(30):
            state = self.tick(WHITE, yaw)
            if state['phase'] == 'turn': return state
        self.fail('reverse did not transition to the IMU turn')

    def settle(self, yaw):
        for _ in range(12):
            state = self.tick(WHITE, yaw)
            if state['phase'] == 'align': return state
        self.fail('turn never verified its stopped angle')

    def test_straight_ignores_absolute_imu_drift_and_every_correction_is_forward(self):
        for yaw in range(0, 121, 10):
            self.tick(CENTER, yaw)
            self.assertEqual(self.motor.outputs, (20, 20))
        for pattern in itertools.product((0, 1), repeat=4):
            if pattern == WHITE: continue
            state = self.tick(pattern, 120)
            self.assertEqual(state['phase'], 'follow')
            self.assertTrue(all(20 <= value <= 200 for value in self.motor.outputs))
        self.tick((1, 0, 1, 1), 120)
        self.assertEqual(self.motor.outputs, (40, 200))
        self.tick((1, 1, 0, 1), 120)
        self.assertEqual(self.motor.outputs, (200, 40))

    def test_lateral_exit_never_consumes_the_planned_left_corner(self):
        for pattern in [(1, 1, 0, 1), (1, 1, 0, 0), (1, 1, 1, 0)]:
            self.tick(pattern)
        for _ in range(25):
            state = self.tick(WHITE)
            self.assertTrue(state['running']); self.assertEqual(state['phase'], 'follow')
            self.assertEqual(state['reason'], 'recover_right')
            self.assertEqual(state['turn_index'], 0)
            self.assertEqual(self.motor.outputs, (200, 40))
        self.tick((1, 1, 0, 1))
        self.tick(CENTER); state = self.tick(CENTER)
        self.assertEqual(self.motor.outputs, (20, 20)); self.assertEqual(state['turn_index'], 0)

    def test_same_side_drift_also_needs_corner_evidence(self):
        self.tick((1, 0, 1, 1)); self.tick((0, 1, 1, 1))
        state = self.trigger()
        self.assertEqual(state['reason'], 'recover_left')
        self.assertEqual(state['phase'], 'follow'); self.assertEqual(state['white_samples'], 0)
        self.assertEqual(self.motor.outputs, (40, 200))

    def test_center_departure_requires_ten_fresh_whites_and_relative_ninety(self):
        for i in range(9):
            state = self.tick(WHITE, 30)
            self.assertEqual(state['white_samples'], i + 1)
            self.assertEqual(state['phase'], 'follow'); self.assertEqual(self.motor.outputs, (0, 0))
        for _ in range(4):
            self.clock.now += .01
            self.assertEqual(self.f.tick()['white_samples'], 9)
            self.assertEqual(self.motor.outputs, (0, 0))
        state = self.tick(WHITE, 30)
        self.assertEqual(state['phase'], 'reverse')
        self.assertEqual(state['corner_heading_deg'], 30)
        self.assertEqual(state['target_heading_deg'], 120)
        self.assertEqual(self.motor.outputs, (-20, -20))
        state = self.finish_reverse(30)
        self.assertEqual(state['phase'], 'turn')
        self.assertEqual(state['turn_entry_heading_deg'], 30)
        self.assertEqual(state['target_heading_deg'], 120)
        self.assertEqual(self.motor.outputs, (-40, 40))

    def test_planned_branch_then_same_side_edge_is_a_corner_but_opposite_side_is_not(self):
        self.tick((0, 0, 0, 1)); self.tick((0, 0, 1, 1)); self.tick((0, 1, 1, 1))
        self.assertEqual(self.trigger()['phase'], 'turn')
        self.f.stop(); self.owner = self.f.start(); self.seq = 0; self.f.heartbeat(self.owner, 0)
        self.tick(); self.tick()
        self.tick((0, 0, 0, 1)); self.tick((1, 1, 1, 0))
        self.assertEqual(self.trigger()['reason'], 'recover_right')

    def test_plain_center_clears_a_previous_branch(self):
        self.tick((0, 0, 0, 1)); self.tick(CENTER); self.tick((0, 1, 1, 1))
        self.assertEqual(self.trigger()['reason'], 'recover_left')

    def test_any_black_cancels_white_confirmation(self):
        self.tick(WHITE); self.tick(WHITE)
        self.tick(CENTER)
        self.assertEqual(self.motor.outputs, (20, 20))
        state = self.tick(WHITE)
        self.assertEqual(state['white_samples'], 1); self.assertEqual(state['phase'], 'follow')
        self.assertEqual(self.motor.outputs, (0, 0))

    def test_turn_cannot_finish_on_a_line_or_advance_before_centered_reacquisition(self):
        self.trigger()
        for _ in range(3):
            state = self.tick(CENTER, 45)
            self.assertEqual(state['phase'], 'turn')
        self.settle(90)
        self.assertEqual(self.tick((1, 0, 1, 1), 90)['phase'], 'align')
        self.tick(CENTER, 90)
        self.assertEqual(self.tick(CENTER, 90)['turn_index'], 1)
        for _ in range(20): self.tick(CENTER, 130)
        self.assertEqual(self.motor.outputs, (20, 20))
        self.assertEqual(self.trigger(130)['target_heading_deg'], 220)

    def test_overshoot_reverses_with_useful_sustained_output_then_verifies_stop(self):
        self.trigger(); self.tick(WHITE, 100)
        for _ in range(7): state = self.tick(WHITE, 100)
        self.assertEqual(state['angle_stage'], 'correct')
        self.assertEqual(self.motor.outputs, (40, -40))
        for _ in range(11): state = self.tick(WHITE, 100)
        self.assertGreaterEqual(min(map(abs, self.motor.outputs)), 60)
        self.assertEqual(state['phase'], 'turn'); self.assertEqual(state['turn_index'], 0)
        self.assertEqual(self.settle(90)['phase'], 'align')

    def test_white_lateral_recovery_uses_existing_loss_ceiling(self):
        self.tick((1, 1, 1, 0))
        for _ in range(50):
            state = self.tick(WHITE)
            if not state['running']: break
        self.assertEqual(state['reason'], 'line_gap_timeout')
        self.assertEqual(state['turn_index'], 0); self.assertEqual(self.motor.outputs, (0, 0))

    def test_valid_gears_and_no_weak_uart_targets_during_tracking_or_fine_turn(self):
        from motor_control import MotorControl
        class Wire:
            def __init__(self): self.packets = []
            def send(self, packet): self.packets.append(packet)
            def close(self): pass
        self.f.close()
        wire = Wire(); motor = MotorControl(wire, clock=self.clock, mode='speed', startup_boost=True)
        self.addCleanup(motor.close)
        self.f = TrackImuFollower(motor, self.feed, self.imu, {'turns': ['left']},
                                  clock=self.clock, threaded=False)
        self.addCleanup(self.f.close)
        self.owner = self.f.start(); self.seq = 0; self.f.heartbeat(self.owner, 0)
        self.tick(); self.tick()
        self.assertEqual(motor.snapshot()['outputs'], [200] * 4)
        for speed in (20, 30, 40):
            self.seq += 1; self.f.heartbeat(self.owner, self.seq, speed=speed)
            for pattern in (CENTER, (1, 0, 1, 1), (0, 1, 1, 1), (1, 1, 1, 0)):
                self.tick(pattern)
        self.tick(); self.tick(); self.trigger(); self.tick(WHITE, 80)
        for _ in range(22): self.tick(WHITE, 80)
        for packet in wire.packets:
            if not packet.startswith(b'$spd:'): continue
            outputs = [int(v) for v in packet[5:-1].split(b',')]
            self.assertTrue(all(value == 0 or abs(value) >= 20 for value in outputs), packet)
        self.assertTrue(any(p.startswith(b'$spd:-60,-60,60,60#') for p in wire.packets))
        with self.assertRaisesRegex(ValueError, 'invalid_speed'):
            self.f.heartbeat(self.owner, self.seq + 1, speed=10)

    def test_new_start_still_requires_calibration_and_clears_departure(self):
        self.tick((1, 1, 1, 0)); self.tick(WHITE); self.f.stop()
        self.imu.origin = None
        with self.assertRaisesRegex(ValueError, 'imu_not_calibrated'): self.f.start()
        self.imu.calibrate(); self.f.reset_route()
        self.owner = self.f.start(); self.seq = 0; self.f.heartbeat(self.owner, 0)
        self.assertEqual(self.f.snapshot()['departure_kind'], None)
        self.assertEqual(self.tick(WHITE)['reason'], 'line_lost')

    def test_latest_observed_false_left_turn_replays_as_forward_right_recovery(self):
        source = (Path(__file__).resolve().parents[4] / 'outputs/RBE201TC_STRAIGHT_IMU_REPAIR_20261113'
                  / 'line_20261113T105849_7a72b1b321e1.jsonl')
        if not source.exists(): self.skipTest('raw robot evidence is outside the source package')
        samples = [e for s in source.read_text().splitlines() if (e := json.loads(s)).get('event') == 'sample']
        end = next(i for i, e in enumerate(samples) if e.get('phase') == 'turn')
        self.f.stop(); self.clock.now = samples[0]['monotonic']
        self.imu.accept((0, 0, 0)); self.owner = self.f.start(); self.seq = 0; self.f.heartbeat(self.owner, 0)
        for event in samples[:end + 1]:
            self.clock.now = event['monotonic']; self.imu.accept((0, 0, 0))
            self.feed.item.update(value=event['raw_values'], monotonic=event['sample_monotonic'], successes=event['sample_count'])
            self.seq += 1; self.f.heartbeat(self.owner, self.seq)
            state = self.f.tick()
            self.assertTrue(state['running']); self.assertEqual(state['phase'], 'follow')
            self.assertTrue(all(value >= 20 for value in self.motor.outputs))
        self.assertEqual(state['reason'], 'recover_right'); self.assertEqual(state['turn_index'], 0)

    def test_all_fourteen_turns_with_centered_straights_and_signed_imu_feedback(self):
        config = json.loads((Path(__file__).resolve().parents[2] / 'deploy/imu_route.json').read_text())
        self.f.turns = tuple(config['turns'])
        yaw = 0
        for index, direction in enumerate(self.f.turns):
            for _ in range(30):
                state = self.tick(CENTER, yaw)
                self.assertEqual(state['phase'], 'follow')
                self.assertEqual(state['turn_index'], index)
                self.assertEqual(self.motor.outputs, (20, 20))
            self.trigger(yaw)
            yaw += 90 if direction == 'left' else -90
            self.settle((yaw + 180) % 360 - 180)
            self.tick(CENTER, (yaw + 180) % 360 - 180)
            state = self.tick(CENTER, (yaw + 180) % 360 - 180)
            self.assertEqual(state['phase'], 'follow'); self.assertEqual(state['turn_index'], index + 1)
        self.assertEqual(self.tick(WHITE, yaw)['reason'], 'route_complete')

    def test_server_selects_hybrid_and_no_motion_is_created_by_configuration(self):
        from motor_control import MotorControl
        from server import create_server
        class Wire:
            def __init__(self): self.packets = []
            def send(self, packet): self.packets.append(packet)
            def close(self): pass
        class Sensors:
            directory = 'fixture'
            def one(inner, name): return self.feed()
        wire = Wire(); motor = MotorControl(wire, clock=self.clock, mode='speed', startup_boost=True)
        server = create_server('127.0.0.1', 0, sensors=Sensors(), motor=motor, imu=self.imu,
                               route_config={'controller': 'track_imu', 'turns': ['left']})
        try:
            self.assertIsInstance(server.driving_mode.follower, TrackImuFollower)
            self.assertFalse(server.driving_mode.snapshot()['armed'])
            self.assertTrue(all(not p.startswith(b'$spd:') or p == b'$spd:0,0,0,0#' for p in wire.packets))
        finally:
            server.server_close(); motor.close()

    def test_hybrid_configuration_cannot_fall_back_to_non_imu_driving(self):
        from server import create_server
        with self.assertRaisesRegex(ValueError, 'imu_required_for_route'):
            create_server('127.0.0.1', 0, route_config={'controller': 'track_imu'})

    def test_first_white_clears_uart_then_tenth_starts_reverse_before_sustained_turn(self):
        from motor_control import MotorControl
        class Wire:
            def __init__(self): self.packets = []
            def send(self, packet): self.packets.append(packet)
            def close(self): pass
        self.f.close()
        wire = Wire(); motor = MotorControl(wire, clock=self.clock, mode='speed', startup_boost=True)
        self.addCleanup(motor.close)
        self.f = TrackImuFollower(motor, self.feed, self.imu, {'turns': ['left']},
                                  clock=self.clock, threaded=False)
        self.addCleanup(self.f.close)
        self.owner = self.f.start(); self.seq = 0; self.f.heartbeat(self.owner, 0)
        self.tick(); self.tick()
        first = len(wire.packets)
        self.tick(WHITE)
        self.assertEqual(wire.packets[first:], [b'$pwm:0,0,0,0#', b'$spd:0,0,0,0#'])
        for _ in range(8):
            self.tick(WHITE)
            self.assertEqual(motor.snapshot()['outputs'], [0, 0, 0, 0])
            self.assertTrue(motor.snapshot()['armed'])
        state = self.tick(WHITE)
        self.assertEqual(state['phase'], 'reverse')
        self.assertEqual(motor.snapshot()['outputs'], [-200, -200, -200, -200])
        for _ in range(26):
            self.tick(WHITE, yaw=0)
            self.assertTrue(all(value < 0 for value in motor.snapshot()['outputs']))
        state = self.tick(WHITE, yaw=0)
        self.assertEqual(state['phase'], 'turn')
        self.assertEqual(motor.snapshot()['outputs'], [-200, -200, 200, 200])
        for _ in range(12):
            self.tick(WHITE, yaw=0)
            self.assertLess(motor.snapshot()['outputs'][0], 0)
            self.assertGreater(motor.snapshot()['outputs'][2], 0)
        self.assertEqual(self.f.phase, 'turn')

    def test_reverse_uses_confirmed_speed_for_point_eight_seconds_even_if_probes_see_black(self):
        state = self.trigger(30, finish_reverse=False)
        self.assertEqual(state['phase'], 'reverse')
        self.assertEqual(state['reverse_speed'], 20)
        self.assertEqual(state['target_heading_deg'], 120)
        self.assertEqual(self.motor.outputs, (-20, -20))
        state = self.tick(CENTER, 35, dt=.4)
        self.assertEqual(state['phase'], 'reverse'); self.assertEqual(self.motor.outputs, (-20, -20))
        self.seq += 1; self.f.heartbeat(self.owner, self.seq, speed=40)
        state = self.tick((1, 1, 1, 0), 40, dt=.39)
        self.assertEqual(state['phase'], 'reverse'); self.assertEqual(self.motor.outputs, (-20, -20))
        state = self.tick(WHITE, 45, dt=.02)
        self.assertEqual(state['phase'], 'turn')
        self.assertEqual(state['corner_heading_deg'], 30)
        self.assertEqual(state['turn_entry_heading_deg'], 45)
        self.assertEqual(state['target_heading_deg'], 120)

    def test_right_turn_also_anchors_to_confirmed_white_not_the_reverse_endpoint(self):
        self.f.turns = ('right',)
        self.trigger(30, finish_reverse=False)
        state = self.finish_reverse(40)
        self.assertEqual(state['corner_heading_deg'], 30)
        self.assertEqual(state['turn_entry_heading_deg'], 40)
        self.assertEqual(state['target_heading_deg'], -60)
        self.assertEqual(self.motor.outputs, (40, -40))

    def test_stop_during_reverse_revokes_motion_and_a_new_start_clears_its_timer(self):
        self.trigger(finish_reverse=False)
        self.assertIsNotNone(self.f.reverse_started)
        self.f.stop()
        self.clock.now += 1
        self.assertFalse(self.f.tick()['running']); self.assertEqual(self.motor.outputs, (0, 0))
        self.imu.accept((0, 0, 0))
        self.owner = self.f.start(); self.seq = 0; self.f.heartbeat(self.owner, 0)
        self.assertIsNone(self.f.reverse_started); self.assertIsNone(self.f.corner_heading)
        self.tick(); self.tick()
        self.assertEqual(self.motor.outputs, (20, 20)); self.assertEqual(self.f.phase, 'follow')

    def test_large_accumulated_straight_heading_never_steers_or_snaps_the_next_turn(self):
        for i in range(80):
            self.tick(CENTER, (i * 10 + 180) % 360 - 180)
            self.assertEqual(self.motor.outputs, (20, 20))
        state = self.trigger(70, finish_reverse=False)
        self.assertAlmostEqual(state['corner_heading_deg'], 790)
        self.assertAlmostEqual(state['target_heading_deg'], 880)
        self.assertEqual(self.motor.outputs, (-20, -20))

    def test_after_angle_completion_every_visible_pattern_immediately_resumes_forward_feedback(self):
        self.trigger(); self.settle(90)
        for pattern in itertools.product((0, 1), repeat=4):
            if pattern == WHITE: continue
            state = self.tick(pattern, 90)
            self.assertTrue(state['running'])
            self.assertTrue(all(value >= 20 for value in self.motor.outputs))
        self.tick(CENTER, 90); state = self.tick(CENTER, 90)
        self.assertEqual(state['phase'], 'follow'); self.assertEqual(state['turn_index'], 1)
