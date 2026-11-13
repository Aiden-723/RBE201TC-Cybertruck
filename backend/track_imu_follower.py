"""Active policy: strict autonomous output cap20; no forward lost-line search."""
from basic_line_follower import WHITE
from positive_arc_follower import PositiveArcFollower


class TrackImuFollower(PositiveArcFollower):
    control_policy = 'line_hard20_stop_loss_v5'
    autonomy_speeds = (20,)
    correction_outer = 20
    correction_inner = 4
    minimum_running_output = 4
    minimum_turn_output = 20
    max_correction_speed = 20
    turn_power_limit = 20

    def _drive_pair_locked(self, left, right):
        left, right = max(-20, min(20, left)), max(-20, min(20, right))
        # All autonomous commands bypass the manual 200/.2s startup assist.
        driven = self.motor.drive_sides(self._motor_session, self._motor_seq,
                                        left, right, precision=True)
        if self.phase == 'reverse' and self.reverse_started is None and left < 0 and right < 0:
            self.reverse_started = self.clock()
        return driven

    def _powered_turn_pair_locked(self, heading, direction, base_speed, label):
        return (-20, 20, label, -1.0) if direction > 0 else (20, -20, label, 1.0)

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        if self.phase == 'follow' and values == WHITE and self._departure_kind == 'side_exit':
            raise ValueError('line_lost')
        return super()._decide_locked(values, fresh, mono, now, hold_pattern)
