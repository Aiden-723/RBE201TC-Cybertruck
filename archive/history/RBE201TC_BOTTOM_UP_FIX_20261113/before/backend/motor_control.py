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
    inner = max(20, speed // 5)
    # Forward arcs keep all four wheels forward. A 5:1 command ratio gives
    # a pronounced yaw without STOP, zero inner output or counter-rotation.
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
    startup_boost_speed = 200
    startup_boost_seconds = .2

    def __init__(self, transport, clock=time.monotonic, mode='pwm', *, startup_boost=False):
        if mode not in ('pwm', 'speed'):
            raise ValueError('invalid motor mode')
        if type(startup_boost) is not bool:
            raise ValueError('invalid_startup_boost')
        self.startup_boost_enabled = startup_boost
        self.requested_values = (0, 0, 0, 0)
        self._boost_until = None
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
        self.speed_transition_resets = 0
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
        self.requested_values = (0, 0, 0, 0)
        self._boost_until = None
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

    def _write_targets_locked(self, values):
        try:
            # In the current no-feedback startup profile, a zero spd target does
            # not clear old PID output. Clear it before stopping/reversing a
            # previously driven channel; preserve normal same-sign stepdowns.
            if (self.startup_boost_enabled and self.mode == 'speed' and
                    any(old != 0 and (new == 0 or old * new < 0)
                        for old, new in zip(self.values, values))):
                self._write(b'$pwm:0,0,0,0#')
                self.speed_transition_resets += 1
            command = 'spd' if self.mode == 'speed' else 'pwm'
            self._write(('$' + command + ':' + ','.join(map(str, values)) + '#').encode('ascii'))
        except Exception:
            self.fault = 'serial_write_failed'
            self._stop_locked('serial_write_failed', disarm=True)
            raise ControlError('serial_write_failed')
        self.drive_commands_sent += 1
        self.last_drive_time = self.clock()
        self.values = values

    def _submit_targets_locked(self, values, *, continuous=False, allow_boost=True):
        changed = values != self.requested_values
        # Manual actions retain the user's 200 / .2s behavior. Autonomous
        # same-direction magnitude adjustments are running corrections, not starts.
        restart = (any(new and (old == 0 or old * new < 0)
                       for old, new in zip(self.requested_values, values))
                   if continuous else changed)
        boosting = (allow_boost and self.startup_boost_enabled and any(values) and
                    (restart or (self._boost_until is not None and self.clock() < self._boost_until)))
        # Preserve arc ratios during startup too: equal +/-200 would briefly
        # turn a forward+steer command into a zero-travel pivot.
        proportional = continuous or len({abs(value) for value in values if value}) > 1
        if boosting and proportional:
            peak = max(map(abs, values))
            output = tuple(int(value * self.startup_boost_speed / peak) for value in values)
        elif boosting:
            output = tuple(self.startup_boost_speed * (1 if value > 0 else -1) if value else 0
                           for value in values)
        else:
            output = values
        self._write_targets_locked(output)
        self.requested_values = values
        if boosting and restart:
            self._boost_until = self.last_drive_time + self.startup_boost_seconds
        elif not boosting:
            self._boost_until = None

    def _finish_boost_locked(self):
        if self._boost_until is None or self.clock() < self._boost_until:
            return
        self._boost_until = None
        if self.session and not self.closed and not self.fault and self.values != self.requested_values:
            # This is a target change, never a new lease or an owner heartbeat.
            self._write_targets_locked(self.requested_values)

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
            self._check_locked()  # Expiry/STOP takes priority over finishing a boost.
            try:
                self._finish_boost_locked()
            except ControlError:
                pass  # The write path has already latched the fault and stopped.

    def snapshot(self):
        with self.lock:
            self._check_locked()
            return {'enabled': True, 'armed': self.session is not None,
                    'state': 'fault' if self.fault else 'driving' if any(self.values) else 'armed' if self.session else 'stopped',
                    'outputs': list(self.values), 'command_mode': self.mode,
                    'requested_outputs': list(self.requested_values),
                    'startup_boost_enabled': self.startup_boost_enabled,
                    'startup_boost_speed': self.startup_boost_speed,
                    'startup_boost_duration_s': self.startup_boost_seconds,
                    'startup_boost_active': self._boost_until is not None,
                    'pwm': list(self.values) if self.mode == 'pwm' else None,
                    'speed_setpoints': list(self.values) if self.mode == 'speed' else None,
                    'reason': self.reason, 'fault': self.fault,
                    'lease_ms': round(self.lease_seconds * 1000), 'max_hold_seconds': None,
                    'mapping': {'left': ['M1', 'M2'], 'right': ['M3', 'M4'], 'verified': False},
                    'drive_commands_sent': self.drive_commands_sent,
                    'speed_transition_resets': self.speed_transition_resets,
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
                self._submit_targets_locked(values)
                now = self.last_drive_time
                self.deadline = now + self.lease_seconds
                self.reason = 'command_sent'
            self.idle_deadline = self.clock() + self.arm_idle_seconds
            return self.snapshot()

    def drive_sides(self, session, seq, left, right, *, precision=False):
        """Internal autonomous bridge; shares the existing owner, lease and wire.

        Not an HTTP command: DrivingMode alone arbitrates automatic ownership.
        Existing manual command/mix and serial configuration remain unchanged.
        Values are controller setpoints in this instance's existing mode, not
        measured wheel speed, and never change firmware configuration.
        precision suppresses startup assist only for internal angle corrections.
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
            if type(precision) is not bool:
                raise ControlError('invalid_precision_mode')
            values = (left, left, right, right)
            self.seq = seq
            # Speed targets already encode direction. A tracking correction is
            # not a global STOP: keep spd updates continuous across sign changes.
            if self.mode != 'speed' and any(self.values) and any(a * b < 0 for a, b in zip(self.values, values)):
                self._stop_locked('direction_change')
                if self.fault:
                    raise ControlError('serial_fault_restart_required')
            # Endpoint corrections must not restart the normal 200/.2s assist.
            self._submit_targets_locked(values, continuous=True, allow_boost=not precision)
            now = self.last_drive_time
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
