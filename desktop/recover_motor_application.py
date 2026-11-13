"""Start already-backed-up original firmware via STM32 ROM GO, no flash write.
Requires exact two-copy backup hash, valid vector, confirmed MCU PID0414 and
current Flash vector matching backup. Keeps one fd open across GO and parity
change. Afterwards tests an atomic DTR/RTS application on a second open.
"""
from pathlib import Path
from serial import Serial, PARITY_EVEN, PARITY_NONE
import argparse
import array
import fcntl
import hashlib
import json
import os
import stat
import struct
import subprocess
import termios
import time
import urllib.request

PORT = Path('/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0')
BACKUP = Path('/home/sunrise/rbe201tc-sensor-studio/diagnostics/rom_backup_20261110')
EXPECTED_SHA = 'edfb23cb0ff28450248d4355ae12cda3cf34248495c418dff646d949bd77e22b'
_PAUSED_MONITOR = False


class AtomicModemSerial(Serial):
    def _apply_both_modem_lines(self):
        mask = (termios.TIOCM_DTR if self._dtr_state else 0) | (termios.TIOCM_RTS if self._rts_state else 0)
        fcntl.ioctl(self.fd, termios.TIOCMSET, array.array('i', [mask]), True)

    def _update_dtr_state(self):
        self._apply_both_modem_lines()

    def _update_rts_state(self):
        self._apply_both_modem_lines()


def api(ep):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open('http://127.0.0.1:8765/api/v1/' + ep, timeout=3) as response:
        return json.load(response)['data']


def assert_idle():
    if _PAUSED_MONITOR:
        assert int(subprocess.check_output(['systemctl', 'show', 'rbe201tc-monitor.service', '-p', 'MainPID', '--value'], text=True)) == 0
    else:
        c, a = api('control'), api('experiments')
        assert c['enabled'] is False and c['state'] == 'disabled' and c['armed'] is False and c['fault'] is None
        assert len(c['outputs']) == 4 and all(type(v) is int and v == 0 for v in c['outputs'])
        assert a['active_run_id'] is None and all(a[k] is False for k in ('busy', 'committing', 'closing'))
    assert PORT.is_char_device()
    dev = PORT.stat().st_rdev
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fds = list((proc / 'fd').iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                s = fd.stat()
            except OSError:
                continue
            assert not (stat.S_ISCHR(s.st_mode) and s.st_rdev == dev), 'UART owner: ' + proc.name


def query_application(port):
    rows = []
    for name, packet in (('configuration', b'$read_flash#'), ('battery', b'$read_vol#')):
        port.reset_input_buffer()
        assert port.write(packet) == len(packet)
        data, end = bytearray(), time.monotonic() + 2
        while time.monotonic() < end and len(data) < 4096:
            data.extend(port.read(min(256, 4096 - len(data))))
        rows.append({'name': name, 'bytes': len(data), 'hex': data.hex(), 'ascii': data.decode('ascii', errors='replace')})
    return rows


def recover_original_application():
    assert os.geteuid() == 0
    assert_idle()
    saved = [(BACKUP / f'original_flash_read{i}.bin').read_bytes() for i in (1, 2)]
    assert saved[0] == saved[1] and len(saved[0]) == 262144
    assert hashlib.sha256(saved[0]).hexdigest() == EXPECTED_SHA
    sp, pc = struct.unpack('<II', saved[0][:8])
    assert 0x20000000 <= sp <= 0x20010000 and pc & 1 == 1 and 0x08000000 <= (pc & ~1) < 0x08040000
    assert b'Motor_Version' in saved[0] and b'1.7.3' in saved[0]
    report = {'action': 'ROM GO into verified original Flash, not a firmware update',
              'backup_sha256': EXPECTED_SHA, 'flash_writes': 0, 'erase': False,
              'option_writes': 0, 'motion_commands_sent': 0}
    port = Serial(port=None, baudrate=115200, parity=PARITY_EVEN, timeout=.05, write_timeout=.5, exclusive=True)
    port.dtr, port.rts, port.port = False, False, str(PORT)

    def read_n(n):
        data, end = bytearray(), time.monotonic() + 2
        while len(data) < n and time.monotonic() < end:
            data.extend(port.read(n - len(data)))
        if len(data) != n:
            raise RuntimeError(f'Short ROM response {len(data)}/{n}: {data.hex()}')
        return bytes(data)

    def send(data):
        assert port.write(data) == len(data)

    def ack(label):
        value = read_n(1)
        if value != b'\x79':
            raise RuntimeError(label + ': NACK/unexpected ' + value.hex())

    def command(op):
        assert op in (0x02, 0x11, 0x21)
        send(bytes([op, op ^ 255]))
        ack(hex(op))

    def address(base):
        assert base == 0x08000000
        data, checksum = base.to_bytes(4, 'big'), 0
        for b in data:
            checksum ^= b
        send(data + bytes([checksum]))
        ack('address')

    port.open()
    try:
        port.rts, port.dtr = True, False
        time.sleep(.1)
        port.dtr = True
        time.sleep(.1)
        port.reset_input_buffer()
        send(b'\x7f')
        ack('sync')
        command(0x02)
        length = read_n(1)[0] + 1
        assert length == 2
        pid = read_n(length)
        ack('ID tail')
        assert pid == b'\x04\x14'
        command(0x11)
        address(0x08000000)
        send(b'\x07\xf8')
        ack('vector read length')
        assert read_n(8) == saved[0][:8]
        command(0x21)
        address(0x08000000)
        report['go_acknowledged'] = True
        # No close/reopen/reset; only restore the application's documented parity.
        port.parity = PARITY_NONE
        time.sleep(1)
        report['same_fd_after_go_dtr_true_rts_true'] = query_application(port)
    except Exception as error:
        report['error'] = repr(error)
    finally:
        port.close()
    print('GO_ORIGINAL_RESULT', json.dumps(report, ensure_ascii=False), flush=True)
    if not report.get('go_acknowledged'):
        return
    assert_idle()
    second = AtomicModemSerial(port=None, baudrate=115200, parity=PARITY_NONE, timeout=.05, write_timeout=.5, exclusive=True)
    second.dtr, second.rts, second.port = False, False, str(PORT)
    try:
        second.open()
        time.sleep(1)
        result = {'opening': 'DTR/RTS False applied atomically in one TIOCMSET',
                  'application_queries': query_application(second), 'motion_commands_sent': 0}
    finally:
        second.close()
    print('ATOMIC_REOPEN_RESULT', json.dumps(result, ensure_ascii=False), flush=True)


def main():
    global _PAUSED_MONITOR
    parser = argparse.ArgumentParser()
    parser.add_argument('--pause-monitor', action='store_true')
    args = parser.parse_args()
    sensor_pid = None
    if args.pause_monitor:
        c, a = api('control'), api('experiments')
        assert c['enabled'] is True and c['state'] == 'stopped' and c['armed'] is False and c['fault'] is None
        assert c['outputs'] == [0, 0, 0, 0] and c['drive_commands_sent'] == 0
        assert a['active_run_id'] is None and all(a[k] is False for k in ('busy', 'committing', 'closing'))
        pid = int(subprocess.check_output(['systemctl', 'show', 'rbe201tc-monitor.service', '-p', 'MainPID', '--value'], text=True))
        proc = Path('/proc') / str(pid)
        assert 'cockpit_driving_20261106' in str((proc / 'cwd').resolve())
        assert b'backend/server.py\0' in (proc / 'cmdline').read_bytes()
        sensor_pid = int(subprocess.check_output(['systemctl', 'show', 'rbe201tc-sensors.service', '-p', 'MainPID', '--value'], text=True))
        subprocess.run(['systemctl', 'stop', 'rbe201tc-monitor.service'], check=True, timeout=20)
        _PAUSED_MONITOR = True
    try:
        recover_original_application()
    finally:
        if _PAUSED_MONITOR:
            subprocess.run(['systemctl', 'start', 'rbe201tc-monitor.service'], check=True, timeout=20)
            deadline = time.monotonic() + 10
            while True:
                try:
                    c = api('control')
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.2)
            assert int(subprocess.check_output(['systemctl', 'show', 'rbe201tc-sensors.service', '-p', 'MainPID', '--value'], text=True)) == sensor_pid
            print('RESTORED_SERVICE', json.dumps({k: c.get(k) for k in ('enabled', 'armed', 'state', 'fault', 'outputs', 'command_mode', 'max_hold_seconds', 'drive_commands_sent', 'serial_diagnostics')}, ensure_ascii=False), flush=True)
            _PAUSED_MONITOR = False


if __name__ == '__main__':
    main()
