"""One-shot RDK X5 IIC voltage read. No scans or motor-control writes.

Only bus 5, 7-bit slave 0x26, documented read-only battery register 0x08.
The single write byte is a register selector; no parameter or motion data.
Existing monitor is paused only after stationary software-state checks and
restored in finally. Sensor worker is not restarted. Does not alter pinmux.
"""
from pathlib import Path
import ctypes
import fcntl
import json
import os
import stat
import subprocess
import time
import urllib.request

BUS = Path('/dev/i2c-5')
ADDR = 0x26
REGISTER = 0x08
MONITOR = 'rbe201tc-monitor.service'
SENSORS = 'rbe201tc-sensors.service'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(endpoint):
    with OPENER.open('http://127.0.0.1:8765/api/v1/' + endpoint, timeout=3) as response:
        return json.load(response)['data']


def service_pid(service):
    return int(subprocess.check_output(['systemctl', 'show', service, '-p', 'MainPID', '--value'], text=True).strip())


def owners():
    result = set()
    dev = BUS.stat().st_rdev
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            for fd in (process / 'fd').iterdir():
                try:
                    entry = fd.stat()
                    if stat.S_ISCHR(entry.st_mode) and entry.st_rdev == dev:
                        result.add(int(process.name))
                except OSError:
                    continue
        except OSError:
            continue
    return sorted(result)


class Message(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16),
                ('length', ctypes.c_uint16), ('buffer', ctypes.POINTER(ctypes.c_uint8))]


class Transaction(ctypes.Structure):
    _fields_ = [('messages', ctypes.POINTER(Message)), ('count', ctypes.c_uint32)]


def main():
    assert os.geteuid() == 0
    assert Path('/proc/device-tree/model').read_bytes().rstrip(b'\0') == b'D-Robotics RDK X5 V1.0'
    assert BUS.is_char_device()
    assert (Path('/sys/class/i2c-dev/i2c-5/device').resolve().as_posix().endswith('/341c0000.i2c/i2c-5'))
    assert not Path('/sys/bus/i2c/devices/5-0026').exists(), 'Device already bound to a kernel driver'
    assert owners() == [], 'IIC bus already owned'
    control = get('control')
    activity = get('experiments')
    assert control['armed'] is False
    assert len(control['outputs']) == 4 and all(type(v) is int and v == 0 for v in control['outputs'])
    disabled_no_serial = (control['enabled'] is False and control['state'] == 'disabled'
                          and control['fault'] is None
                          and not Path('/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0').exists())
    assert disabled_no_serial or (control['state'] == 'stopped' and control['fault'] is None) or (control['state'] == 'fault' and control['fault'] == 'serial_stop_failed')
    assert activity['active_run_id'] is None and all(activity[k] is False for k in ('busy', 'committing', 'closing'))
    old_pid, sensor_pid = service_pid(MONITOR), service_pid(SENSORS)
    process = Path('/proc') / str(old_pid)
    cmd = (process / 'cmdline').read_bytes()
    assert b'backend/server.py\0' in cmd
    if disabled_no_serial:
        # Confirmed boot behavior: server deliberately omits motor arguments
        # when the USB controller is absent. Do not enable or restart it.
        assert b'--motor-port\0' not in cmd and b'--motor-mode\0' not in cmd
    else:
        assert b'--motor-mode\0speed\0' in cmd
    assert 'cockpit_driving_20261106' in str((process / 'cwd').resolve())
    print('PREFLIGHT', json.dumps({'monitor_pid': old_pid, 'sensor_pid': sensor_pid,
          'bus': str(BUS), 'address': hex(ADDR), 'register': hex(REGISTER),
          'motion_commands': 0, 'scan': False}), flush=True)
    if not disabled_no_serial:
        subprocess.run(['systemctl', 'stop', MONITOR], check=True, timeout=20)
    result = {'bus': str(BUS), 'address_7bit': ADDR, 'register': REGISTER, 'read_bytes': 2,
              'motion_commands_sent': 0, 'persistent_config_changed': False,
              'monitor_already_disabled_without_serial': disabled_no_serial, 'samples': []}
    try:
        assert service_pid(MONITOR) == (old_pid if disabled_no_serial else 0)
        assert owners() == []
        fd = os.open(BUS, os.O_RDWR | os.O_CLOEXEC)
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
            libc.ioctl.restype = ctypes.c_int
            for index in range(3):
                selector = (ctypes.c_uint8 * 1)(REGISTER)
                response = (ctypes.c_uint8 * 2)()
                messages = (Message * 2)(Message(ADDR, 0, 1, selector), Message(ADDR, 1, 2, response))
                transfer = Transaction(messages, 2)
                ret = libc.ioctl(fd, 0x0707, ctypes.byref(transfer))  # I2C_RDWR
                if ret != 2:
                    error = ctypes.get_errno()
                    sample = {'index': index, 'ok': False, 'ioctl_result': ret,
                              'errno': error, 'error': os.strerror(error)}
                    result['samples'].append(sample)
                    print('SAMPLE', json.dumps(sample), flush=True)
                    break
                raw = bytes(response)
                sample = {'index': index, 'ok': True, 'hex': raw.hex(),
                          'voltage_v_from_documented_encoding': int.from_bytes(raw, 'big') / 10.0}
                result['samples'].append(sample)
                print('SAMPLE', json.dumps(sample), flush=True)
                if index < 2:
                    time.sleep(0.1)
        finally:
            os.close(fd)
    finally:
        assert owners() == [], 'Unexpected IIC owner after diagnostic close'
        if not disabled_no_serial:
            subprocess.run(['systemctl', 'start', MONITOR], check=True, timeout=20)
        else:
            assert service_pid(MONITOR) == old_pid
        assert service_pid(SENSORS) == sensor_pid
        result['monitor_restored_pid'] = service_pid(MONITOR)
        result['sensor_pid_unchanged'] = sensor_pid
    for index in range(30):
        try:
            final = get('control')
            break
        except Exception:
            time.sleep(0.2)
    else:
        raise RuntimeError('Monitor restarted but HTTP restoration not verified')
    result['final_control'] = {k: final.get(k) for k in ('enabled', 'armed', 'state', 'fault', 'outputs', 'drive_commands_sent')}
    print('RESULT', json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
