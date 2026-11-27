"""Hard-20 line tracking and relative IMU turns."""
from basic_line_follower import CENTER, WHITE
from positive_arc_follower import PositiveArcFollower


class TrackImuFollower(PositiveArcFollower):
    control_policy = 'line_track20_boost200_20261127'
    decision_errors = PositiveArcFollower.decision_errors | {'turn_angle_limit'}
    autonomy_speeds = (20,)
    white_confirmation_samples = 3
    minimum_widening_samples = 3
    reverse_seconds = 0.0
    minimum_reacquire_angle = 60.0
    maximum_turn_angle = 130.0
    sharp_minimum_reacquire_angle = 145.0
    sharp_maximum_turn_angle = 180.0
    correction_outer = 20
    correction_inner = 4
    minimum_running_output = 4
    minimum_turn_output = 20
    max_correction_speed = 20
    turn_power_limit = 20

    def __init__(self, motor, line_source, imu, config, **kwargs):
        self.final_sharp_right = config.get('final_sharp_right', False)
        if type(self.final_sharp_right) is not bool:
            raise ValueError('invalid_final_sharp_right')
        self._pwm_mode = getattr(motor, 'mode', None) == 'pwm'
        if self._pwm_mode:
            speed = 600
            self.control_policy = f'line_track_pwm{speed}_20261127'
            self.autonomy_speeds = (speed,)
            self.maximum_start_speed = speed
            self.minimum_running_output = self.minimum_turn_output = speed
            self.correction_outer = self.correction_inner = speed
            self.max_correction_speed = self.turn_power_limit = speed
        super().__init__(motor, line_source, imu, config, **kwargs)
        if self.final_sharp_right and self.turns[-1:] != ('right',):
            raise ValueError('final_sharp_turn_requires_right')

    def start(self, speed=None):
        return super().start(self.autonomy_speeds[0] if speed is None else speed)

    def _reset_departure(self):
        super()._reset_departure()
        self._crossed_full_width = False
        self._widening_samples = 0
        self._final_connector_seen = False
        self._final_connector_cleared = False

    def _is_final_sharp_turn(self):
        return self.final_sharp_right and self.turn_index == len(self.turns) - 1

    def _observe_line(self, values):
        middle_on_line = values[1] == values[2] == 0
        self._departure_kind = 'centered' if middle_on_line else 'side_exit'
        if self.phase == 'follow' and self._is_final_sharp_turn() and not self._final_connector_cleared:
            # The right branch widens the line while both middle probes stay on it.
            self._final_connector_seen |= middle_on_line and values[3] == 0
            if self._final_connector_seen:
                self._final_connector_cleared = self._confirm_center_locked(values, fresh=True)
        waiting_for_turn = self.phase == 'follow' and self.turn_index < len(self.turns)
        sustained_widening = self._widening_samples >= self.minimum_widening_samples
        previous = self._last_visible_values
        if not waiting_for_turn or values == CENTER:
            # Retain same-side evidence across one centered sample at a bend.
            self._corner_hint = waiting_for_turn and sustained_widening and previous != CENTER
            self._crossed_full_width = False
            if not self._corner_hint:
                self._widening_samples = 0
            return

        direction = -1 if self.turns[self.turn_index] == 'left' else 1
        outer, far_inner, opposite_outer = (0, 2, 3) if direction < 0 else (3, 1, 0)
        # The planned outer probe joins while the far inner probe can still
        # see the incoming line.
        extends_line = (previous is not None and previous[1] == previous[2] == 0
                        and previous[outer] == 1 and values[outer] == values[far_inner] == 0)
        # A full-width crossing after tracking was established also arms a
        # bend, including when the previous reading only saw the outer edge.
        crosses_line = self.seen_line and values == (0, 0, 0, 0)
        still_wide = values[outer] == values[far_inner] == 0
        planned_edge = values[outer] == 0 and values[opposite_outer] == 1
        self._crossed_full_width = crosses_line or (
            self._crossed_full_width and (still_wide or planned_edge)
        )
        widening_hint = extends_line or (self._corner_hint and still_wide)
        if widening_hint and planned_edge and middle_on_line:
            self._widening_samples = min(self._widening_samples + 1, self.minimum_widening_samples)
        elif not (sustained_widening and (planned_edge or crosses_line)):
            self._widening_samples = 0
        self._corner_hint = (self._crossed_full_width or widening_hint
                             or self._widening_samples >= self.minimum_widening_samples)
        if self._corner_hint:
            self._departure_kind = 'corner_edge'

    def _corner_eligible(self):
        if self._is_final_sharp_turn():
            return self._final_connector_cleared and super()._corner_eligible()
        return self._corner_hint and super()._corner_eligible()

    def _follow_pair(self, values, heading):
        if self.phase == 'follow' and self._is_final_sharp_turn() and values[1] == values[2] == 0:
            return super()._follow_pair(values, heading)
        error = self._line_error(values)
        if not error:
            return super()._follow_pair(values, heading)

        speed = self._speed
        if error < 0:
            left, right, label = -speed, speed, 'track_left'
        else:
            left, right, label = speed, -speed, 'track_right'
        if self._left * left < 0 or self._right * right < 0:
            return 0, 0, label, error
        return left, right, label, error

    def _drive_pair_locked(self, left, right):
        limit = self.autonomy_speeds[0]
        left, right = max(-limit, min(limit, left)), max(-limit, min(limit, right))
        return self.motor.drive_sides(
            self._motor_session, self._motor_seq, left, right,
            precision=self._pwm_mode, coalesce_unchanged=True,
        )

    def _turn_pair_locked(self, values, fresh, heading):
        minimum, maximum = self.minimum_reacquire_angle, self.maximum_turn_angle
        if self._is_final_sharp_turn():
            minimum, maximum = self.sharp_minimum_reacquire_angle, self.sharp_maximum_turn_angle
            self.target_heading = self.turn_entry_heading + self.turn_sign * maximum
        angle = self.turn_sign * (heading - self.turn_entry_heading)
        if minimum <= angle <= maximum and fresh and values != WHITE:
            self.phase, self.angle_stage = 'align', 'done'
            self._center_samples = 0
            return 0, 0, 'turn_angle_reached', 0.0
        if abs(angle) >= maximum:
            raise ValueError('turn_angle_limit')
        speed = self._speed
        if self.turn_sign > 0:
            return -speed, speed, 'route_turn_left', -1.0
        return speed, -speed, 'route_turn_right', 1.0

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        if (self.phase == 'follow' and values == WHITE and not self._corner_eligible()
                and (self.turn_index < len(self.turns) or self._departure_kind == 'side_exit')):
            raise ValueError('line_lost')
        return super()._decide_locked(values, fresh, mono, now, hold_pattern)
