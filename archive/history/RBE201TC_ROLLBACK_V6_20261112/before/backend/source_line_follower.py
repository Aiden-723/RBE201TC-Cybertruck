"""Pinned Yahboom four-digital rule adaptation over the existing UART owner.

Reference: YahboomTechnology/Tracking-module e5a9e261c549c89c5421e48ebe6df1b5244d7229
4.Raspberry Pi tutorials/tracking.py lines132-185. Truth table is derived from
its ordered conditions, not a new steering heuristic. GPIO/PWM code is not run.
Source PWM 35/40/45 are NOT converted to UART speed values. The previously
selected speed profile remains base84/outer200; actual dynamics need validation.
The explicit user rule 'no blind forward on white' overrides vendor WHITE hold.
"""
from basic_line_follower import BasicLineFollower, steering, WHITE

SOURCE_COMMIT = 'e5a9e261c549c89c5421e48ebe6df1b5244d7229'
# Independent fact table: exact branch outcome and special-branch dwell.
RULES = {'0000': ('spin_right', 0.08), '0001': ('spin_left', 0.08), '0010': ('spin_right', 0.08), '0011': ('spin_left', 0), '0100': ('spin_right', 0.08), '0101': ('spin_left', 0.08), '0110': ('spin_right', 0.08), '0111': ('spin_left', 0), '1000': ('spin_right', 0.08), '1001': ('run', 0), '1010': ('spin_right', 0.08), '1011': ('left', 0), '1100': ('spin_right', 0), '1101': ('right', 0), '1110': ('spin_right', 0), '1111': ('retain_previous', 0)}


def source_decision(values, speed):
    steering([1,0,0,1], speed)  # Existing strict speed validation.
    if not isinstance(values,(tuple,list)) or len(values)!=4 or any(type(v) is not int or v not in (0,1) for v in values):
        raise ValueError('invalid_line_values')
    action,dwell = RULES[''.join(map(str,values))]
    pairs = {'spin_right':(200,-200,'hard_right',3.0),
             'spin_left':(-200,200,'hard_left',-3.0),
             'left':(0,speed,'inner_left',-1.0),
             'right':(speed,0,'inner_right',1.0),
             'run':(speed,speed,'center',0.0)}
    return pairs.get(action), dwell, action


class SourceLineFollower(BasicLineFollower):
    control_policy = 'yahboom_four_digital_v8'

    def _reset_hold_locked(self):
        super()._reset_hold_locked()
        self._source_until = 0.0
        self._source_pair = None
        self._pending_dwell = 0.0

    def _classify_gap(self, values):
        # Original mixed/all-black branches are valid steering decisions.
        return 'white' if values is not None and tuple(values)==WHITE else None

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        if hold_pattern is not None:
            # Preserve the user's no-blind-forward guard, checked every tick.
            self._source_until = 0.0
            self._pending_dwell = 0.0
            return super()._decide_locked(values,fresh,mono,now,hold_pattern)
        if self._source_pair is not None and now < self._source_until:
            return self._source_pair
        decision,dwell,action = source_decision(values,self._speed)
        self._source_pair = decision
        self._pending_dwell = dwell
        return decision

    def _after_drive_locked(self, sent_at):
        # The source sleeps AFTER submitting the motor command. Start timing
        # here, not before the UART write/zero crossing. Never block safety.
        if self._pending_dwell:
            self._source_until = sent_at + self._pending_dwell
            self._pending_dwell = 0.0

    def _snapshot_locked(self):
        state = super()._snapshot_locked()
        state.update(source_commit=SOURCE_COMMIT, source_mixed_dwell_s=.08,
                     source_dwell_remaining_s=max(0.0,self._source_until-(self._clock_seen or 0.0)) if self._running else 0.0,
                     source_pwm_profile=[35,40,45], speed_profile_units='existing_serial_spd_setpoints',
                     white_policy_override='no_blind_forward')
        return state
