"""One manual/automatic arbiter over the existing single motor owner."""
import threading
from motor_control import ControlError
from basic_line_follower import BasicLineFollower


class DrivingMode:
    def __init__(self, motor=None, line_source=None, *, follower=None):
        self.motor = motor
        self.mode = 'manual'
        self.lock = threading.RLock()
        self.closed = False
        self.follower = follower if follower is not None else (
            BasicLineFollower(motor, line_source) if motor is not None and line_source is not None else None)

    def snapshot(self):
        with self.lock:
            state = self.motor.snapshot() if self.motor else {
                'enabled': False, 'armed': False, 'state': 'disabled',
                'outputs': [0, 0, 0, 0], 'fault': None}
            autonomy = self.follower.snapshot() if self.follower else {
                'ready': False, 'running': False, 'state': 'not_configured'}
            return {**state, 'driving_mode': self.mode, 'autonomy': autonomy}

    def _stop(self, reason):
        if self.follower:
            self.follower.stop(reason)
        if self.motor:
            return self.motor.command({'action': 'stop'})
        return {'outputs': [0, 0, 0, 0], 'armed': False, 'fault': None}

    def command(self, data):
        if not isinstance(data, dict):
            raise ValueError('invalid_body')
        action = data.get('action')
        with self.lock:
            if self.closed and action != 'stop':
                raise ControlError('control_closed')
            if action == 'set_mode':
                if set(data) != {'action', 'mode'} or data['mode'] not in ('manual', 'automatic'):
                    raise ValueError('invalid_mode')
                if data['mode'] != self.mode:
                    # Same arbitration lock as manual requests. Stop/revoke the
                    # executor before publishing the new mode to any requester.
                    state = self._stop('mode_changed')
                    if state.get('fault') or state.get('armed') or any(state.get('outputs', [1])):
                        raise ControlError('mode_stop_failed')
                    self.mode = data['mode']
                return self.snapshot()
            if action == 'autonomy_start':
                if set(data) not in ({'action'}, {'action', 'speed'}):
                    raise ValueError('invalid_autonomy_start')
                if self.follower is None:
                    raise ControlError('autonomy_not_configured')
                if self.mode != 'automatic':
                    raise ControlError('automatic_mode_required')
                owner = self.follower.start(speed=data.get('speed', 20))
                # The executor is only armed here. A first valid, explicit
                # owner heartbeat is required before it can issue motion.
                return {**self.snapshot(), 'autonomy_session': owner}
            if action == 'autonomy_heartbeat':
                if set(data) not in ({'action', 'autonomy_session', 'seq'}, {'action', 'autonomy_session', 'seq', 'speed'}):
                    raise ValueError('invalid_autonomy_heartbeat')
                if self.mode != 'automatic' or self.follower is None:
                    raise ControlError('automatic_mode_required')
                self.follower.heartbeat(data['autonomy_session'], data['seq'], **({'speed': data['speed']} if 'speed' in data else {}))
                return self.snapshot()
            if action == 'stop':
                if set(data) != {'action'}:
                    raise ControlError('invalid_command')
                self._stop('operator_stop')
                return self.snapshot()
            if action in ('arm', 'drive') and self.mode != 'manual':
                raise ControlError('manual_mode_required')
            if self.motor is None:
                raise ControlError('motor_unavailable')
            result = self.motor.command(data)
            return {**self.snapshot(), **result}  # Preserve manual arm's session response.

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.follower:
                self.follower.close()
            if self.motor:
                self.motor.command({'action': 'stop'})
