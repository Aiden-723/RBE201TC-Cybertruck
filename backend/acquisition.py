"""Station logs without hardware ownership or synchronous HTTP disk work.

Startup reads a bounded history cache. All mutations and fsync run on one worker;
HTTP status and export use last-committed memory snapshots, never on-demand disk.
"""
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import threading

from experiment_log import FIELDS, RunLog, Station, capture_station, LogPersistenceError

RUN_ID = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
PURPOSES = ('bench_diagnostic', 'room_stationary')
MAX_EVENTS = 500
MAX_FILE = 8 * 1024 * 1024
MAX_HISTORY_BYTES = 16 * 1024 * 1024


class AcquisitionError(ValueError):
    pass


class CaptureCancelled(KeyboardInterrupt):
    pass


def text(value, name, maximum=1000, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise AcquisitionError('invalid_' + name)
    value = value.strip()
    if required and not value:
        raise AcquisitionError('missing_' + name)
    return value


def csv_bytes(records):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode('utf-8-sig')


class AcquisitionManager:
    def __init__(self, root, fetch_status, *, timeout=10, now=None, sensor_only=False):
        self.root = Path(root).expanduser().resolve()
        self.fetch_status, self.timeout, self.now = fetch_status, timeout, now
        self.sensor_only = sensor_only
        self.lock = threading.RLock()
        self.log, self.stations, self.worker = None, {}, None
        self.busy = self.closing = self.stopping = self.committing = False
        self.operation = None
        self.cancel_event = threading.Event()
        self.last_error = None
        self.history = {}
        self._discover()  # Before HTTP starts; never repeated by a request.

    def _load_disk(self, directory, purpose):
        parent = self.root / purpose
        if (parent.is_symlink() or directory.is_symlink() or directory.resolve().parent != parent
                or not RUN_ID.fullmatch(directory.name)):
            raise AcquisitionError('invalid_output_directory')
        manifest_path = directory / 'run_manifest.json'
        csv_path = directory / ('temperature_points_' + directory.name + '.csv')
        for path in (manifest_path, csv_path):
            if path.is_symlink() or path.resolve().parent != directory or path.stat().st_size > MAX_FILE:
                raise AcquisitionError('invalid_history_file')
        from datetime import datetime
        import math

        def invalid_constant(value):
            raise AcquisitionError('invalid_json_constant')

        def check_time(value):
            if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
                raise AcquisitionError('invalid_history_time')

        def numeric(value, low=-math.inf, high=math.inf):
            if not isinstance(value, str) or not value.strip():
                raise AcquisitionError('missing_history_number')
            result = float(value)
            if not math.isfinite(result) or not low <= result <= high:
                raise AcquisitionError('invalid_history_number')
            return result

        manifest_raw, raw = manifest_path.read_bytes(), csv_path.read_bytes()
        manifest = json.loads(manifest_raw, parse_constant=invalid_constant)
        if not isinstance(manifest, dict) or manifest.get('RunID') != directory.name or manifest.get('purpose') != purpose:
            raise AcquisitionError('invalid_run_manifest')
        if any(not isinstance(manifest.get(key, ''), str) for key in ('GroupID', 'RoomID')):
            raise AcquisitionError('invalid_run_manifest')
        check_time(manifest.get('started_at'))
        if manifest.get('closed_at') is not None:
            check_time(manifest['closed_at'])
        try:
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
            if tuple(reader.fieldnames or []) != FIELDS:
                raise AcquisitionError('invalid_history_schema')
            rows = list(reader)
        except csv.Error as exc:
            raise AcquisitionError('invalid_history_csv') from exc
        points = set()
        if len(rows) > MAX_EVENTS:
            raise AcquisitionError('invalid_history_rows')
        for row in rows:
            if set(row) != set(FIELDS) or any(not isinstance(value, str) for value in row.values()):
                raise AcquisitionError('invalid_history_rows')
            point = row['Detection point']
            if not re.fullmatch(r'[1-9][0-9]*', point) or point in points:
                raise AcquisitionError('invalid_history_event')
            points.add(point)
            if (row['Status'] not in ('pending', 'success', 'sensor_failure', 'request_failure', 'timeout', 'cancelled') or
                    row['RunID'] != directory.name or row['RunPurpose'] != purpose or
                    row['GroupID'] != manifest.get('GroupID', '') or row['RoomID'] != manifest.get('RoomID', '')):
                raise AcquisitionError('invalid_history_rows')
            check_time(row['Trigger time'])
            if row['Status'] != 'pending':
                check_time(row['Result time'])
            if row['Status'] == 'success':
                numeric(row['Temperature_°C'], -20, 80)
                numeric(row['Humidity_%RH'], 0, 100)
            elif row['Temperature_°C'] != '' or row['Humidity_%RH'] != '':
                raise AcquisitionError('failure_values_must_be_blank')
            if purpose == 'room_stationary':
                if not manifest.get('GroupID') or not manifest.get('RoomID') or not row['StationID'].strip():
                    raise AcquisitionError('formal_identifiers_required')
                numeric(row['X_m']); numeric(row['Y_m']); numeric(row['SensorHeight_m'], 0)
        if manifest.get('raw_csv_sha256') and hashlib.sha256(raw).hexdigest() != manifest['raw_csv_sha256']:
            raise AcquisitionError('history_checksum_mismatch')
        return {'manifest': manifest, 'rows': rows, 'csv': raw, 'manifest_bytes': manifest_raw}

    def _discover(self):
        budget = MAX_HISTORY_BYTES
        for purpose in PURPOSES:
            parent = self.root / purpose
            if not parent.is_dir() or parent.is_symlink():
                continue
            for directory in sorted(parent.iterdir(), reverse=True)[:100]:
                if len(self.history) >= 100:
                    break
                try:
                    entry = self._load_disk(directory, purpose)
                    size = len(entry['csv']) + len(entry['manifest_bytes'])
                    if size > budget:
                        continue
                    key = directory.name
                    if key in self.history:
                        self.history.pop(key)  # Ambiguous duplicate IDs are not served.
                        continue
                    self.history[key] = entry
                    budget -= size
                except (OSError, ValueError, KeyError, UnicodeError):
                    continue  # Invalid history stays on disk, not presented as valid experiments.

    def _entry(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise AcquisitionError('unknown_run')
        if self.log and self.log.run_id == run_id:
            return {'manifest': dict(self.log.manifest), 'rows': list(self.log.records.values())}
        entry = self.history.get(run_id)
        if not entry:
            raise AcquisitionError('unknown_run')
        return entry

    def _info(self, run_id):
        entry = self._entry(run_id)
        manifest, rows = entry['manifest'], entry['rows']
        return {'run_id': run_id, 'purpose': manifest['purpose'], 'group_id': manifest.get('GroupID', ''),
                'room_id': manifest.get('RoomID', ''), 'closed_at': manifest.get('closed_at'),
                'started_at': manifest.get('started_at'), 'events': [dict(r) for r in rows[-50:]],
                'event_count': len(rows), 'status_counts': dict(Counter(r['Status'] for r in rows)),
                'interrupted': not manifest.get('closed_at') and not (self.log and self.log.run_id == run_id and not self.log.closed)}

    def snapshot(self, selected=None):
        with self.lock:
            ids = set(self.history)
            if self.log:
                ids.add(self.log.run_id)
            runs = []
            for run_id in sorted(ids, reverse=True)[:100]:
                info = self._info(run_id)
                runs.append({key: value for key, value in info.items() if key != 'events'})
            active = self.log.run_id if self.log and not self.log.closed else None
            chosen = selected or active or (runs[0]['run_id'] if runs else None)
            return {'enabled': True, 'active_run_id': active, 'busy': self.busy,
                    'sensor_only': self.sensor_only,
                    'allowed_purposes': ['bench_diagnostic'] if self.sensor_only else list(PURPOSES),
                    'operation': self.operation, 'committing': self.committing,
                    'closing': self.closing, 'error': self.last_error, 'runs': runs,
                    'selected_run': self._info(chosen) if chosen else None,
                    'stations': list(self.stations), 'max_events': MAX_EVENTS,
                    'source': 'existing_in_process_snapshot',
                    'history_scope': 'Most recent bounded startup cache; disk files are never deleted',
                    'limitations': 'Manual stationary association; next published outcome, not a physical trigger or autonomous position measurement'}

    def _remember(self, log):
        # Called from worker, outside the state lock; no file I/O.
        rows, manifest = list(log.records.values()), dict(log.manifest)
        entry = {'manifest': manifest, 'rows': rows, 'csv': csv_bytes(rows),
                 'manifest_bytes': json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2).encode()}
        with self.lock:
            self.history[log.run_id] = entry
            while len(self.history) > 100 or sum(len(e['csv']) + len(e['manifest_bytes']) for e in self.history.values()) > MAX_HISTORY_BYTES:
                oldest = min(self.history)
                self.history.pop(oldest)

    def _launch(self, operation, function):
        self.busy, self.operation, self.last_error, self.committing = True, operation, None, False
        self.cancel_event.clear()

        def work():
            try:
                function()
            except CaptureCancelled:
                pass
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)[:500]
            finally:
                close_failed = False
                while True:
                    with self.lock:
                        log = self.log
                        closing = self.closing or self.cancel_event.is_set() and operation == 'start'
                    if log:
                        try:
                            if closing and not log.closed:
                                log.close()
                            self._remember(log)
                        except (LogPersistenceError, OSError, ValueError) as exc:
                            close_failed = True
                            with self.lock:
                                self.last_error = str(exc)[:500]
                    with self.lock:
                        late_close = self.closing or self.cancel_event.is_set() and operation == 'start'
                        if log and late_close and not log.closed and not close_failed:
                            continue  # A close accepted during cache update must not be lost.
                        self.busy = self.closing = self.committing = False
                        self.operation = None
                        break

        self.worker = threading.Thread(target=work, name='station-log-' + operation, daemon=True)
        try:
            self.worker.start()
        except RuntimeError:
            self.busy, self.operation = False, None
            raise AcquisitionError('recorder_worker_unavailable')

    def _start(self, data):
        if self.busy or self.closing:
            raise AcquisitionError('capture_in_progress')
        if self.log and not self.log.closed:
            raise AcquisitionError('run_already_open')
        if set(data) - {'action', 'purpose', 'group_id', 'room_id', 'notes', 'stations'}:
            raise AcquisitionError('unexpected_fields')
        purpose = data.get('purpose')
        if purpose not in PURPOSES:
            raise AcquisitionError('invalid_purpose')
        if self.sensor_only and purpose != 'bench_diagnostic':
            raise AcquisitionError('sensor_only_bench_required')
        group, room = text(data.get('group_id', ''), 'group_id', 80), text(data.get('room_id', ''), 'room_id', 80)
        notes, stations = text(data.get('notes', ''), 'notes', 2000), {}
        supplied = data.get('stations', [])
        if purpose == 'bench_diagnostic':
            if supplied or group or room:
                raise AcquisitionError('bench_cannot_claim_room_coordinates')
            stations['BENCH_UNLOCATED'] = Station('BENCH_UNLOCATED', notes=notes)
        else:
            if not group or not room:
                raise AcquisitionError('formal_identifiers_required')
            if not isinstance(supplied, list) or not 1 <= len(supplied) <= 100:
                raise AcquisitionError('measured_stations_required')
            for item in supplied:
                if not isinstance(item, dict) or set(item) - {'station_id', 'x_m', 'y_m', 'height_m', 'notes'}:
                    raise AcquisitionError('invalid_station')
                key = text(item.get('station_id'), 'station_id', 80, True)
                if key in stations:
                    raise AcquisitionError('duplicate_station_id')
                station = Station(key, item.get('x_m'), item.get('y_m'), item.get('height_m'),
                                  text(item.get('notes', ''), 'station_notes', 1000))
                station.validate(formal=True)
                stations[key] = station

        def create():
            parent = self.root / purpose
            if parent.is_symlink():
                raise AcquisitionError('invalid_output_directory')
            metadata = {'source': 'existing MonitorService snapshot; no extra hardware reader',
                        'source_sha256': {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                          for name in ('acquisition.py', 'experiment_log.py', 'server.py', 'hardware_worker.py', 'motor_control.py')},
                        'notes': notes, 'stations': [s.__dict__ for s in stations.values()]}
            log = RunLog(parent, purpose=purpose, group_id=group, room_id=room, metadata=metadata,
                         now=self.now, transport='in_process_snapshot',
                         motion_policy='sensor_only' if self.sensor_only else 'zero_command')
            with self.lock:
                self.log, self.stations = log, stations
        self._launch('start', create)

    def _capture(self, data):
        if set(data) != {'action', 'station_id'}:
            raise AcquisitionError('invalid_capture_request')
        if not self.log or self.log.closed:
            raise AcquisitionError('no_open_run')
        if self.busy or self.closing:
            raise AcquisitionError('capture_in_progress')
        if len(self.log.records) >= MAX_EVENTS:
            raise AcquisitionError('run_event_limit_reached')
        key = data.get('station_id')
        if not isinstance(key, str) or key not in self.stations:
            raise AcquisitionError('unknown_station')
        log, station = self.log, self.stations[key]

        def wait(seconds):
            if self.cancel_event.wait(seconds):
                raise CaptureCancelled()

        def fetch(timeout):
            if self.cancel_event.is_set():
                raise CaptureCancelled()
            state = self.fetch_status(timeout)
            if self.cancel_event.is_set():
                raise CaptureCancelled()
            return state

        def complete(point, status, **kwargs):
            # Linearization point: accepted cancellation wins before commit begins.
            # Once claimed, cancel returns an explicit conflict, never fake acceptance.
            with self.lock:
                if self.cancel_event.is_set():
                    status = 'cancelled'
                    kwargs['explanation'] = 'Operator cancelled before terminal commit'
                self.committing = True
            return log.finish(point, status, **kwargs)  # fsync outside the state lock

        self._launch('capture', lambda: capture_station(log, station, fetch, timeout=self.timeout,
                                                       wait=wait, complete=complete))

    def command(self, data):
        if not isinstance(data, dict):
            raise AcquisitionError('invalid_request')
        with self.lock:
            if self.stopping:
                raise AcquisitionError('recorder_stopping')
            action = data.get('action')
            if action == 'start_run':
                self._start(data)
            elif action == 'capture':
                self._capture(data)
            elif action in ('cancel', 'close_run'):
                if set(data) != {'action'}:
                    raise AcquisitionError('unexpected_fields')
                if self.busy:
                    if action == 'cancel' and (self.committing or self.closing):
                        raise AcquisitionError('result_committing')
                    self.closing = action == 'close_run' or self.closing
                    self.cancel_event.set()
                elif action == 'close_run' and self.log and not self.log.closed:
                    self.closing = True
                    self._launch('close', self.log.close)
            else:
                raise AcquisitionError('unknown_action')
            return self.snapshot()

    def export(self, run_id, kind):
        with self.lock:
            if kind not in ('csv', 'manifest'):
                raise AcquisitionError('unknown_export')
            entry = self._entry(run_id)
            # Capture references under lock; serialization does not hold up status.
            rows, manifest = entry['rows'], entry['manifest']
            raw = entry.get('csv' if kind == 'csv' else 'manifest_bytes')
        if raw is None:
            raw = csv_bytes(rows) if kind == 'csv' else json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2).encode()
        if len(raw) > MAX_FILE:
            raise AcquisitionError('export_too_large')
        filename = 'temperature_points_' + run_id + '.csv' if kind == 'csv' else 'run_manifest.json'
        return filename, raw

    def close(self):
        with self.lock:
            self.stopping = self.closing = True
            self.cancel_event.set()
            worker = self.worker
        if worker:
            worker.join(timeout=2)
        with self.lock:
            busy, log = self.busy, self.log
        if busy:
            self.last_error = 'Capture did not stop; persisted pending data is preserved'
        elif log and not log.closed and not log.failed:
            log.close()
