"""Asynchronous autonomous trial journal and operator outcomes; never controls motors."""
from datetime import datetime, timezone
from pathlib import Path
import copy
import json
import os
import queue
import threading
import time
import uuid

RESULTS = {'passed': '正常通过', 'straight_drift': '直线偏离', 'overshot': '冲出黑线',
           'underturn': '转弯不足／中途停住', 'oscillation': '转过头／来回摆动',
           'no_motion': '轮子不动／起步困难', 'manual_stop': '主动结束测试', 'custom': '自行填写'}


class LineTrials:
    def __init__(self, directory, configuration=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.configuration = configuration or {}
        self.lock = threading.RLock()
        self.records = {}
        self.active = None
        self.error = None
        self.closed = False
        self.queue = queue.SimpleQueue()
        self.stream = None
        for path in self.directory.glob('line_*.json'):
            record = json.loads(path.read_text())
            if record['status'] == 'running':
                journal = self.directory / (record['id'] + '.jsonl')
                events = []
                if journal.exists():
                    for line in journal.read_text().splitlines():
                        try: events.append(json.loads(line))
                        except ValueError: break
                if events:
                    last = events[-1]
                    start, end = record.get('start_recorded_monotonic'), last.get('recorded_monotonic')
                    if start is not None and end is not None:
                        record['duration_s'] = max(0, end - start)
                    record['event_count'] = len(events)
                    record['sample_count'] = sum(e.get('event') == 'sample' for e in events)
                    record['patterns'], record['actions'] = {}, {}
                    for event in events:
                        if event.get('event') == 'sample':
                            pattern = ''.join(map(str, event.get('values') or [])) or 'unknown'
                            record['patterns'][pattern] = record['patterns'].get(pattern, 0) + 1
                            action = event.get('reason', 'unknown')
                            record['actions'][action] = record['actions'].get(action, 0) + 1
                    record['last_recorded_at'] = last.get('recorded_at')
                record.update(status='interrupted', stop_reason='service_restart', ended_at=None)
                self._save(record)
            self.records[record['id']] = record
        self.worker = threading.Thread(target=self._work, name='line-trial-writer', daemon=True)
        self.worker.start()

    def _save(self, record):
        path = self.directory / (record['id'] + '.json')
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def observe(self, event):
        # Called under the follower lock: enqueue only, no filesystem I/O or waits.
        try:
            if not self.closed:
                self.queue.put({**event, 'recorded_at': datetime.now(timezone.utc).isoformat(), 'recorded_monotonic': time.monotonic()})
        except Exception:
            self.error = 'record_queue_failed'

    def _consume(self, event):
        if event['event'] == 'start_armed':
            if self.active is not None:
                self._finish('superseded', event)
            trial_id = 'line_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:12]
            self.active = trial_id
            self.records[trial_id] = dict(id=trial_id, started_at=event['recorded_at'], ended_at=None,
                start_monotonic=event.get('monotonic'), start_recorded_monotonic=event['recorded_monotonic'], duration_s=0, status='running', stop_reason=None,
                result=None, event_count=0, sample_count=0, patterns={}, actions={},
                configuration={**self.configuration, **event.get('configuration', {})})
            self.stream = (self.directory / (trial_id + '.jsonl')).open('x', encoding='utf-8')
            self._save(self.records[trial_id])
        if self.active is None:
            return
        record = self.records[self.active]
        self.stream.write(json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n')
        self.stream.flush()
        record['event_count'] += 1
        if event['event'] == 'sample':
            record['sample_count'] += 1
            pattern = ''.join(map(str, event['values'])) if event.get('values') else 'unknown'
            record['patterns'][pattern] = record['patterns'].get(pattern, 0) + 1
            action = event.get('reason', 'unknown')
            record['actions'][action] = record['actions'].get(action, 0) + 1
        start, end = record['start_recorded_monotonic'], event.get('recorded_monotonic')
        if start is not None and end is not None:
            record['duration_s'] = max(0, end - start)
        if event['event'] in ('failure', 'stop'):
            self._finish(event.get('reason', 'stopped'), event)

    def _finish(self, reason, event):
        if self.stream:
            self.stream.flush()
            os.fsync(self.stream.fileno())
        record = self.records[self.active]
        record.update(status='finished', stop_reason=reason, ended_at=event['recorded_at'],
                      final_state={k: copy.deepcopy(v) for k, v in event.items() if k != 'configuration'})
        self._save(record)
        if self.stream:
            self.stream.close()
        self.stream = None
        self.active = None

    def _work(self):
        while True:
            event = self.queue.get()
            if event is None:
                break
            if event['event'] == '_barrier':
                event['barrier'].set()
                continue
            try:
                with self.lock:
                    self._consume(event)
            except (OSError, ValueError, TypeError):
                self.error = 'record_persistence_failed'
                if self.stream:
                    self.stream.close()
                self.stream = None
                self.active = None
        if self.stream:
            self.stream.close()
            self.stream = None

    def flush(self):
        # A barrier for shutdown/tests, never used by the driving callback.
        barrier = threading.Event()
        self.queue.put({'event': '_barrier', 'barrier': barrier})
        barrier.wait(5)

    def snapshot(self):
        with self.lock:
            return dict(enabled=True, active_id=self.active, error=self.error,
                        records=[{key: copy.deepcopy(record.get(key)) for key in ('id', 'started_at', 'ended_at', 'duration_s', 'status', 'stop_reason', 'result', 'sample_count')}
                                 for record in sorted(self.records.values(), key=lambda r:r['started_at'], reverse=True)])

    def annotate(self, data):
        if (not isinstance(data, dict) or set(data) != {'trial_id', 'category', 'note'} or
                data['category'] not in RESULTS or not isinstance(data['note'], str) or len(data['note']) > 2000 or
                (data['category'] == 'custom' and not data['note'].strip())):
            raise ValueError('invalid_trial_result')
        with self.lock:
            record = self.records.get(data['trial_id'])
            if record is None or record['status'] == 'running':
                raise ValueError('trial_not_finished')
            changed = copy.deepcopy(record)
            changed['result'] = dict(category=data['category'], label=RESULTS[data['category']],
                                     note=data['note'].strip(), saved_at=datetime.now(timezone.utc).isoformat())
            self._save(changed)
            self.records[changed['id']] = changed
            return copy.deepcopy(changed)

    def export(self):
        with self.lock:
            records = copy.deepcopy(list(self.records.values()))
        exported = []
        for record in records:
            path = self.directory / (record['id'] + '.jsonl')
            events = []
            if path.exists():
                for line in path.read_text().splitlines():
                    try: events.append(json.loads(line))
                    except ValueError: break  # A power loss can leave one incomplete trailing line.
            exported.append(dict(summary=record, events=events))
        return dict(trials=exported, motion_feedback='unverified', description='Commanded motor outputs and sampled probe states; not measured wheel speed.')

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.queue.put(None)
        self.worker.join(5)
