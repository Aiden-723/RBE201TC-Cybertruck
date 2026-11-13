"""Forward line correction, contextual corner departure, relative IMU turns.

Keep the previous IMU controller as a rollback. Straight tracking uses only
the four probes; a drifting absolute yaw must not steer a centered vehicle.
"""
from basic_line_follower import CENTER, WHITE
from imu_line_follower import ImuLineFollower


class TrackImuFollower(ImuLineFollower):
    control_policy = 'line_forward_context_imu_turn_v1'
    autonomy_speeds = (20, 30, 40)
    minimum_running_output = 20
    minimum_turn_output = 40

    def __init__(self, *args, **kwargs):
        self._reset_departure()
        super().__init__(*args, **kwargs)

    def _reset_departure(self):
        self._corner_hint = False
        self._departure_kind = None
        self._white_mode = None

    def start(self, speed=20):
        if type(speed) is not int or speed not in self.autonomy_speeds:
            raise ValueError('invalid_speed')
        with self._lock:
            if self._running:
                raise ValueError('autonomy_already_running')
            self._reset_departure()
            return super().start(speed)

    def reset_route(self):
        with self._lock:
            super().reset_route()
            self._reset_departure()

    def _snapshot_locked(self):
        state = super()._snapshot_locked()
        state.update(imu_required=True, speed_gears=list(self.autonomy_speeds),
                     minimum_running_output=self.minimum_running_output,
                     minimum_turn_output=self.minimum_turn_output,
                     departure_kind=self._departure_kind, white_mode=self._white_mode,
                     next_turn_ready=self._corner_eligible())
        return state

    def _diagnostic_locked(self):
        return {**super()._diagnostic_locked(), 'departure_kind': self._departure_kind,
                'corner_hint': self._corner_hint, 'white_mode': self._white_mode,
                'next_turn_ready': self._corner_eligible()}

    def _corner_eligible(self):
        return self.seen_line and self.phase == 'follow' and self._departure_kind in ('centered', 'corner_edge')

    @staticmethod
    def _line_error(values):
        positions = [p for p, bit in zip((-3, -1, 1, 3), values) if bit == 0]
        return sum(positions) / len(positions)

    def _follow_pair(self, values, heading):
        # Restore the inner-probe 60% / 100% forward differential relationship.
        # Keep both sides powered: the old 10/6, 10/2 and 40/0 pairs stall TT motors.
        speed = max(self.minimum_running_output, self._speed)
        if values[1] == values[2] == 0:
            return speed, speed, 'center' if values == CENTER else 'broad_line', 0.0
        error = self._line_error(values)
        if not error:
            return speed, speed, 'broad_line', 0.0
        ratio = max(.5, 1 - .4 * abs(error))
        outer = min(40, max(speed, round(self.minimum_running_output / ratio)))
        inner = max(self.minimum_running_output, round(outer * ratio))
        return (inner, outer, 'track_left', error) if error < 0 else (
            outer, inner, 'track_right', error)

    def _observe_line(self, values):
        expected = -1 if self.turns[self.turn_index:self.turn_index+1] == ('left',) else 1
        if values[1] == values[2] == 0:
            # A broad branch on the planned side can precede that side's edge.
            # A plain centered sample clears older branch evidence.
            if values == CENTER:
                self._corner_hint = False
            elif values[0 if expected < 0 else 3] == 0 and values[3 if expected < 0 else 0] == 1:
                self._corner_hint = True
            self._departure_kind = 'centered'
        else:
            error = self._line_error(values)
            if not error or error * expected <= 0:
                self._corner_hint = False
            self._departure_kind = 'corner_edge' if self._corner_hint else 'side_exit'

    def _powered_turn_pair_locked(self, heading, direction, base_speed, label):
        # Hold a useful command until angle feedback changes it. No weak 5/10
        # endpoint pulses. Retain the existing IMU-confirmed stalled-turn boost.
        return super()._powered_turn_pair_locked(
            heading, direction, max(self.minimum_turn_output, base_speed), label)

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        heading = self._imu_locked(now)['heading_deg']
        if self.phase == 'align':
            if not fresh:
                return self._left, self._right, self._band or 'turn_settling', self._error or 0.0
            if values == WHITE:
                raise ValueError('line_not_found_after_turn')
            if self._confirm_center_locked(values, fresh):
                self.turn_index += 1
                self.phase, self.seen_line = 'follow', True
                self._reset_departure()
            self._observe_line(values)
            return self._follow_pair(values, heading)

        if self.phase == 'follow':
            if values != WHITE:
                self._white_samples = 0
                self._white_mode = None
                if not self.seen_line and self._confirm_center_locked(values, fresh):
                    self.seen_line = True
                if fresh:
                    self._observe_line(values)
                return self._follow_pair(values, heading)
            if not self.seen_line:
                raise ValueError('line_lost')
            if self._white_mode is None:
                self._white_mode = ('corner' if self._departure_kind in ('centered', 'corner_edge')
                                    else 'recover')
            if self._white_mode == 'recover':
                # Lateral departure is NOT the next route corner. Continue the
                # actual forward correction, with the existing loss ceiling.
                if self._line_gap_started is None:
                    self._line_gap_started = mono
                    self._line_gap_limit_s = self.line_loss_limit_s
                self._gap_deadline_locked(now)
                label = 'recover_left' if self._left < self._right else 'recover_right'
                return self._left, self._right, label, self._error or 0.0
            if self.turn_index >= len(self.turns):
                raise ValueError('route_complete')
            if fresh:
                self._white_samples += 1
            if self._white_samples < self.white_confirmation_samples:
                return self._left, self._right, 'white_confirmation', 0.0
            self._white_samples = 0
            self.turn_started = now
            self.phase, self.seen_line = 'offset', False
            self._center_samples = 0

        if self.phase == 'offset':
            if now - self.turn_started < self.offset_s:
                return self._speed, self._speed, 'corner_offset', 0.0
            self.turn_entry_heading = heading
            self.turn_sign = 1 if self.turns[self.turn_index] == 'left' else -1
            self.target_heading = heading + self.turn_sign * 90
            self.phase = 'turn'
            self._reset_angle_locked()
            self.angle_stage = 'coarse'
        # Probe changes cannot end an IMU turn before its angle is verified.
        return self._angle_pair_locked(heading)
