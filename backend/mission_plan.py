"""Offline-only mission reducer. NO hardware, network, clock, disk, or executor.

Intents are inert data, NOT motor commands or persisted sensor records. There is
no production route/import wiring. Real calibration, position, marker recognition,
stationarity and timing evidence must be supplied and independently commissioned;
validating a declaration cannot establish that the physical evidence is genuine.
"""
from dataclasses import dataclass
import math
import re

from line_calibration import CHANNELS, PINS, black_in_physical_order, build_calibration


class MissionError(ValueError):
    """An invalid plan/start or a call that cannot apply to the current state."""


@dataclass(frozen=True)
class CalibrationEvidence:
    white_reference: dict
    black_reference: dict
    physical_left_to_right_order: tuple
    physical_order_evidence_id: str


@dataclass(frozen=True)
class StationPlan:
    station_id: str
    marker_id: str
    x_m: float
    y_m: float
    height_m: float


@dataclass(frozen=True)
class Timing:
    max_observation_age: float
    max_update_gap: float
    stop_timeout: float
    settle_seconds: float
    adapt_seconds: float
    capture_timeout: float
    mission_timeout: float


@dataclass(frozen=True)
class MissionPlan:
    mission_id: str
    group_id: str
    room_id: str
    stations: tuple
    timing: Timing
    calibration: CalibrationEvidence
    route_evidence_id: str
    marker_evidence_id: str
    stationary_evidence_id: str
    timing_evidence_id: str


@dataclass(frozen=True)
class Observation:
    # All times are from ONE monotonic domain; callers must not mix wall clocks.
    observed_at: float
    sequence: int
    source_id: str
    line_values: tuple
    physical_pins: tuple
    line_trackable: bool
    zero_command: bool
    stationary_confirmed: bool
    control_healthy: bool
    emergency_stop: bool
    marker_id: str = None


@dataclass(frozen=True)
class Intent:
    kind: str
    mission_id: str = None
    station_id: str = None
    request_id: str = None
    event_id: str = None
    reason: str = None


TERMINAL = frozenset(('completed', 'failed', 'cancelled', 'emergency_stopped'))
RESULTS = frozenset(('success', 'sensor_failure', 'request_failure', 'timeout', 'cancelled'))
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')


def _identifier(value, name):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise MissionError('invalid_' + name)
    return value


def _evidence(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000 or '\x00' in value:
        raise MissionError('missing_' + name)


def _number(value, name, *, positive=False):
    try:
        valid = type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise MissionError('invalid_' + name)
    if positive and value <= 0:
        raise MissionError('invalid_' + name)
    return value


def validate_plan(plan):
    """Validate explicit declarations; never infer defaults or fabricate evidence."""
    if not isinstance(plan, MissionPlan):
        raise MissionError('explicit_plan_required')
    for name in ('mission_id', 'group_id', 'room_id'):
        _identifier(getattr(plan, name), name)
    for name in ('route_evidence_id', 'marker_evidence_id', 'stationary_evidence_id', 'timing_evidence_id'):
        _evidence(getattr(plan, name), name)
    if not isinstance(plan.timing, Timing):
        raise MissionError('explicit_timing_required')
    for name in Timing.__dataclass_fields__:
        _number(getattr(plan.timing, name), name, positive=True)
    if not isinstance(plan.stations, tuple) or not 1 <= len(plan.stations) <= 100:
        raise MissionError('explicit_stations_required')
    stations, markers = set(), set()
    for station in plan.stations:
        if not isinstance(station, StationPlan):
            raise MissionError('invalid_station')
        _identifier(station.station_id, 'station_id')
        _identifier(station.marker_id, 'marker_id')
        if station.station_id in stations or station.marker_id in markers:
            raise MissionError('duplicate_station_or_marker')
        stations.add(station.station_id); markers.add(station.marker_id)
        for name in ('x_m', 'y_m', 'height_m'):
            _number(getattr(station, name), name)
        if station.height_m < 0:
            raise MissionError('invalid_height_m')
    evidence = plan.calibration
    if not isinstance(evidence, CalibrationEvidence):
        raise MissionError('black_white_and_physical_order_required')
    _evidence(evidence.physical_order_evidence_id, 'physical_order_evidence_id')
    order = evidence.physical_left_to_right_order
    if (not isinstance(order, tuple) or len(order) != 4 or
            any(not isinstance(channel, str) for channel in order) or set(order) != set(CHANNELS)):
        raise MissionError('physical_order_required')
    try:
        calibration = build_calibration(evidence.white_reference, evidence.black_reference, order)
        black_in_physical_order(calibration['white_levels'], calibration)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise MissionError('invalid_calibration: ' + str(exc)) from exc
    source = evidence.white_reference['host'].get('hostname')
    _evidence(source, 'calibration_source_id')
    return calibration, source


class MissionPlanner:
    """Single-owner reducer. Caller serializes calls; no threads or callbacks run.

    A fresh Observation is required for every active transition, including recorder
    acknowledgements. No restart/resume exists. Any failed/cancelled/estopped run
    requires separate human review and a NEW planner/mission identifier.
    """
    def __init__(self, plan=None):
        self.plan = plan
        self.state, self.reason = 'idle', None
        self.index = 0
        self._calibration = self._source = None
        self._started = self._updated = self._sampled = self._sequence = None
        self._phase_started = self._capture_started = None
        self._request_id = self._event_id = None
        self._outstanding = False
        self._marker_armed = True
        self._last_marker = None
        self._event_ids = set()
        self._outcomes = []

    def snapshot(self):
        return {'state': self.state, 'reason': self.reason, 'station_index': self.index,
                'station_id': self._station().station_id if self._calibration else None,
                'request_id': self._request_id, 'event_id': self._event_id,
                'outcomes': [dict(outcome) for outcome in self._outcomes],
                'integrated': False, 'executes_hardware': False, 'writes_records': False}

    def _station(self):
        return self.plan.stations[min(self.index, len(self.plan.stations) - 1)]

    def _intent(self, kind, reason=None):
        return Intent(kind, self.plan.mission_id if isinstance(self.plan, MissionPlan) else None,
                      self._station().station_id if self._calibration else None,
                      self._request_id, self._event_id, reason)

    def _end(self, state, reason):
        if self.state in TERMINAL:
            return (self._intent('hold_stop', self.reason),)
        self.state, self.reason = state, reason
        intents = [self._intent('hold_stop', reason)]
        if self._outstanding:
            intents.append(self._intent('cancel_capture', reason))
        self._outstanding = False
        return tuple(intents)

    def cancel(self):
        return self._end('cancelled', 'operator_cancelled')

    def emergency_stop(self):
        return self._end('emergency_stopped', 'emergency_stop')

    def _consume(self, observation, now):
        _number(now, 'now')
        if now < 0 or not isinstance(observation, Observation):
            raise MissionError('invalid_observation')
        _number(observation.observed_at, 'observed_at')
        if observation.observed_at < 0 or not 0 <= now - observation.observed_at < self.plan.timing.max_observation_age:
            raise MissionError('stale_or_future_observation')
        if type(observation.sequence) is not int or observation.sequence < 0:
            raise MissionError('invalid_sequence')
        if self._updated is not None:
            if now < self._updated:
                raise MissionError('monotonic_clock_regressed')
            if now - self._updated >= self.plan.timing.max_update_gap:
                raise MissionError('observation_gap')
            if observation.sequence <= self._sequence or observation.observed_at <= self._sampled:
                raise MissionError('replayed_or_reordered_observation')
        if observation.source_id != self._source or observation.physical_pins != tuple(PINS):
            raise MissionError('observation_source_or_pins_changed')
        for name in ('line_trackable', 'zero_command', 'stationary_confirmed', 'control_healthy', 'emergency_stop'):
            if type(getattr(observation, name)) is not bool:
                raise MissionError('invalid_' + name)
        if observation.marker_id is not None:
            _identifier(observation.marker_id, 'observed_marker_id')
        try:
            black_in_physical_order(observation.line_values, self._calibration)
        except (ValueError, TypeError, KeyError) as exc:
            raise MissionError('invalid_line_observation') from exc
        self._updated, self._sampled, self._sequence = now, observation.observed_at, observation.sequence

    def _guard(self, observation, now):
        if self.state in TERMINAL:
            return (self._intent('hold_stop', self.reason),)
        if self.state == 'idle':
            raise MissionError('mission_not_started')
        try:
            self._consume(observation, now)
        except MissionError as exc:
            return self._end('failed', str(exc))
        if observation.emergency_stop:
            return self._end('emergency_stopped', 'emergency_stop')
        if not observation.control_healthy:
            return self._end('failed', 'control_not_healthy')
        if now - self._started >= self.plan.timing.mission_timeout:
            return self._end('failed', 'mission_timeout')
        if self.state != 'following' and observation.marker_id not in (None, self._station().marker_id):
            return self._end('failed', 'station_marker_changed')
        if self.state in ('settling', 'adapting', 'awaiting_pending', 'awaiting_result'):
            if not observation.zero_command or not observation.stationary_confirmed:
                return self._end('failed', 'stationary_hold_lost')
        if self._outstanding and now - self._capture_started >= self.plan.timing.capture_timeout:
            return self._end('failed', 'capture_timeout')
        return None

    def start(self, observation, *, now):
        if self.state != 'idle':
            raise MissionError('mission_cannot_restart')
        self._calibration, self._source = validate_plan(self.plan)
        self._consume(observation, now)
        if observation.emergency_stop:
            return self._end('emergency_stopped', 'emergency_stop')
        if not observation.control_healthy or not observation.line_trackable:
            raise MissionError('healthy_trackable_start_required')
        if observation.marker_id is not None:
            raise MissionError('clear_start_marker_required')
        self._started, self.state = now, 'following'
        return (self._intent('follow_line'),)

    def advance(self, observation, *, now):
        guarded = self._guard(observation, now)
        if guarded is not None:
            return guarded
        if self.state == 'following':
            marker = observation.marker_id
            if not self._marker_armed:
                if marker is None:
                    self._marker_armed = True
                elif marker == self._last_marker:
                    if not observation.line_trackable:
                        return self._end('failed', 'line_not_trackable')
                    return (self._intent('follow_line', 'leave_completed_marker'),)
                else:
                    return self._end('failed', 'marker_not_cleared')
            if marker is not None:
                if marker != self._station().marker_id:
                    return self._end('failed', 'unexpected_marker')
                self.state, self._phase_started = 'stopping', now
                return (self._intent('hold_stop', 'station_marker'),)
            if not observation.line_trackable:
                return self._end('failed', 'line_not_trackable')
            return (self._intent('follow_line'),)
        if self.state == 'stopping':
            if now - self._phase_started >= self.plan.timing.stop_timeout:
                return self._end('failed', 'stop_timeout')
            if observation.zero_command and observation.stationary_confirmed:
                self.state, self._phase_started = 'settling', now
        elif self.state == 'settling':
            if now - self._phase_started >= self.plan.timing.settle_seconds:
                self.state, self._phase_started = 'adapting', now
        elif self.state == 'adapting':
            if now - self._phase_started >= self.plan.timing.adapt_seconds:
                self._request_id = self.plan.mission_id + ':' + str(self.index + 1)
                self._event_id = None
                self.state, self._capture_started, self._outstanding = 'awaiting_pending', now, True
                return (self._intent('hold_stop'), self._intent('request_capture'))
        return (self._intent('hold_stop'),)

    def confirm_pending(self, request_id, event_id, observation, *, now, durable=False):
        """Future recorder adapter acknowledgement AFTER its pending row is durable."""
        if request_id != self._request_id or not self._outstanding:
            return ()  # Unrelated/late callbacks never refresh liveness or resume.
        guarded = self._guard(observation, now)
        if guarded is not None:
            return guarded
        try:
            _identifier(event_id, 'event_id')
        except MissionError as exc:
            return self._end('failed', str(exc))
        if self.state == 'awaiting_result' and event_id == self._event_id and durable is True:
            return (self._intent('hold_stop'),)  # Idempotent acknowledgement.
        if self.state != 'awaiting_pending' or durable is not True or event_id in self._event_ids:
            return self._end('failed', 'invalid_pending_acknowledgement')
        self._event_id = event_id
        self._event_ids.add(event_id)
        self.state = 'awaiting_result'
        return (self._intent('hold_stop'),)

    def accept_result(self, request_id, event_id, station_id, status, observation, *, now, durable=False):
        """Accept only a matching, unique, durable terminal from the real recorder.

        Sensor values are deliberately not accepted; this module cannot create a
        CSV success, fill missing values, use last_good, or overwrite a late row.
        """
        if request_id != self._request_id or not self._outstanding:
            return ()
        guarded = self._guard(observation, now)
        if guarded is not None:
            return guarded
        if self.state != 'awaiting_result':
            return self._end('failed', 'pending_not_confirmed')
        if event_id != self._event_id or station_id != self._station().station_id:
            return self._end('failed', 'result_correlation_mismatch')
        if not isinstance(status, str) or status not in RESULTS or durable is not True:
            return self._end('failed', 'invalid_or_nondurable_terminal')
        self._outcomes.append({'station_id': station_id, 'request_id': request_id,
                               'event_id': event_id, 'status': status})
        self._outstanding = False
        if status != 'success':
            return self._end('failed', 'capture_' + status)
        if self.index + 1 == len(self.plan.stations):
            return self._end('completed', 'all_planned_stations_completed')
        if not observation.line_trackable:
            return self._end('failed', 'line_not_trackable')
        self._last_marker = self._station().marker_id
        self.index += 1
        self._marker_armed = False
        self.state = 'following'
        return (self._intent('follow_line', 'durable_station_success'),)
