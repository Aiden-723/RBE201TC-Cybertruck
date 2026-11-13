"""Line-led straight driving; all-WHITE triggers a configured IMU quarter turn.

The base class retains motor ownership, GPIO mapping, STOP and heartbeat rules.
No outer-probe pivot latch or timed lost-line continuation is used in this policy.
"""
import math
from basic_line_follower import BasicLineFollower, CENTER, WHITE


class ImuLineFollower(BasicLineFollower):
    control_policy = 'line_straight_imu_quarter_turn_v1'
    scale_pair_to_speed = False
    decision_errors = {'imu_not_live', 'imu_not_calibrated', 'imu_origin_changed',
                       'route_complete', 'line_not_found_after_turn'}
    angle_tolerance = 3.0

    def __init__(self, motor, line_source, imu, config, **kwargs):
        self.imu = imu
        turns = config.get('turns', [])
        if not isinstance(turns, list) or any(x not in ('left', 'right') for x in turns):
            raise ValueError('invalid_route_turns')
        self.turns = tuple(turns)
        self.offset_s = config.get('turn_offset_seconds', 0.0)
        if type(self.offset_s) not in (int, float) or not math.isfinite(self.offset_s) or not 0 <= self.offset_s <= 1:
            raise ValueError('invalid_turn_offset')
        self.turn_index = 0
        self.target_heading = 0.0
        self.phase = 'follow'
        self.seen_line = False
        self.turn_started = self.imu_revision = None
        self.last_heading = None
        self.heading_sample = None
        super().__init__(motor, line_source, **kwargs)

    def _imu_locked(self, now):
        value = self.imu.snapshot()
        mono = value.get('monotonic')
        if (value.get('status') != 'live' or type(mono) not in (int, float) or
                not math.isfinite(mono) or not 0 <= now - mono <= self.max_sensor_age):
            raise ValueError('imu_not_live')
        if not value.get('calibrated'):
            raise ValueError('imu_not_calibrated')
        if self.imu_revision is not None and value.get('revision') != self.imu_revision:
            raise ValueError('imu_origin_changed')
        heading = value.get('heading_deg')
        if type(heading) not in (int, float) or not math.isfinite(heading):
            raise ValueError('imu_not_live')
        self.last_heading = heading
        self.heading_sample = {key: value.get(key) for key in ('monotonic', 'successes', 'revision', 'age_seconds')}
        return value

    def start(self, speed=20):
        with self._lock:
            value = self._imu_locked(self.clock())
            if not self.turns:
                raise ValueError('route_not_configured')
            # Retain completed route progress across Stop/Start. Calibrate restarts the course.
            self.imu_revision = value['revision']
            self.seen_line = False
            if self.phase == 'offset':
                self.turn_started = self.clock()
            return super().start(speed)

    def reset_route(self):
        with self._lock:
            if self._running:
                raise ValueError('stop_before_calibration')
            self.turn_index, self.target_heading = 0, 0.0
            self.phase, self.seen_line = 'follow', False
            self.imu_revision = None

    def _snapshot_locked(self):
        state = super()._snapshot_locked()
        imu = self.imu.snapshot()
        state.update(phase=self.phase, turn_index=self.turn_index, turn_count=len(self.turns),
                     target_heading_deg=self.target_heading, heading_deg=self.last_heading,
                     turn_offset_seconds=self.offset_s)
        if not self._running:
            if not self.turns:
                state.update(ready=False, reason='route_not_configured')
            elif imu['status'] != 'live':
                state.update(ready=False, reason='imu_not_live')
            elif not imu['calibrated']:
                state.update(ready=False, reason='imu_not_calibrated')
        return state

    def _diagnostic_locked(self):
        return {**super()._diagnostic_locked(), 'phase': self.phase, 'turn_index': self.turn_index,
                'target_heading_deg': self.target_heading, 'heading_deg': self.last_heading,
                'imu_sample': self.heading_sample}

    def _follow_pair(self, values, heading):
        # Both middle probes on black: keep going through broad corner markings.
        # Otherwise use every visible probe's left/right position for forward differential drive.
        if values[1] == values[2] == 0:
            if values == CENTER and abs(heading - self.target_heading) >= 25:
                if heading < self.target_heading:
                    return self._speed // 2, self._speed, 'heading_left', -1.0
                return self._speed, self._speed // 2, 'heading_right', 1.0
            return self._speed, self._speed, 'center', 0.0
        positions = [p for p, bit in zip((-3, -1, 1, 3), values) if bit == 0]
        error = sum(positions) / len(positions)
        inner = max(0, round(self._speed * (1 - .4 * abs(error))))
        if error < 0:
            return inner, self._speed, 'track_left', error
        if error > 0:
            return self._speed, inner, 'track_right', error
        return self._speed, self._speed, 'center', 0.0

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        heading = self._imu_locked(now)['heading_deg']
        if self.phase == 'align':
            if not fresh:
                return 0, 0, 'turn_settling', 0.0
            if values == WHITE:
                raise ValueError('line_not_found_after_turn')
            self.phase, self.seen_line = 'follow', True
        if self.phase == 'follow':
            if values != WHITE:
                self.seen_line = True
                return self._follow_pair(values, heading)
            if not self.seen_line:
                raise ValueError('line_lost')
            if self.turn_index >= len(self.turns):
                raise ValueError('route_complete')
            self.turn_started = now
            self.phase = 'offset'
        if self.phase == 'offset':
            if now - self.turn_started < self.offset_s:
                return self._speed, self._speed, 'corner_offset', 0.0
            self.target_heading += 90 if self.turns[self.turn_index] == 'left' else -90
            self.phase = 'turn'
            self.turn_sign = 1 if self.turns[self.turn_index] == 'left' else -1
        # During a turn, GPIO colour does not choose or finish the turn.
        error = self.target_heading - heading
        remaining = self.turn_sign * error
        if remaining <= self.angle_tolerance:
            self.turn_index += 1
            self.phase = 'align'
            self.seen_line = False
            return 0, 0, 'turn_angle_reached', 0.0
        speed = self._speed if remaining > 15 else max(1, self._speed // 2)
        return (-speed, speed, 'imu_turn_left', -1.0) if self.turn_sign > 0 else (
            speed, -speed, 'imu_turn_right', 1.0)
