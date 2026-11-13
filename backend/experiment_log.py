"""Persistent station-event logging, independent of UART/GPIO/camera ownership.

The first eight CSV columns retain the course event schema. Every new event is
committed as pending before any HTTP request, then updated in place at most once.
Only the existing sampler's NEXT published outcome is eligible; last_good/history
are never substituted. This is stationary, manually located acquisition, NOT an
autonomous marker-triggered DHT request or proof that the chassis has stopped.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid


COURSE_FIELDS = ('RunID', 'Detection point', 'Trigger time', 'Result time',
                 'Humidity_%RH', 'Temperature_°C', 'Status', 'Explanation')
FIELDS = COURSE_FIELDS + ('RunPurpose', 'GroupID', 'RoomID', 'StationID', 'X_m',
                         'Y_m', 'SensorHeight_m', 'Trigger_source', 'Sampled_at',
                         'Sampler_monotonic', 'Sampler_successes', 'Sampler_failures',
                         'Delay_seconds', 'Motion_evidence', 'Notes')
FINAL_STATES = {'success', 'sensor_failure', 'request_failure', 'timeout', 'cancelled'}


def timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timestamp must include a timezone')
    return value.isoformat(timespec='milliseconds')


def number(value, low=-math.inf, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError('A finite numeric value is required')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Numeric value is outside the permitted range')
    return value


@dataclass(frozen=True)
class Station:
    station_id: str
    x_m: float = None
    y_m: float = None
    height_m: float = None
    notes: str = ''

    def validate(self, formal=False):
        if not isinstance(self.station_id, str) or not self.station_id.strip():
            raise ValueError('StationID is required')
        values = (self.x_m, self.y_m, self.height_m)
        if formal and any(v is None for v in values):
            raise ValueError('Formal station capture requires measured X/Y/height')
        for value in values:
            if value is not None:
                number(value)
        if self.height_m is not None:
            number(self.height_m, low=0)


class LogPersistenceError(RuntimeError):
    pass


class RunLog:
    def __init__(self, output_dir, *, purpose, group_id='', room_id='', metadata=None,
                 now=None, run_id=None, transport='http_get', motion_policy='zero_command',
                 association_mode='manual_stationary'):
        if transport not in ('http_get', 'in_process_snapshot'):
            raise ValueError('Unknown observation transport')
        if purpose not in ('bench_diagnostic', 'room_stationary'):
            raise ValueError('Unknown run purpose')
        if motion_policy not in ('zero_command', 'sensor_only'):
            raise ValueError('Unknown motion observation policy')
        if motion_policy == 'sensor_only' and purpose != 'bench_diagnostic':
            raise ValueError('Sensor-only recording is restricted to bench diagnostics')
        if association_mode not in ('manual_stationary', 'automatic_stationary'):
            raise ValueError('Unknown station association mode')
        self.association_mode = association_mode
        self.motion_policy = motion_policy
        if purpose == 'room_stationary' and (not group_id.strip() or not room_id.strip()):
            raise ValueError('Formal capture requires GroupID and RoomID')
        self.now = now or (lambda: datetime.now().astimezone())
        started = self.now()
        stamp = timestamp(started)
        self.run_id = run_id or (started.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                                 + '_' + uuid.uuid4().hex[:8])
        if not self.run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in self.run_id):
            raise ValueError('Unsafe RunID')
        self.directory = Path(output_dir).expanduser().resolve() / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / ('temperature_points_' + self.run_id + '.csv')
        self.manifest_path = self.directory / 'run_manifest.json'
        self.purpose, self.group_id, self.room_id = purpose, group_id, room_id
        self.records = {}
        self.failed = None
        self.closed = False
        self.manifest = {
            'schema_version': '1.0', 'RunID': self.run_id, 'purpose': purpose,
            'GroupID': group_id, 'RoomID': room_id, 'started_at': stamp,
            'association_mode': association_mode,
            'acquisition': ('automatic stationary task; observe next outcome; external stationarity evidence required'
                            if association_mode == 'automatic_stationary' else
                            'sensor-only bench; observe next outcome; motion is unknown'
                            if motion_policy == 'sensor_only' else
                            'manual stationary; observe next outcome from existing sampler over HTTP GET'
                            if transport == 'http_get' else
                            'manual stationary; observe next outcome from existing in-process sampler snapshot'),
            'acquisition_transport': transport,
            'motion_policy': motion_policy,
            'sampling_limitations': 'Trigger is not a native READ request or physical sample instant; keep vehicle and sensor stationary until result. Motion feedback is unverified.',
            'metadata': metadata or {}, 'closed_at': None,
        }
        self._atomic(self.manifest_path, json.dumps(self.manifest, ensure_ascii=False,
                                                  allow_nan=False, indent=2).encode())
        self._commit({})

    def _atomic(self, destination, data):
        if self.failed:
            raise LogPersistenceError(self.failed)
        if destination.parent.resolve() != self.directory or destination not in (self.path, self.manifest_path):
            raise ValueError('Output must remain in this reserved run directory')
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.event-', dir=self.directory)
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            self.failed = 'Cannot persist run: ' + str(exc)
            raise LogPersistenceError(self.failed) from exc
        finally:
            if temporary is not None:
                candidate = Path(temporary)
                if candidate.parent.resolve() == self.directory and candidate.exists():
                    candidate.unlink()

    def _commit(self, records):
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records.values())
        self._atomic(self.path, output.getvalue().encode('utf-8-sig'))
        self.records = records

    def begin(self, station):
        if self.closed:
            raise ValueError('Run is closed')
        station.validate(self.purpose == 'room_stationary')
        point = str(len(self.records) + 1)
        row = dict.fromkeys(FIELDS, '')
        row.update({'RunID': self.run_id, 'Detection point': point,
                    'Trigger time': timestamp(self.now()), 'Status': 'pending',
                    'RunPurpose': self.purpose, 'GroupID': self.group_id,
                    'RoomID': self.room_id, 'StationID': station.station_id,
                    'X_m': '' if station.x_m is None else station.x_m,
                    'Y_m': '' if station.y_m is None else station.y_m,
                    'SensorHeight_m': '' if station.height_m is None else station.height_m,
                    'Trigger_source': ('automatic_station/passive_next_result' if self.association_mode == 'automatic_stationary'
                                       else 'manual_station/passive_next_result'),
                    'Motion_evidence': ('motor_interface_disabled; actual_motion_unknown; bench_only'
                                        if self.motion_policy == 'sensor_only' else
                                        'zero_command_required; actual_stationary_unverified'),
                    'Notes': station.notes})
        updated = dict(self.records)
        updated[point] = row
        self._commit(updated)
        return point

    def finish(self, point, status, *, explanation='', value=None, source=None):
        point = str(point)
        if status not in FINAL_STATES:
            raise ValueError('Invalid terminal status')
        if point not in self.records:
            raise ValueError('Unknown event')
        if self.records[point]['Status'] != 'pending':
            return False  # A late/duplicate reply cannot revise a completed event.
        row = dict(self.records[point])
        ended = self.now()
        started = datetime.fromisoformat(row['Trigger time'])
        delay = (ended - started).total_seconds()
        if delay < 0:
            status = 'request_failure'
            explanation = 'Recorder wall clock moved backwards; sample excluded. ' + explanation
        row.update({'Status': status, 'Result time': timestamp(ended),
                    'Explanation': explanation,
                    'Delay_seconds': '' if delay < 0 else round(delay, 3)})
        if status == 'success':
            if not isinstance(value, dict):
                raise ValueError('Success requires a current valid sample')
            row['Temperature_°C'] = number(value.get('temperature_c'), -20, 80)
            row['Humidity_%RH'] = number(value.get('humidity_pct'), 0, 100)
        elif not explanation:
            raise ValueError('Failure/cancellation requires an explanation')
        if source:
            for target, key in [('Sampled_at', 'sampled_at'), ('Sampler_monotonic', 'monotonic'),
                                ('Sampler_successes', 'successes'), ('Sampler_failures', 'failures')]:
                row[target] = source.get(key, '')
        updated = dict(self.records)
        updated[point] = row
        self._commit(updated)
        return True

    def close(self):
        if self.closed:
            return
        for point, row in list(self.records.items()):
            if row['Status'] == 'pending':
                self.finish(point, 'cancelled', explanation='Recorder closed before a result was available')
        manifest = dict(self.manifest)
        manifest.update(closed_at=timestamp(self.now()), row_count=len(self.records),
                        raw_csv_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                        status_counts=dict(Counter(r['Status'] for r in self.records.values())))
        self._atomic(self.manifest_path, json.dumps(manifest, ensure_ascii=False,
                                                  allow_nan=False, indent=2).encode())
        self.manifest = manifest
        self.closed = True


def _health(snapshot, now, motion_policy='zero_command'):
    host = snapshot['host']
    device_time = datetime.fromisoformat(host['device_time'])
    if not host.get('is_rdk') or not host.get('clock_valid') or device_time.tzinfo is None:
        raise ValueError('RDK identity/clock is not valid')
    if abs((device_time - now).total_seconds()) > 5:
        raise ValueError('Device and recorder clocks differ by more than 5 seconds')
    control = snapshot.get('control', {})
    if motion_policy == 'sensor_only':
        if (control.get('enabled') is not False or control.get('state') != 'disabled'
                or control.get('armed') is not False or control.get('outputs') is not None):
            raise ValueError('Sensor-only recording requires an explicitly disabled motor interface')
    else:
        if control.get('enabled') is not True or control.get('fault') or control.get('state') not in ('stopped', 'armed'):
            raise ValueError('Stopped motor-command state cannot be verified')
        outputs = control.get('outputs')
        if not isinstance(outputs, list) or len(outputs) != 4 or any(type(x) is not int or x != 0 for x in outputs):
            raise ValueError('Nonzero or invalid motor commands observed; stationary capture cancelled')
    uptime = number(snapshot['system']['uptime_seconds']['value'], low=0)
    return uptime


def capture_station(log, station, fetch_status, *, timeout=10, clock=time.monotonic,
                    wait=time.sleep, complete=None):
    """Record ONE next published outcome using GET only; never operate hardware.

    fetch_status(timeout_seconds) returns the /api/v1/status DATA object.
    A failure is retained as its own event, not silently retried into success.
    """
    number(timeout, low=0.1, high=120)
    complete = complete or log.finish
    point = log.begin(station)
    deadline = clock() + timeout
    source = None
    try:
        initial = fetch_status(max(0.05, min(2, deadline - clock())))
        baseline_uptime = _health(initial, log.now(), log.motion_policy)
        baseline = initial['sensors']['dht11']
        marker = max(baseline_uptime, number(baseline.get('monotonic', 0), low=0))
        counts = [number(baseline.get(key, 0), low=0) for key in ('successes', 'failures')]
        while clock() < deadline:
            wait(min(0.2, max(0, deadline - clock())))
            remaining = deadline - clock()
            if remaining <= 0:
                break
            snapshot = fetch_status(max(0.05, min(2, remaining)))
            if clock() >= deadline:
                break  # A late HTTP response cannot turn an expired event into success.
            uptime = _health(snapshot, log.now(), log.motion_policy)
            if uptime < baseline_uptime:
                raise ValueError('Board rebooted during capture')
            current = snapshot['sensors']['dht11']
            if any(number(current.get(key, 0), low=0) < old for key, old in zip(('successes', 'failures'), counts)):
                raise ValueError('DHT worker restarted during capture')
            mono = number(current.get('monotonic', 0), low=0)
            if mono <= marker or current.get('status') not in ('live', 'error'):
                continue
            source = current
            sampled_at = datetime.fromisoformat(current['sampled_at'])
            if sampled_at.tzinfo is None:
                raise ValueError('Source result timestamp has no timezone')
            if abs((sampled_at - log.now()).total_seconds()) > 9:
                raise ValueError('Source result time is stale or inconsistent')
            if current['status'] == 'error':
                complete(point, 'sensor_failure', explanation=str(current.get('error') or 'DHT sampler failure'), source=current)
            else:
                try:
                    value = current['value']
                    number(value['temperature_c'], -20, 80)
                    number(value['humidity_pct'], 0, 100)
                    number(current['age_seconds'], 0, 9)
                except (KeyError, TypeError, ValueError):
                    complete(point, 'sensor_failure', explanation='New DHT sample is malformed, stale, or outside range', source=current)
                else:
                    complete(point, 'success', value=value, source=current,
                               explanation=('Next published sampler outcome; sensor-only bench, motion unknown'
                                            if log.motion_policy == 'sensor_only' else
                                            'Next published sampler outcome; manual stationary association, not hardware-triggered'))
            return log.records[point]
        complete(point, 'timeout', explanation='No new DHT outcome within %.1f seconds' % timeout)
    except (KeyboardInterrupt, SystemExit):
        complete(point, 'cancelled', explanation='Recorder interrupted before completion', source=source)
        raise
    except LogPersistenceError:
        raise  # Do not pretend a disk failure is a successfully recorded sensor failure.
    except (OSError, ValueError, TypeError, KeyError) as exc:
        complete(point, 'request_failure', explanation=str(exc)[:500], source=source)
    return log.records[point]
