"""Bounded Mac-only UART query; no motor commands, firmware writes or scans."""
from pathlib import Path
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
import plistlib
import select
import stat
import struct
import subprocess
import termios
import time

OUT = Path(__file__).resolve().parent
QUERIES = (('configuration', b'$read_flash#'), ('battery', b'$read_vol#'))


def endpoint():
    raw = subprocess.check_output(['/usr/sbin/ioreg', '-p', 'IOService', '-l', '-a'], timeout=10)
    matches = set()
    def walk(obj, matched=False):
        if isinstance(obj, list):
            for value in obj:
                walk(value, matched)
        elif isinstance(obj, dict):
            matched = matched or (obj.get('idVendor') == 0x1A86 and obj.get('idProduct') == 0x7522)
            if matched and isinstance(obj.get('IOCalloutDevice'), str):
                matches.add(obj['IOCalloutDevice'])
            for child in obj.get('IORegistryEntryChildren', []):
                walk(child, matched)
    walk(plistlib.loads(raw))
    if len(matches) != 1:
        raise RuntimeError('Need exactly one serial endpoint under 1A86:7522: ' + repr(matches))
    name = matches.pop()
    if not name.startswith('/dev/cu.wchusbserial'):
        raise RuntimeError('Unexpected callout device: ' + name)
    return Path(name)


def main():
    path = endpoint()
    original_stat = path.stat()
    assert stat.S_ISCHR(original_stat.st_mode)
    ownership = subprocess.run(['/usr/sbin/lsof', '-nP', '-F', 'p', str(path)], capture_output=True, text=True, timeout=5)
    if ownership.returncode != 1 or ownership.stdout.strip():
        raise RuntimeError('Port is owned or ownership check indeterminate: ' + ownership.stdout + ownership.stderr)
    report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'host': 'Mac',
              'device': str(path), 'vid_pid': '1A86:7522', 'baudrate': 115200,
              'format': '8N1', 'hardware_flow_control': False,
              'queries': [], 'motion_commands_sent': 0, 'firmware_or_persistent_config_changed': False}
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        assert os.fstat(fd).st_rdev == original_stat.st_rdev
        fcntl.ioctl(fd, termios.TIOCEXCL)
        settings = termios.tcgetattr(fd)
        settings[0] = 0
        settings[1] = 0
        settings[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        settings[3] = 0
        settings[4] = settings[5] = termios.B115200
        settings[6][termios.VMIN] = 0
        settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack('I', termios.TIOCM_DTR | termios.TIOCM_RTS))
        actual = termios.tcgetattr(fd)
        assert actual[4] == actual[5] == termios.B115200
        for name, packet in QUERIES:
            termios.tcflush(fd, termios.TCIFLUSH)
            ready = select.select([], [fd], [], 1)[1]
            if not ready or os.write(fd, packet) != len(packet):
                raise RuntimeError('Query write not completed')
            deadline = time.monotonic() + 2
            data = bytearray()
            while len(data) < 4096:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if select.select([fd], [], [], remaining)[0]:
                    try:
                        piece = os.read(fd, 4096 - len(data))
                    except BlockingIOError:
                        continue
                    if not piece:
                        raise RuntimeError('Serial device returned EOF')
                    data.extend(piece)
            item = {'name': name, 'query_ascii': packet.decode('ascii'), 'wait_seconds': 2,
                    'received_bytes': len(data), 'ascii': data.decode('ascii', errors='replace'), 'hex': data.hex()}
            report['queries'].append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
        report['query_execution_completed'] = True
        report['driver_reply_observed'] = any(q['received_bytes'] for q in report['queries'])
    finally:
        os.close(fd)
    name = 'mac_queries_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'
    with (OUT / name).open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'report': str(OUT / name), 'driver_reply_observed': report['driver_reply_observed'],
                      'serial_closed': True, 'motion_commands_sent': 0}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
