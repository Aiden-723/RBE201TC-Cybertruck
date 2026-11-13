"""Continuous four-digital feedback over snapshots and the ONE motor owner.

Algorithm reference, read only: Yahboom Tracking-module commit
 e5a9e261c549c89c5421e48ebe6df1b5244d7229, tracking.py while-loop rules.
https://github.com/YahboomTechnology/Tracking-module/blob/e5a9e261c549c89c5421e48ebe6df1b5244d7229/4.Raspberry%20Pi%20tutorials/tracking.py
Independent input/output adaptation: no vendor source expressions or GPIO code
are copied. Outer -> strong correction, inner -> one-side forward, center ->
forward, recomputed each tick. WHITE and BOTH outers mean position unknown:
never select a new direction from either. No PID, angle estimate or locked turn.

Round3 physical sweep establishes raw X1..X4 RIGHT to LEFT; physical left-to-right
is X4,X3,X2,X1, black=0. GPIO publication order and its owner remain untouched.
Only a fresh usable line successfully driven in THIS start authorizes bounded
ambiguous-position retention of the last actual nonzero pair (never invent one).
WHITE may retain only the existing unequal correction pair: .2s for an
inner-wheel correction, .4s for an already observed outer-probe pivot;
WHITE after straight/start stops immediately. Never select or strengthen a turn.
Any fresh usable line clears the gap; switching unknown patterns never does.
"""
from collections import deque
import copy
import hmac
import math
import secrets
import threading
import time


PINS = (15, 18, 16, 11)  # Raw GPIO publication order, NOT physical left-to-right.
RAW_CHANNEL_ORDER = ('X1', 'X2', 'X3', 'X4')
CENTER = (1, 0, 0, 1)
WHITE = (1, 1, 1, 1)
MAX_CORRECTION_SPEED = 50


def steering(values, speed=140):
    """Return current physical left-to-right digital rule; error is diagnostic."""
    if type(speed) is not int or not 50 <= speed <= 220:
        raise ValueError('invalid_speed')
    if (not isinstance(values, (list, tuple)) or len(values) != 4 or
            any(type(value) is not int or value not in (0, 1) for value in values)):
        raise ValueError('invalid_line_values')
    speed = min(speed, MAX_CORRECTION_SPEED)
    if values[0] == values[3] == 0:
        raise ValueError('ambiguous_line')
    if values[0] == 0:
        return -MAX_CORRECTION_SPEED, MAX_CORRECTION_SPEED, 'hard_left', -3.0
    if values[3] == 0:
        return MAX_CORRECTION_SPEED, -MAX_CORRECTION_SPEED, 'hard_right', 3.0
    if values[1] == values[2] == 0:
        return speed, speed, 'center', 0.0
    if values[1] == 0:
        return 0, speed, 'inner_left', -1.0
    if values[2] == 0:
        return speed, 0, 'inner_right', 1.0
    raise ValueError('line_lost')


def _position_gap_pattern(values):
    """Classify validated physical tuples; None is pre-sample diagnostics only."""
    if values is None:
        return None
    if values == WHITE:
        return 'white'
    if values[0] == values[3] == 0:
        return 'both_outer'
    return None


class BasicLineFollower:
    period = .02
    max_sensor_age = .4
    owner_timeout = .8
    control_policy = 'continuous_four_digital_v6'
    max_correction_speed = MAX_CORRECTION_SPEED
    line_loss_limit_s = 1.4  # Existing BOTH-outer ambiguity budget.
    white_correction_hold_limit_s = .2
    white_pivot_hold_limit_s = .4
    trace_limit = 40

    def __init__(self, motor, line_source, clock=time.monotonic, threaded=True, *,
                 channel_permutation=(0, 1, 2, 3)):
        if not callable(clock) or type(threaded) is not bool:
            raise ValueError('invalid_worker_configuration')
        if (not isinstance(channel_permutation, (list, tuple)) or len(channel_permutation) != 4 or
                any(type(index) is not int or not 0 <= index <= 3 for index in channel_permutation) or
                len(set(channel_permutation)) != 4):
            raise ValueError('invalid_channel_permutation')
        self._channel_permutation = tuple(channel_permutation)
        self.motor, self.line_source = motor, line_source
        self.clock, self.threaded = clock, threaded
        self._lock = threading.RLock()
        self._exit = threading.Event()
        self._thread = None  # Constructor does not start threads, arm, read or move.
        self._closed = self._running = self._heartbeat_seen = False
        self._owner = self._motor_session = None
        self._owner_seq = self._motor_seq = -1
        self._owner_time = self._clock_seen = None
        self._generation = 0
        self._state, self._reason = 'stopped', 'not_started'
        self._speed, self._left, self._right = 140, 0, 0
        self._values = self._raw_values = self._sample_mono = self._sample_count = None
        self._band = self._error = None
        self._stop_sent = False
        self._trace = deque(maxlen=self.trace_limit)
        self._last_failure = None
        self._last_visible_values = None
        self._reset_hold_locked()

    def _reset_hold_locked(self):
        self._hold_authorized = False
        self._line_gap_started = None
        self._line_gap_elapsed = 0.0
        self._line_gap_limit_s = self.line_loss_limit_s

    def _available(self):
        return (callable(self.line_source) and callable(getattr(self.motor, 'command', None))
                and callable(getattr(self.motor, 'drive_sides', None)))

    def _gap_diagnostic_locked(self):
        return {'line_gap_active': self._running and self._line_gap_started is not None,
                'line_gap_elapsed_s': self._line_gap_elapsed,
                'line_gap_limit_s': self._line_gap_limit_s,
                'white_correction_hold_limit_s': self.white_correction_hold_limit_s,
                'white_pivot_hold_limit_s': self.white_pivot_hold_limit_s,
                'hold_authorized': self._hold_authorized,
                'hold_pattern': _position_gap_pattern(self._values),
                'sample_monotonic': self._sample_mono, 'sample_count': self._sample_count,
                'last_visible_values': (list(self._last_visible_values)
                                        if self._last_visible_values is not None else None)}

    def _diagnostic_locked(self):
        return {'monotonic': self._clock_seen, 'state': self._state, 'reason': self._reason,
                'values': list(self._values) if self._values is not None else None,
                'raw_values': list(self._raw_values) if self._raw_values is not None else None,
                'left': self._left, 'right': self._right, **self._gap_diagnostic_locked()}

    def _record_locked(self, event):
        self._trace.append({'event': event, **self._diagnostic_locked()})

    def _snapshot_locked(self):
        # Never expose autonomy tokens, motor sessions or exception contents.
        return {'ready': self._available() and not self._closed,
                'running': self._running, 'state': self._state, 'reason': self._reason,
                'speed': self._speed, 'left': self._left, 'right': self._right,
                'last_values': list(self._values) if self._values is not None else None,
                'last_raw_values': list(self._raw_values) if self._raw_values is not None else None,
                'raw_channel_order': list(RAW_CHANNEL_ORDER), 'raw_physical_pins': list(PINS),
                'channel_permutation': list(self._channel_permutation),
                'band': self._band, 'error': self._error,
                'turn_strength': self._band.split('_')[0] if self._band else None,
                'heartbeat_received': self._heartbeat_seen,
                'sensor_max_age_s': self.max_sensor_age, 'owner_timeout_s': self.owner_timeout,
                'period_s': self.period, 'black_level': 0,
                'channel_order': [RAW_CHANNEL_ORDER[index] for index in self._channel_permutation],
                'control_policy': self.control_policy, 'direction_recovery_enabled': False,
                'max_correction_speed': self.max_correction_speed, 'line_loss_limit_s': self.line_loss_limit_s,
                **self._gap_diagnostic_locked(),
                'transition_trace': copy.deepcopy(list(self._trace)),
                'last_failure': copy.deepcopy(self._last_failure),
                'motion_feedback': 'unverified', 'station_detection': False}

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def _now_locked(self):
        try:
            now = self.clock()
            valid = type(now) in (int, float) and math.isfinite(now) and now >= 0
        except Exception:
            valid = False
        if not valid:
            raise ValueError('invalid_clock')
        if self._clock_seen is not None and now < self._clock_seen:
            raise ValueError('clock_regressed')
        self._clock_seen = now
        return now

    def _halt_locked(self, reason, *, fault=False):
        # Revoke before motor STOP; all motor calls use this same lock.
        self._generation += 1
        self._running = self._heartbeat_seen = False
        self._owner = self._motor_session = None
        self._owner_time = None
        self._owner_seq = self._motor_seq = -1
        self._left = self._right = 0
        if self._line_gap_started is not None and self._clock_seen is not None:
            self._line_gap_elapsed = max(0.0, self._clock_seen - self._line_gap_started)
        self._hold_authorized = False
        states = {'line_lost': 'lost_line', 'stale_line_sample': 'sensor_stale',
                  'owner_heartbeat_expired': 'heartbeat_expired'}
        self._state = 'closed' if self._closed else states.get(reason, 'fault') if fault else 'stopped'
        self._reason = reason
        if not self._stop_sent:
            try:
                result = self.motor.command({'action': 'stop'})
                if isinstance(result, dict) and result.get('fault'):
                    raise ValueError('motor_stop_failed')
                self._stop_sent = True
            except Exception:
                self._state = 'closed' if self._closed else 'fault'
                self._reason = 'motor_stop_failed'
                self._stop_sent = False  # A later explicit stop/close may retry.
                fault = True
        if fault:
            self._last_failure = self._diagnostic_locked()
        self._record_locked('failure' if fault else 'stop')
        return self._snapshot_locked()

    def start(self, speed=140):
        if type(speed) is not int or not 50 <= speed <= 220:
            raise ValueError('invalid_speed')
        with self._lock:
            if self._closed:
                raise ValueError('follower_closed')
            if self._running:
                raise ValueError('autonomy_already_running')
            if not self._available():
                raise ValueError('follower_unavailable')
            try:
                now = self._now_locked()
            except ValueError as exc:
                self._halt_locked(str(exc), fault=True)
                raise
            self._stop_sent = False
            try:
                armed = self.motor.command({'action': 'arm'})
                session = armed.get('session') if isinstance(armed, dict) else None
                if not isinstance(session, str) or not session or armed.get('fault'):
                    raise ValueError('motor_arm_failed')
            except Exception as exc:
                self._halt_locked('motor_arm_failed', fault=True)
                raise ValueError('motor_arm_failed') from exc
            self._generation += 1
            self._owner = secrets.token_urlsafe(32)
            self._motor_session = session
            self._owner_seq = self._motor_seq = -1
            self._owner_time, self._speed = now, min(speed, MAX_CORRECTION_SPEED)
            self._sample_mono = self._sample_count = None
            # Preserve last raw values, trace and last failure across explicit
            # stop/start. New session sample ordering starts independently.
            self._left = self._right = 0
            self._band = self._error = None
            self._reset_hold_locked()
            self._running, self._heartbeat_seen = True, False
            self._state, self._reason = 'waiting_heartbeat', 'awaiting_first_heartbeat'
            self._record_locked('start_armed')
            if self.threaded and self._thread is None:
                self._thread = threading.Thread(target=self._run, name='basic-line-follower', daemon=True)
                try:
                    self._thread.start()
                except RuntimeError as exc:
                    self._thread = None
                    self._halt_locked('worker_start_failed', fault=True)
                    raise ValueError('worker_start_failed') from exc
            return self._owner

    def heartbeat(self, session, seq):
        with self._lock:
            if (not self._running or not isinstance(session, str) or self._owner is None or
                    not hmac.compare_digest(session.encode('utf-8'), self._owner.encode('utf-8'))):
                raise ValueError('not_autonomy_owner')
            if type(seq) is not int or not 0 <= seq <= 2**53 - 1 or seq <= self._owner_seq:
                raise ValueError('stale_heartbeat')
            try:
                now = self._now_locked()
                if now - self._owner_time >= self.owner_timeout:
                    raise ValueError('owner_heartbeat_expired')
            except ValueError as exc:
                self._halt_locked(str(exc), fault=True)
                raise
            self._owner_seq, self._owner_time = seq, now
            self._heartbeat_seen = True
            if self._state == 'waiting_heartbeat':
                self._state, self._reason = 'waiting_sensor', 'heartbeat_received'
                self._record_locked('first_heartbeat')
            return self._snapshot_locked()

    def _sample(self, reading, now):
        # Validate raw data BEFORE continuous feedback or gap handling so even a
        # valid all-white/ambiguous pattern is retained for failure diagnostics.
        if not isinstance(reading, dict) or reading.get('status') != 'live':
            raise ValueError('line_not_live')
        values = reading.get('value')
        if (not isinstance(values, (list, tuple)) or len(values) != 4 or
                any(type(value) is not int or value not in (0, 1) for value in values)):
            raise ValueError('invalid_line_values')
        mono, age, count = reading.get('monotonic'), reading.get('age_seconds'), reading.get('successes')
        try:
            valid_clock = (type(mono) in (int, float) and type(age) in (int, float) and
                           math.isfinite(mono) and math.isfinite(age) and mono >= 0 and age >= 0)
        except OverflowError:
            valid_clock = False
        if not valid_clock:
            raise ValueError('invalid_line_clock')
        if mono > now:
            raise ValueError('future_line_sample')
        if age > self.max_sensor_age or now - mono > self.max_sensor_age:
            raise ValueError('stale_line_sample')
        if reading.get('physical_pins') not in (list(PINS), PINS):
            raise ValueError('line_pins_changed')
        if type(count) is not int or count < 1:
            raise ValueError('invalid_line_sequence')
        values = tuple(values)
        fresh = True
        if self._sample_mono is not None:
            if mono < self._sample_mono or count < self._sample_count:
                raise ValueError('replayed_line_sample')
            if mono == self._sample_mono or count == self._sample_count:
                if mono != self._sample_mono or count != self._sample_count or values != self._raw_values:
                    raise ValueError('inconsistent_line_replay')
                fresh = False  # Re-reading cache NEVER counts as another sample.
        return values, mono, count, fresh

    def _gap_deadline_locked(self, now):
        if self._line_gap_started is not None:
            self._line_gap_elapsed = max(0.0, now - self._line_gap_started)
            if self._line_gap_elapsed >= self._line_gap_limit_s:
                raise ValueError('line_gap_timeout')

    def _decide_locked(self, values, fresh, mono, now, hold_pattern):
        if hold_pattern is None:
            return steering(values, self._speed)  # Every usable frame overrides the last direction.
        if hold_pattern == 'white' and self._left == self._right:
            raise ValueError('line_lost')  # Never continue straight or reuse an old turn.
        missing = 'line_lost' if hold_pattern == 'white' else 'ambiguous_line'
        if not self._hold_authorized:
            raise ValueError(missing)  # No successful fresh usable pair in THIS start.
        if self._line_gap_started is None:
            if not fresh:
                raise ValueError(missing)  # A cache cannot create authority or a gap.
            self._line_gap_started = mono
        if hold_pattern == 'white':
            # WHITE cannot select or upgrade a turn. Shorten this continuous gap
            # once, so ambiguous/WHITE switching or cached frames cannot extend it.
            limit = (self.white_pivot_hold_limit_s if self._left * self._right < 0
                     else self.white_correction_hold_limit_s)
            self._line_gap_limit_s = min(self._line_gap_limit_s, limit)
        self._gap_deadline_locked(now)
        # Continue only the exact pair already submitted; fresh line overrides immediately.
        return self._left, self._right, 'line_gap_hold', None

    def tick(self):
        with self._lock:
            if not self._running:
                return self._snapshot_locked()
            try:
                now = self._now_locked()
                if now - self._owner_time >= self.owner_timeout:
                    return self._halt_locked('owner_heartbeat_expired', fault=True)
            except ValueError as exc:
                return self._halt_locked(str(exc), fault=True)
            if not self._heartbeat_seen:
                return self._snapshot_locked()
            generation = self._generation
        # STOP never waits for this callback. A late in-flight read is discarded.
        try:
            reading = self.line_source()
        except Exception:
            with self._lock:
                if self._running and generation == self._generation:
                    return self._halt_locked('line_source_failed', fault=True)
                return self._snapshot_locked()
        with self._lock:
            if not self._running or generation != self._generation:
                return self._snapshot_locked()
            try:
                now = self._now_locked()
                if now - self._owner_time >= self.owner_timeout:
                    return self._halt_locked('owner_heartbeat_expired', fault=True)
                raw_values, mono, count, fresh = self._sample(reading, now)
                # The ONLY raw -> physical transformation; replay remains raw.
                values = tuple(raw_values[index] for index in self._channel_permutation)
                previous = (self._state, self._band, self._values, self._line_gap_started is not None)
                self._raw_values = raw_values
                self._values, self._sample_mono, self._sample_count = values, mono, count
                hold_pattern = _position_gap_pattern(values)
                fresh_usable = fresh and hold_pattern is None
                left, right, band, error = self._decide_locked(values, fresh, mono, now, hold_pattern)
                if fresh_usable:
                    # The same classification controls clearing, state and last
                    # visible evidence. Fresh both-outer is NOT a usable line.
                    self._line_gap_started, self._line_gap_elapsed = None, 0.0
                    self._line_gap_limit_s = self.line_loss_limit_s
                self._motor_seq += 1
                # Motor owns sign-change STOP packets; never revoke then reuse.
                driven = self.motor.drive_sides(self._motor_session, self._motor_seq, left, right)
                if isinstance(driven, dict) and (driven.get('fault') or driven.get('armed') is False):
                    raise ValueError('motor_drive_failed')
                sent_at = self._now_locked()
                if sent_at - self._owner_time >= self.owner_timeout:
                    return self._halt_locked('owner_heartbeat_expired', fault=True)
                if sent_at - mono > self.max_sensor_age:
                    return self._halt_locked('stale_line_sample', fault=True)
                self._gap_deadline_locked(sent_at)
            except ValueError as exc:
                reason = str(exc)
                known = {'line_not_live', 'invalid_line_values', 'line_lost', 'ambiguous_line',
                         'invalid_line_clock', 'future_line_sample', 'stale_line_sample', 'line_pins_changed',
                         'invalid_line_sequence', 'replayed_line_sample', 'inconsistent_line_replay',
                         'invalid_clock', 'clock_regressed', 'line_gap_timeout'}
                return self._halt_locked(reason if reason in known else 'motor_drive_failed', fault=True)
            except Exception:
                return self._halt_locked('motor_drive_failed', fault=True)
            self._left, self._right, self._band, self._error = left, right, band, error
            self._state = ('line_gap' if hold_pattern is not None else
                           'left' if error < 0 else 'right' if error > 0 else 'straight')
            self._reason = band
            if fresh_usable:
                self._last_visible_values = values
                if left != 0 or right != 0:
                    self._hold_authorized = True  # Only after fresh usable actual motor submission.
            decision = (self._state, self._band, self._values, self._line_gap_started is not None)
            if decision != previous:
                self._record_locked('decision')
            return self._snapshot_locked()

    def stop(self, reason='operator_stop'):
        if not isinstance(reason, str) or not reason or len(reason) > 100:
            raise ValueError('invalid_stop_reason')
        with self._lock:
            return self._halt_locked(reason)

    def _run(self):
        delay = self.period
        while not self._exit.wait(delay):
            started = time.monotonic()
            try:
                self.tick()
            except Exception:
                with self._lock:
                    self._halt_locked('worker_failed', fault=True)
            # Account for snapshot + serial time. Drop overdue periods instead
            # of queuing a burst of stale commands; STOP/close interrupt wait.
            delay = max(.001, self.period - (time.monotonic() - started))

    def close(self):
        with self._lock:
            self._closed = True
            self._exit.set()
            self._halt_locked('closed')
            thread = self._thread
        # No join with follower lock held: the worker may be finishing tick().
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
