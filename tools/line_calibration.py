#!/usr/bin/env python3
"""Read-only line-reference capture/check/build; never applies settings or drives."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from record_stations import ROOT, StatusClient
# record_stations adds the existing backend directory to sys.path.
# Avoid importing a backend module with this script's own basename.
import importlib.util
spec = importlib.util.spec_from_file_location('line_reference_logic', ROOT / 'backend' / 'line_calibration.py')
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


def save_new(path, value):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    return path


def read_reference(path):
    path = Path(path).expanduser().resolve()
    raw = path.read_bytes()
    if len(raw) > 1048576:
        raise ValueError('Reference exceeds the 1 MiB limit')
    return json.loads(raw), {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def capture(client, surface, seconds, setup):
    if not math.isfinite(seconds) or not 0.5 <= seconds <= 30:
        raise ValueError('Reference sampling window must be between 0.5 and 30 seconds')
    samples, errors, seen = [], [], set()
    host = None
    started = datetime.now().astimezone().isoformat()
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            state = client(min(1, max(0.05, end - time.monotonic())))
            host = state['host']
            if host.get('is_rdk') is not True:
                raise ValueError('Not an RDK status response')
            outputs = state.get('control', {}).get('outputs')
            if (not isinstance(outputs, list) or len(outputs) != 4
                    or any(type(x) is not int or x != 0 for x in outputs)):
                raise ValueError('No zero-command reference accepted while motor outputs are nonzero or unknown')
            sensor = state['sensors']['line_sensors']
            if sensor['status'] != 'live' or not 0 <= logic.finite(sensor['age_seconds']) < 2:
                raise ValueError('Line sensor snapshot is not fresh/live')
            logic.bits(sensor['value'])
            if sensor['physical_pins'] != list(logic.PINS):
                raise ValueError('Existing sensor channel pin mapping changed')
            marker = (sensor['monotonic'], sensor['successes'])
            if marker not in seen:
                seen.add(marker)
                samples.append({key: sensor[key] for key in (
                    'value', 'monotonic', 'sampled_at', 'successes', 'failures', 'age_seconds', 'physical_pins')})
            time.sleep(min(0.08, max(0, end - time.monotonic())))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    except KeyboardInterrupt:
        errors.append('Operator interrupted the reference capture')
    return {'reference': surface, 'user_confirmed_ready': True, 'setup': setup,
            'recorded_at': started, 'ended_at': datetime.now().astimezone().isoformat(),
            'host': host, 'samples': samples, 'unique_samples': len(samples), 'errors': errors,
            'polarity_verified': False, 'physical_left_to_right_order': None,
            'method': 'HTTP GET only; no motor/GPIO/camera access or control requests',
            'physical_motor_power_verified': False}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    record = commands.add_parser('capture', help='Only run after the declared real surface is physically ready')
    record.add_argument('--surface', choices=('white_paper', 'black_tape'), required=True)
    record.add_argument('--url', default='http://127.0.0.1:8765')
    record.add_argument('--seconds', type=float, default=3.2)
    record.add_argument('--setup', required=True, help='Actual mounting height, material, lighting and motor-power setup')
    record.add_argument('--output', required=True, help='A NEW reference JSON, never overwritten')
    check = commands.add_parser('check', help='Validate an already saved reference, without connecting to hardware')
    check.add_argument('--surface', choices=('white_paper', 'black_tape'), required=True)
    check.add_argument('--input', required=True)
    build = commands.add_parser('build', help='Build a NEW staged calibration; never apply to live service')
    build.add_argument('--white', required=True)
    build.add_argument('--black', required=True)
    build.add_argument('--left-to-right', nargs=4, help='Only supply the four channel names after physical observation; no default order')
    build.add_argument('--output', required=True)
    args = cli.parse_args(argv)
    try:
        if args.command == 'capture':
            if not args.setup.strip():
                raise ValueError('Actual setup notes are required')
            reference = capture(StatusClient(args.url), args.surface, args.seconds, args.setup)
            path = save_new(args.output, reference)
            print('原始参考已保留:', path, flush=True)
            levels = logic.validate_reference(reference, args.surface)
            print(json.dumps({'stable_levels_X1_to_X4': levels,
                              'unique_samples': len(reference['samples']),
                              'polarity_verified': False}, ensure_ascii=False))
        elif args.command == 'check':
            reference, identity = read_reference(args.input)
            levels = logic.validate_reference(reference, args.surface)
            print(json.dumps({'input': identity, 'stable_levels_X1_to_X4': levels,
                              'unique_samples': len(reference['samples']),
                              'polarity_verified': False}, ensure_ascii=False, indent=2))
        else:
            white, white_id = read_reference(args.white)
            black, black_id = read_reference(args.black)
            calibration = logic.build_calibration(white, black, args.left_to_right)
            calibration['reference_files'] = {'white': white_id, 'black': black_id}
            calibration['created_at'] = datetime.now().astimezone().isoformat()
            path = save_new(args.output, calibration)
            print(json.dumps({'output': str(path), 'calibration': calibration}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print('标定未通过：' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
