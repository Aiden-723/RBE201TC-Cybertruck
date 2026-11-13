"""Software-only fake wire checks; never opens a serial port."""
import unittest
from motor_control import MotorControl, ControlError


class Wire:
    def __init__(self):
        self.packets = []
        self.fail = False
    def send(self, packet):
        if self.fail:
            raise OSError('SOFTWARE_FIXTURE_DISCONNECT')
        self.packets.append(packet)
    def close(self):
        pass


class AutonomousMotorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.wire = Wire()
        self.motor = MotorControl(self.wire, clock=lambda: self.now, mode='speed')
        self.addCleanup(self.motor.close)
    def arm(self):
        return self.motor.command({'action': 'arm'})['session']
    def test_internal_differential_uses_existing_speed_wire(self):
        owner = self.arm()
        result = self.motor.drive_sides(owner, 0, 83, 140)
        self.assertEqual(result['outputs'], [83, 83, 140, 140])
        self.assertEqual(self.wire.packets[-1], b'$spd:83,83,140,140#')
        self.assertEqual(result['motion_feedback'], 'unverified')
        self.assertFalse(any(b'flash' in p or b'pid' in p for p in self.wire.packets))
    def test_manual_arm_session_and_default_speed_still_work(self):
        owner = self.arm()
        result = self.motor.command(dict(action='drive', session=owner, seq=0, direction='forward', speed=200))
        self.assertEqual(result['outputs'], [200] * 4)
    def test_bridge_is_not_a_public_motor_command(self):
        owner = self.arm()
        with self.assertRaisesRegex(ControlError, 'invalid_command'):
            self.motor.command(dict(action='drive_sides', session=owner, seq=0, left=140, right=140))
        self.assertEqual(self.motor.snapshot()['drive_commands_sent'], 0)
    def test_no_owner_no_motion(self):
        before = list(self.wire.packets)
        for owner in (None, '', 'not-owner', 123, False):
            with self.subTest(owner=owner), self.assertRaises(ControlError):
                self.motor.drive_sides(owner, 0, 140, 140)
        self.assertEqual(before, self.wire.packets)
    def test_side_validation_does_not_consume_sequence(self):
        owner = self.arm()
        for left, right in ((True, 140), (140, False), (1.0, 140), (221, 0), (0, -221), ('140', 0), (None, 0)):
            with self.subTest(left=left, right=right), self.assertRaisesRegex(ControlError, 'invalid_sides'):
                self.motor.drive_sides(owner, 0, left, right)
        self.motor.drive_sides(owner, 0, 0, 140)
        self.assertEqual(self.motor.snapshot()['outputs'], [0, 0, 140, 140])
    def test_sequence_must_be_strict_integer_and_increase(self):
        owner = self.arm()
        self.motor.drive_sides(owner, 2, 140, 140)
        for seq in (2, 1, True, 3.0, 2**53):
            with self.subTest(seq=seq), self.assertRaisesRegex(ControlError, 'stale_command'):
                self.motor.drive_sides(owner, seq, 140, 140)
    def test_speed_correction_updates_target_without_intermediate_stop(self):
        owner = self.arm()
        self.motor.drive_sides(owner, 0, 140, 140)
        before=len(self.wire.packets)
        self.motor.drive_sides(owner, 1, -134, 134)
        self.assertEqual(self.wire.packets[before:], [b'$spd:-134,-134,134,134#'])
    def test_expiry_before_write_revokes_old_owner(self):
        owner = self.arm()
        self.motor.drive_sides(owner, 0, 140, 140)
        self.now += .451
        with self.assertRaisesRegex(ControlError, 'not_armed'):
            self.motor.drive_sides(owner, 1, 140, 140)
        self.assertFalse(self.motor.snapshot()['armed'])
        self.assertEqual(self.motor.snapshot()['outputs'], [0]*4)
    def test_continuous_valid_lease_has_no_three_second_cap(self):
        owner = self.arm()
        for seq in range(101):
            self.now = 100 + seq*.1
            self.motor.drive_sides(owner, seq, 140, 140)
        self.assertEqual(self.motor.snapshot()['outputs'], [140]*4)
        self.assertIsNone(self.motor.snapshot()['max_hold_seconds'])
    def test_global_stop_revokes_differential_owner(self):
        owner = self.arm()
        self.motor.drive_sides(owner, 0, 83, 140)
        self.motor.command({'action': 'stop'})
        with self.assertRaisesRegex(ControlError, 'not_armed'):
            self.motor.drive_sides(owner, 1, 83, 140)
    def test_serial_fault_is_latched_without_retrying_movement(self):
        owner = self.arm()
        self.wire.fail = True
        with self.assertRaises(ControlError):
            self.motor.drive_sides(owner, 0, 140, 140)
        self.wire.fail = False
        before = list(self.wire.packets)
        with self.assertRaisesRegex(ControlError, 'serial_fault_restart_required'):
            self.motor.drive_sides(owner, 1, 140, 140)
        self.assertEqual(before, self.wire.packets)
    def test_closed_bridge_rejects_movement(self):
        owner = self.arm()
        self.motor.close()
        with self.assertRaises(ControlError):
            self.motor.drive_sides(owner, 0, 140, 140)


if __name__ == '__main__':
    unittest.main()

class V6ContinuousOutputTests(unittest.TestCase):
    def test_recorded_opposing_patterns_have_one_speed_packet_each_no_full_car_stop(self):
        from basic_line_follower import BasicLineFollower
        from test_basic_line_follower import Clock,Feed
        clock=Clock()
        class TimedWire(Wire):
            def send(self,packet):
                super().send(packet);clock.now+=.03
        wire=TimedWire();motor=MotorControl(wire,clock=clock,mode='speed');self.addCleanup(motor.close)
        feed=Feed(clock);f=BasicLineFollower(motor,feed,clock=clock,threaded=False,channel_permutation=(0,1,2,3));self.addCleanup(f.close)
        owner=f.start(84)
        # Actual v6 physical pattern order; timestamps are synthetic. This
        # checks output interruption, not an asserted physical trajectory.
        frames=[([1,0,1,1],[0,0,84,84]),([1,0,1,0],[200,200,-200,-200]),([1,0,1,1],[0,0,84,84]),([1,0,0,1],[84]*4)]*5
        for seq,(values,expected) in enumerate(frames):
            clock.now+=.02;feed.publish(values);f.heartbeat(owner,seq)
            before=len(wire.packets);began=clock.now;s=f.tick()
            self.assertTrue(s['running']);self.assertEqual(motor.snapshot()['outputs'],expected)
            self.assertEqual(wire.packets[before:],[('$spd:'+','.join(map(str,expected))+'#').encode()])
            self.assertAlmostEqual(clock.now-began,.03)
        f.stop();self.assertEqual(wire.packets[-3:],[b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'])
        self.assertFalse(motor.snapshot()['armed'])

    def test_pwm_bridge_preserves_existing_reversal_sequence(self):
        wire=Wire();motor=MotorControl(wire,mode='pwm');self.addCleanup(motor.close)
        owner=motor.command({'action':'arm'})['session'];motor.drive_sides(owner,0,140,140)
        before=len(wire.packets);motor.drive_sides(owner,1,140,-140)
        self.assertEqual(wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#',b'$pwm:140,140,-140,-140#'])

    def test_speed_update_failure_still_latches_full_stop(self):
        wire=Wire();motor=MotorControl(wire,mode='speed');self.addCleanup(motor.close)
        owner=motor.command({'action':'arm'})['session'];motor.drive_sides(owner,0,84,84);wire.fail=True
        with self.assertRaises(ControlError):motor.drive_sides(owner,1,200,-200)
        self.assertFalse(motor.snapshot()['armed']);self.assertIsNotNone(motor.snapshot()['fault'])
