"""Line-led straight driving; all-WHITE triggers a configured IMU quarter turn.

The base class retains motor ownership, GPIO mapping, STOP and heartbeat rules.
No outer-probe pivot latch or timed lost-line continuation is used in this policy.
"""
import math
from basic_line_follower import BasicLineFollower, CENTER, WHITE


class ImuLineFollower(BasicLineFollower):
    control_policy = 'line_straight_imu_quarter_turn_v4'
    scale_pair_to_speed = False
    decision_errors = {'imu_not_live', 'imu_not_calibrated', 'imu_origin_changed',
                       'route_complete', 'line_not_found_after_turn'}
    angle_tolerance = 2.0
    angle_stable_seconds = .12
    angle_stable_change = .5
    fine_speed_limit = 10
    white_confirmation_samples = 2

    def __init__(self, motor, line_source, imu, config, **kwargs):
        self.imu = imu
        turns = config.get('turns', [])
        if not isinstance(turns, list) or any(x not in ('left', 'right') for x in turns):
            raise ValueError('invalid_route_turns')
        self.turns = tuple(turns)
        self.offset_s = config.get('turn_offset_seconds', 0.0)
        if type(self.offset_s) not in (int, float) or not math.isfinite(self.offset_s) or not 0 <= self.offset_s <= 1:
            raise ValueError('invalid_turn_offset')
        self._white_samples = 0
        self.turn_index = 0
        self.target_heading = 0.0
        self.phase = 'follow'
        self.seen_line = False
        self.turn_started = self.imu_revision = None
        self.turn_entry_heading = None
        self.last_heading = None
        self.heading_sample = None
        self._reset_angle_locked()
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
            if self._running:
                raise ValueError('autonomy_already_running')
            # The existing Start button starts a NEW recorded test, not a resume.
            self.imu_revision = value['revision']
            self._white_samples = 0
            self.turn_index = 0
            self.target_heading = value['heading_deg']
            self.turn_entry_heading = self.turn_started = None
            self.phase, self.seen_line = 'follow', False
            self._reset_angle_locked()
            return super().start(speed)

    def reset_route(self):
        with self._lock:
            if self._running:
                raise ValueError('stop_before_calibration')
            self._white_samples = 0
            self.turn_index, self.target_heading = 0, 0.0
            self.phase, self.seen_line = 'follow', False
            self.imu_revision = None
            self.turn_entry_heading = self.turn_started = None
            self._center_samples = 0
            self._reset_angle_locked()

    def _snapshot_locked(self):
        state = super()._snapshot_locked()
        imu = self.imu.snapshot()
        state.update(phase=self.phase, turn_index=self.turn_index, turn_count=len(self.turns),
                     target_heading_deg=self.target_heading, heading_deg=self.last_heading,
                     turn_offset_seconds=self.offset_s, turn_entry_heading_deg=self.turn_entry_heading,
                     next_turn_ready=self.seen_line and self.phase == 'follow',
                     white_samples=self._white_samples, white_confirmation_samples=self.white_confirmation_samples,
                     angle_stage=self.angle_stage, angle_tolerance_deg=self.angle_tolerance,
                     angle_error_deg=(self.target_heading - self.last_heading) if self.last_heading is not None else None)
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
                'imu_sample': self.heading_sample, 'turn_entry_heading_deg': self.turn_entry_heading,
                'next_turn_ready': self.seen_line and self.phase == 'follow',
                'white_samples': self._white_samples, 'white_confirmation_samples': self.white_confirmation_samples,
                'angle_stage': self.angle_stage, 'angle_error_deg': (self.target_heading-self.last_heading) if self.last_heading is not None else None}

    def _reset_angle_locked(self):
        self.angle_stage = None
        self._settle_started = self._settle_heading = None
        self._correction_sign = None

    def _drive_pair_locked(self, left, right):
        if self.phase == 'turn' and self.angle_stage in ('brake', 'correct'):
            return self.motor.drive_sides(self._motor_session, self._motor_seq, left, right, precision=True)
        return super()._drive_pair_locked(left, right)

    def _brake_angle_locked(self):
        self.angle_stage = 'brake'
        self._settle_started = self._settle_heading = None

    def _angle_pair_locked(self, heading):
        error = self.target_heading - heading
        if self.angle_stage == 'coarse':
            if self.turn_sign * error > 15:
                return (-self._speed, self._speed, 'imu_turn_left', -1.0) if self.turn_sign > 0 else (
                    self._speed, -self._speed, 'imu_turn_right', 1.0)
            # Stop the main turn and clear its output before measuring the endpoint.
            self._brake_angle_locked()
        if self.angle_stage == 'brake':
            stamp = self.heading_sample['monotonic']
            if self._left or self._right:
                self._settle_started = self._settle_heading = None
            elif self._settle_started is None or abs(heading-self._settle_heading) > self.angle_stable_change:
                self._settle_started, self._settle_heading = stamp, heading
            elif stamp-self._settle_started >= self.angle_stable_seconds:
                if abs(error) <= self.angle_tolerance:
                    self.phase, self.angle_stage = 'align', 'done'
                    self._center_samples = 0
                    return 0, 0, 'turn_angle_reached', 0.0
                self.angle_stage = 'correct'
                self._correction_sign = 1 if error > 0 else -1
            if self.angle_stage == 'brake':
                return 0, 0, 'turn_braking', 0.0
        # Fixed target, signed error: under-turn continues; over-turn reverses.
        direction = 1 if error > 0 else -1
        if abs(error) <= self.angle_tolerance or direction != self._correction_sign:
            self._brake_angle_locked()
            return 0, 0, 'turn_braking', 0.0
        speed = max(5, min(self._speed, self.fine_speed_limit, round(abs(error)*2)))
        return (-speed, speed, 'turn_correct_left', -1.0) if direction > 0 else (
            speed, -speed, 'turn_correct_right', 1.0)

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

    def _confirm_center_locked(self, values, fresh):
        if fresh:
            self._center_samples = self._center_samples + 1 if values == CENTER else 0
        return self._center_samples >= self.center_confirmation_samples

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        heading = self._imu_locked(now)['heading_deg']
        if self.phase == 'align':
            if not fresh:
                return self._left, self._right, self._band or 'turn_settling', self._error or 0.0
            if values == WHITE:
                raise ValueError('line_not_found_after_turn')
            # Partial black readings are followed, but cannot unlock another turn.
            # Finish this corner only after distinct fresh centered samples.
            if self._confirm_center_locked(values, fresh):
                self.turn_index += 1
                self.phase, self.seen_line = 'follow', True
            return self._follow_pair(values, heading)
        if self.phase == 'follow':
            if values != WHITE:
                self._white_samples = 0
                if not self.seen_line and self._confirm_center_locked(values, fresh):
                    self.seen_line = True
                return self._follow_pair(values, heading)
            if not self.seen_line:
                raise ValueError('line_lost')
            if self.turn_index >= len(self.turns):
                raise ValueError('route_complete')
            if fresh:
                self._white_samples += 1
            if self._white_samples < self.white_confirmation_samples:
                # Keep the last line-following pair while checking a second NEW
                # GPIO publication. Do not pivot or pulse zero for one white read.
                peak = max(abs(self._left), abs(self._right))
                scale = self._speed / peak if peak else 0
                return round(self._left * scale), round(self._right * scale), 'white_confirmation', 0.0
            self._white_samples = 0
            self.turn_started = now
            self.phase, self.seen_line = 'offset', False
            self._center_samples = 0
        if self.phase == 'offset':
            if now - self.turn_started < self.offset_s:
                return self._speed, self._speed, 'corner_offset', 0.0
            self.turn_entry_heading = heading
            self.turn_sign = 1 if self.turns[self.turn_index] == 'left' else -1
            # Every physical turn is a quarter turn from its own entry heading.
            # Never add 90 to a target left over from an earlier test or corner.
            self.target_heading = heading + self.turn_sign * 90
            self.phase = 'turn'
            self.angle_stage = 'coarse'
        # During the turn, GPIO colour does not select or finish rotation.
        return self._angle_pair_locked(heading)
