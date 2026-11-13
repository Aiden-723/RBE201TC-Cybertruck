#!/usr/bin/env python3
"""GET-only recorder for the existing console. Never opens GPIO/UART or sends POST."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import signal
import sys
from urllib.parse import urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from experiment_log import LogPersistenceError, RunLog, Station, capture_station


def load_stations(path):
    stations = {}
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'StationID', 'X_m', 'Y_m', 'SensorHeight_m'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Station CSV requires StationID,X_m,Y_m,SensorHeight_m')
        for row in reader:
            key = row['StationID'].strip()
            if key in stations:
                raise ValueError('Duplicate StationID: ' + key)
            station = Station(key, float(row['X_m']), float(row['Y_m']),
                              float(row['SensorHeight_m']), row.get('Notes', ''))
            station.validate(formal=True)
            stations[key] = station
    if not stations:
        raise ValueError('No measured station coordinates: fill the header-only station template first')
    return stations


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Recorder refuses redirected API requests')


class StatusClient:
    def __init__(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.port is None or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Use an explicit loopback HTTP URL, e.g. http://127.0.0.1:8765')
        self.url = url.rstrip('/')
        # Local traffic must not use a system HTTP proxy; never follow redirects.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def __call__(self, timeout):
        import os
        headers = {'Accept': 'application/json'}
        token = os.environ.get('DEVICE_CONSOLE_TOKEN')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(self.url + '/api/v1/status', headers=headers, method='GET')
        with self.opener.open(request, timeout=timeout) as response:
            data = response.read(131073)
        if len(data) > 131072:
            raise ValueError('Oversized status response')
        envelope = json.loads(data)
        result = envelope['data']
        if envelope.get('schema_version') != '1.0' or not isinstance(result, dict):
            raise ValueError('Unexpected console status schema')
        return result


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--url', default='http://127.0.0.1:8765')
    cli.add_argument('--purpose', choices=('bench_diagnostic', 'room_stationary'), default='bench_diagnostic')
    cli.add_argument('--output-dir', default=str(ROOT / 'data' / 'raw'))
    cli.add_argument('--stations', help='Measured station CSV; mandatory for room_stationary')
    cli.add_argument('--station', help='Capture one station then exit; omit in room mode for interactive stations')
    cli.add_argument('--group-id', default='')
    cli.add_argument('--room-id', default='')
    cli.add_argument('--notes', default='', help='Environment, measured origin, sensor settling, interventions')
    cli.add_argument('--timeout', type=float, default=10)
    args = cli.parse_args(argv)
    try:
        client = StatusClient(args.url)
        if args.purpose == 'room_stationary':
            if not args.stations or not args.group_id.strip() or not args.room_id.strip():
                raise ValueError('room_stationary requires --stations, --group-id, --room-id')
            stations = load_stations(args.stations)
            if args.station and args.station not in stations:
                raise ValueError('Unknown measured StationID: ' + args.station)
        else:
            if args.stations:
                raise ValueError('Bench diagnostics must not be passed off as coordinate-mapped room data')
            key = args.station or 'BENCH_UNLOCATED'
            stations = {key: Station(key, notes=args.notes)}
            args.station = key
        hashes = {}
        for relative in ('backend/hardware_worker.py', 'backend/server.py',
                         'backend/motor_control.py', 'backend/experiment_log.py',
                         'tools/record_stations.py'):
            hashes[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        log = RunLog(args.output_dir, purpose=args.purpose, group_id=args.group_id,
                     room_id=args.room_id, metadata={'source_url': client.url, 'notes': args.notes,
                                                    'source_sha256': hashes})
    except (OSError, ValueError, LogPersistenceError) as exc:
        cli.error(str(exc))
    print('RunID:', log.run_id, '\n原始CSV:', log.path, flush=True)
    print('只读现有采样。停稳并等待传感器适应后再触发；返回结果前保持位置不变。', flush=True)
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    result_code = 0
    try:
        while True:
            key = args.station or input('输入已测 StationID，或 q 结束本次运行：').strip()
            if not args.station and key.lower() == 'q':
                break
            if key not in stations:
                print('未知站点；未采集、未填写猜测坐标。', flush=True)
                continue
            row = capture_station(log, stations[key], client, timeout=args.timeout)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if row['Status'] != 'success':
                result_code = 2
            if args.station:
                break
    except (KeyboardInterrupt, EOFError):
        print('\n记录结束；未完成事件保留为 cancelled。', flush=True)
        result_code = 130
    except (OSError, ValueError, LogPersistenceError) as exc:
        print('记录失败：' + str(exc), file=sys.stderr)
        result_code = 1
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            log.close()
        except (OSError, ValueError, LogPersistenceError) as exc:
            print('无法完成持久记录，保留现有原始文件：' + str(exc), file=sys.stderr)
            result_code = 1
    return result_code


if __name__ == '__main__':
    raise SystemExit(main())
