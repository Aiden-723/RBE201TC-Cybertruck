"""Opt-in, leased PWM wheel testing. Serial transmission is not motion feedback."""
import hmac
import secrets
import threading
import time


class ControlError(Exception):
    pass


def mix(direction, speed):
    if not isinstance(direction, str):
        raise ControlError('invalid_direction')
    if type(speed) is not int or not 80 <= speed <= 220:
        raise ControlError('speed_out_of_range')
    # Course line_follower.py convention; physical wiring is still unverified.
    patterns = {'forward': (1, 1, 1, 1), 'backward': (-1, -1, -1, -1),
                'left': (-1, -1, 1, 1), 'right': (1, 1, -1, -1)}
    if direction in patterns:
        return tuple(n * speed for n in patterns[direction])
    inner = 0
    # Stop the inside wheels for stronger skid steering under ground friction.
    # Right/left keeps the same yaw direction when backing up; the moving
    # side follows travel direction, without counter-rotating the other side.
    arcs = {'forward_right': (speed, speed, inner, inner),
            'forward_left': (inner, inner, speed, speed),
            'backward_right': (-inner, -inner, -speed, -speed),
            'backward_left': (-speed, -speed, -inner, -inner)}
    if direction in arcs:
        return arcs[direction]
    if direction in ('m1', 'm2', 'm3', 'm4'):
        return tuple(speed if i == int(direction[1]) - 1 else 0 for i in range(4))
    raise ControlError('invalid_direction')


class _AtomicModemLines:
    """Apply both Linux modem outputs in one ioctl, without an ISP-like gap."""
    def _update_modem_lines(self):
        import array
        import fcntl
        import termios
        mask = ((termios.TIOCM_DTR if self._dtr_state else 0) |
                (termios.TIOCM_RTS if self._rts_state else 0))
        fcntl.ioctl(self.fd, termios.TIOCMSET, array.array('i', [mask]), True)

    def _update_dtr_state(self):
        self._update_modem_lines()

    def _update_rts_state(self):
        self._update_modem_lines()


class SerialPWM:
    def __init__(self, port):
        import sys
        from serial import Serial
        serial_class = Serial
        if sys.platform.startswith('linux'):
            # pySerial normally applies DTR and RTS in two separate calls on
            # open. On auto-ISP boards the intermediate pair can affect boot.
            class AtomicSerial(_AtomicModemLines, Serial):
                pass
            serial_class = AtomicSerial
        self.serial = serial_class(port=None, baudrate=115200, timeout=0,
                                   write_timeout=0.08, exclusive=True)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()
        self.last_packet_at = 0.0

    def send(self, packet):
        # Do not flush/reset output buffers: a queued STOP must never be discarded.
        # This firmware needs separated UART frames; do not concatenate stop/drive packets.
        delay = .03 - (time.monotonic() - self.last_packet_at)
        if delay > 0:
            time.sleep(delay)
        if self.serial.write(packet) != len(packet):
            raise OSError('short_serial_write')
        self.last_packet_at = time.monotonic()

    def close(self):
        self.serial.close()

    def probe(self):
        results = {}
        queries = [('configuration', b'$read_flash#'), ('battery', b'$read_vol#')]
        for name, packet in queries:
            self.serial.reset_input_buffer()
            self.send(packet)
            deadline = time.monotonic() + .25
            output = bytearray()
            while time.monotonic() < deadline and len(output) < 2048:
                output.extend(self.serial.read(2048 - len(output)))
                time.sleep(.01)
            results[name] = output.decode('ascii', errors='replace')[-1024:]
        return results


class MotorControl:
    lease_seconds = 0.45
    arm_idle_seconds = 60.0

    def __init__(self, transport, clock=time.monotonic, mode='pwm'):
        if mode not in ('pwm', 'speed'):
            raise ValueError('invalid motor mode')
        self.transport = transport
        self.mode = mode
        self.clock = clock
        self.lock = threading.RLock()
        self.session = None
        self.seq = -1
        self.values = (0, 0, 0, 0)
        self.deadline = None
        self.idle_deadline = None
        self.fault = None
        self.reason = 'startup_stop'
        self.closed = False
        self.stopping = threading.Event()
        self.thread = None
        self.drive_commands_sent = 0
        self.last_drive_time = None
        self.serial_diagnostics = {}
        with self.lock:
            self._stop_locked('startup_stop', disarm=True)
            if not self.fault and hasattr(self.transport, 'probe'):
                try:
                    self.serial_diagnostics = self.transport.probe()
                except Exception:
                    self.fault = 'serial_probe_failed'

    def _write(self, packet):
        self.transport.send(packet)

    def _stop_locked(self, reason, disarm=False):
        self.values = (0, 0, 0, 0)
        self.deadline = None
        self.reason = reason
        if disarm:
            self.session = None
            self.idle_deadline = None
        errors = []
        for packet in (b'$pwm:0,0,0,0#', b'$spd:0,0,0,0#', b'$stop#'):
            try:
                self._write(packet)
            except Exception:
                errors.append('serial_stop_failed')
        if errors:
            self.fault = errors[0]
            self.session = None
            self.reason = self.fault

    def start(self):
        self.thread = threading.Thread(target=self._watch, name='motor-watchdog', daemon=False)
        self.thread.start()

    def _watch(self):
        while not self.stopping.wait(0.02):
            self.check_deadline()

    def _check_locked(self):
        now = self.clock()
        if self.deadline is not None and now >= self.deadline:
            self._stop_locked('heartbeat_expired', disarm=True)
        elif self.session and self.idle_deadline is not None and now >= self.idle_deadline:
            self._stop_locked('idle_expired', disarm=True)

    def check_deadline(self):
        with self.lock:
            self._check_locked()

    def snapshot(self):
        with self.lock:
            self._check_locked()
            return {'enabled': True, 'armed': self.session is not None,
                    'state': 'fault' if self.fault else 'driving' if any(self.values) else 'armed' if self.session else 'stopped',
                    'outputs': list(self.values), 'command_mode': self.mode,
                    'pwm': list(self.values) if self.mode == 'pwm' else None,
                    'speed_setpoints': list(self.values) if self.mode == 'speed' else None,
                    'reason': self.reason, 'fault': self.fault,
                    'lease_ms': round(self.lease_seconds * 1000), 'max_hold_seconds': None,
                    'mapping': {'left': ['M1', 'M2'], 'right': ['M3', 'M4'], 'verified': False},
                    'drive_commands_sent': self.drive_commands_sent,
                    'last_drive_age_seconds': None if self.last_drive_time is None else max(0, self.clock() - self.last_drive_time),
                    'serial_diagnostics': self.serial_diagnostics,
                    'motion_feedback': 'unverified', 'stop_feedback': 'unverified'}

    def command(self, data):
        if not isinstance(data, dict) or not isinstance(data.get('action'), str):
            raise ControlError('invalid_command')
        action = data['action']
        with self.lock:
            self._check_locked()
            if action == 'stop':
                if set(data) != {'action'}:
                    raise ControlError('invalid_command')
                self._stop_locked('operator_stop', disarm=True)
                return self.snapshot()
            if self.closed or self.fault:
                raise ControlError('serial_fault_restart_required')
            if action == 'arm':
                if set(data) not in ({'action'}, {'action', 'wheels_raised'}) or data.get('wheels_raised', True) is not True:
                    raise ControlError('invalid_command')
                if self.session:
                    raise ControlError('already_in_use')
                self._stop_locked('armed', disarm=True)
                if self.fault:
                    raise ControlError('serial_fault_restart_required')
                self.session = secrets.token_urlsafe(24)
                self.seq = -1
                self.idle_deadline = self.clock() + self.arm_idle_seconds
                return {**self.snapshot(), 'session': self.session}
            if action not in ('drive', 'release'):
                raise ControlError('invalid_command')
            expected = {'action', 'session', 'seq'} | ({'direction', 'speed'} if action == 'drive' else set())
            if set(data) != expected:
                raise ControlError('invalid_command')
            session, seq = data.get('session'), data.get('seq')
            if not self.session or not isinstance(session, str) or not hmac.compare_digest(session.encode('utf-8'), self.session.encode('utf-8')):
                raise ControlError('not_armed')
            if type(seq) is not int or seq <= self.seq or seq > 2**53 - 1:
                raise ControlError('stale_command')
            values = mix(data['direction'], data['speed']) if action == 'drive' else None
            self.seq = seq
            if action == 'release':
                self._stop_locked('key_released')
            else:
                # A direction change must cross a stop before reversing a motor.
                if any(self.values) and any(a * b < 0 for a, b in zip(self.values, values)):
                    self._stop_locked('direction_change')
                    if self.fault:
                        raise ControlError('serial_fault_restart_required')
                try:
                    command = 'spd' if self.mode == 'speed' else 'pwm'
                    self._write(('$' + command + ':' + ','.join(map(str, values)) + '#').encode('ascii'))
                except Exception:
                    self.fault = 'serial_write_failed'
                    self._stop_locked('serial_write_failed', disarm=True)
                    raise ControlError('serial_write_failed')
                now = self.clock()
                self.drive_commands_sent += 1
                self.last_drive_time = now
                self.values = values
                self.deadline = now + self.lease_seconds
                self.reason = 'command_sent'
            self.idle_deadline = self.clock() + self.arm_idle_seconds
            return self.snapshot()

    def drive_sides(self, session, seq, left, right):
        """Internal autonomous bridge; shares the existing owner, lease and wire.

        Not an HTTP command: DrivingMode alone arbitrates automatic ownership.
        Existing manual command/mix and serial configuration remain unchanged.
        Values are controller setpoints in this instance's existing mode, not
        measured wheel speed, and never change firmware configuration.
        """
        with self.lock:
            self._check_locked()
            if self.closed or self.fault:
                raise ControlError('serial_fault_restart_required')
            if not self.session or not isinstance(session, str) or not hmac.compare_digest(
                    session.encode('utf-8'), self.session.encode('utf-8')):
                raise ControlError('not_armed')
            if type(seq) is not int or seq <= self.seq or seq > 2**53 - 1:
                raise ControlError('stale_command')
            if any(type(value) is not int or not -220 <= value <= 220 for value in (left, right)):
                raise ControlError('invalid_sides')
            values = (left, left, right, right)
            self.seq = seq
            # Both signed spd and pwm frames carry all four new targets at once.
            # Autonomous corrections update those targets directly; explicit
            # stop/release/fault handling and manual reversal stay unchanged.
            try:
                command = 'spd' if self.mode == 'speed' else 'pwm'
                self._write(('$' + command + ':' + ','.join(map(str, values)) + '#').encode('ascii'))
            except Exception:
                self.fault = 'serial_write_failed'
                self._stop_locked('serial_write_failed', disarm=True)
                raise ControlError('serial_write_failed')
            now = self.clock()
            self.drive_commands_sent += 1
            self.last_drive_time = now
            self.values = values
            self.deadline = now + self.lease_seconds
            self.idle_deadline = now + self.arm_idle_seconds
            self.reason = 'autonomous_command_sent'
            return self.snapshot()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(1)
        with self.lock:
            self.closed = True
            self._stop_locked('server_shutdown', disarm=True)
            self.transport.close()
