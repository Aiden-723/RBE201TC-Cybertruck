"""Known-route line-only test policy; never reads or calibrates an IMU.

Follow -> ten fresh WHITE reads -> planned pivot -> line reacquisition -> follow.
The digital line alone cannot measure a 90-degree chassis angle or distance.
"""
from basic_line_follower import BasicLineFollower, CENTER, WHITE


def forward_pair(values, speed):
    if values[1] == values[2] == 0:
        return speed, speed, 'center' if values == CENTER else 'broad_line', 0.0
    positions = [p for p, bit in zip((-3, -1, 1, 3), values) if bit == 0]
    error = sum(positions) / len(positions)
    inner = max(0, round(speed * (1-.4*abs(error))))
    if error < 0:
        return inner, speed, 'track_left', error
    if error > 0:
        return speed, inner, 'track_right', error
    return speed, speed, 'center', 0.0


class RouteLineFollower(BasicLineFollower):
    control_policy = 'line_route_no_imu_v1'
    scale_pair_to_speed = False
    white_confirmation_samples = 10
    decision_errors = {'route_complete'}

    def __init__(self, motor, line_source, config, **kwargs):
        turns = config.get('turns', [])
        if not isinstance(turns, list) or any(v not in ('left', 'right') for v in turns):
            raise ValueError('invalid_route_turns')
        self.turns = tuple(turns)
        self._reset_route_state()
        super().__init__(motor, line_source, **kwargs)

    def _reset_route_state(self):
        self.phase, self.turn_index = 'follow', 0
        self.seen_line = False
        self._white_samples = 0

    def start(self, speed=20):
        with self._lock:
            if self._running:
                raise ValueError('autonomy_already_running')
            if not self.turns:
                raise ValueError('route_not_configured')
            self._reset_route_state()
            return super().start(speed)

    def reset_route(self):
        with self._lock:
            if self._running:
                raise ValueError('stop_before_calibration')
            self._reset_route_state()
            self._center_samples = 0

    def _snapshot_locked(self):
        state = super()._snapshot_locked()
        state.update(phase=self.phase, turn_index=self.turn_index, turn_count=len(self.turns),
                     imu_required=False, turn_completion='fresh_centered_line',
                     white_samples=self._white_samples, white_confirmation_samples=self.white_confirmation_samples,
                     next_turn_ready=self.phase == 'follow' and self.seen_line)
        if not self.turns:
            state.update(ready=False, reason='route_not_configured')
        return state

    def _diagnostic_locked(self):
        return {**super()._diagnostic_locked(), 'phase': self.phase, 'turn_index': self.turn_index,
                'imu_required': False, 'white_samples': self._white_samples,
                'white_confirmation_samples': self.white_confirmation_samples,
                'next_turn_ready': self.phase == 'follow' and self.seen_line,
                'planned_direction': self.turns[self.turn_index] if self.turn_index < len(self.turns) else None}

    def _centered(self, values, fresh):
        if fresh:
            self._center_samples = self._center_samples+1 if values == CENTER else 0
        return self._center_samples >= self.center_confirmation_samples

    def _pivot_pair(self):
        return (-self._speed, self._speed, 'route_turn_left', -1.0) if self.turns[self.turn_index] == 'left' else (
            self._speed, -self._speed, 'route_turn_right', 1.0)

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        if self.phase == 'follow':
            if values != WHITE:
                self._white_samples = 0
                if not self.seen_line and self._centered(values, fresh):
                    self.seen_line = True
                return forward_pair(values, self._speed)
            if not self.seen_line:
                raise ValueError('line_lost')
            if self.turn_index >= len(self.turns):
                raise ValueError('route_complete')
            if fresh:
                self._white_samples += 1
            if self._white_samples < self.white_confirmation_samples:
                peak = max(abs(self._left), abs(self._right))
                scale = self._speed/peak if peak else 0
                return round(self._left*scale), round(self._right*scale), 'white_confirmation', 0.0
            self.phase, self.seen_line = 'turn', False
            self._white_samples = self._center_samples = 0
        if self.phase == 'turn':
            if values == WHITE or not fresh:
                return self._pivot_pair()
            # The line has returned: let the four probes steer onto it.
            self.phase = 'reacquire'
            self._center_samples = 0
        if values == WHITE:
            # Still acquiring THIS corner; never consume another planned turn.
            self.phase = 'turn'
            self._center_samples = 0
            return self._pivot_pair()
        if self._centered(values, fresh):
            self.turn_index += 1
            self.phase, self.seen_line = 'follow', True
        return forward_pair(values, self._speed)
