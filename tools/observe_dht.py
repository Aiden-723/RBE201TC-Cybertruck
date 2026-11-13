#!/usr/bin/env python3
"""Observe existing DHT cache only. Never opens GPIO, UART, or motor APIs."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import time


def stamp():
    return datetime.now().astimezone().isoformat()


def summarize(records):
    previous = None
    counts, errors = Counter(), Counter()
    values, intervals = [], []
    gaps = resets = bad_values = streak = longest = 0
    delta_success = delta_failure = 0
    for record in records:
        if record['kind'] not in ('baseline', 'sample'):
            continue
        item = record['snapshot']
        current = (item['successes'], item['failures'], item['monotonic'])
        if previous is None:
            previous = current
            continue  # Baseline predates the observation window.
        ds, df = current[0]-previous[0], current[1]-previous[1]
        dt = current[2]-previous[2]
        previous = current
        if min(ds, df) < 0 or dt < 0:
            resets += 1
            streak = 0
            continue  # Start a new segment; do not invent lost attempts.
        if ds+df == 0:
            continue
        delta_success += ds
        delta_failure += df
        gaps += max(0, ds+df-1)
        if ds+df != 1:
            streak = 0
        else:
            intervals.append(dt)
        status = item.get('status')
        counts[status] += 1
        if status == 'error':
            errors[str(item.get('error'))] += 1
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
        if status == 'live':
            value = item.get('value') or {}
            pair = (value.get('temperature_c'), value.get('humidity_pct'))
            if all(type(x) in (int, float) and math.isfinite(x) for x in pair) and -20 <= pair[0] <= 80 and 0 <= pair[1] <= 100:
                values.append(pair)
            else:
                bad_values += 1
    total = delta_success+delta_failure
    def span(index):
        return {'min': min(x[index] for x in values), 'max': max(x[index] for x in values)} if values else None
    return {
        'counter_attempts_in_observed_segments': total,
        'counter_successes': delta_success, 'counter_failures': delta_failure,
        'success_rate_pct': round(100*delta_success/total, 3) if total else None,
        'observed_outcomes': dict(counts), 'observed_error_reasons': dict(errors),
        'unobserved_outcomes_within_segments': gaps, 'counter_reset_segments': resets,
        'invalid_live_values': bad_values, 'observed_max_failure_streak': longest,
        'temperature_c': span(0), 'humidity_pct': span(1),
        'max_contiguous_publish_interval_seconds': max(intervals) if intervals else None,
        'read_error_events': sum(r['kind']=='read_error' for r in records),
        'stale_episodes': sum(r['kind']=='stale_start' for r in records),
        'limitations': 'Passive cache observation; not a reference accuracy test, room measurement or proof of stationary wheels. Baseline excluded. Counter gaps do not reveal missed values/reasons; resets exclude their first observation.'}


def observe(source, output, seconds, interval=.2):
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    meta = {'purpose': 'bench_dht_stability', 'started_at': stamp(), 'requested_seconds': seconds,
            'source': str(source), 'poll_seconds': interval, 'status': 'running',
            'nonzero_motor_commands_sent': 0}
    (output/'observation.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    records, previous, last_error, stale = [], None, None, False
    with (output/'raw_observations.jsonl').open('x', encoding='utf-8') as stream:
        def emit(kind, **fields):
            record = dict(kind=kind, observed_at=stamp(), observed_monotonic=time.monotonic(), **fields)
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
            stream.flush()
            records.append(record)
        while time.monotonic()-started < seconds:
            try:
                sample = json.loads(source.read_text())
                key = (sample['successes'], sample['failures'], sample['monotonic'])
                if not all(type(x) is int and x >= 0 for x in key[:2]) or not isinstance(key[2], (float,int)) or not math.isfinite(key[2]):
                    raise ValueError('Invalid sampler counters')
                if key != previous:
                    emit('baseline' if previous is None else 'sample', snapshot=sample)
                    previous = key
                too_old = time.monotonic()-sample['monotonic'] > 9
                if too_old != stale:
                    emit('stale_start' if too_old else 'stale_end')
                    stale = too_old
                last_error = None
            except (OSError, ValueError, KeyError, TypeError) as exc:
                error = type(exc).__name__+': '+str(exc)
                if error != last_error:
                    emit('read_error', error=error)
                    last_error = error
            time.sleep(min(interval, max(0, seconds-(time.monotonic()-started))))
    meta.update(status='complete', ended_at=stamp(), elapsed_seconds=time.monotonic()-started,
                raw_sha256=hashlib.sha256((output/'raw_observations.jsonl').read_bytes()).hexdigest())
    (output/'observation.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    result = summarize(records)
    (output/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/dev/shm/rbe201tc-sensor-studio/dht11.json'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=600)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('--seconds must be positive and finite')
    observe(args.source, args.output, args.seconds)
