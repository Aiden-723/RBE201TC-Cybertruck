"""Guarded CH340 bridge diagnostics. Does not flash MCU or send motion data.
Run on the RDK as root with the existing web motor module disabled.
"""
from pathlib import Path
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import urllib.request

PORT = Path('/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0')
SOURCE = Path('/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106/backend/motor_control.py')
SOURCE_SHA = 'cc34dade1671c1ef33543368eaab04819a8054258ccf272f649d854a9100eed3'
USBDEVFS_CONTROL = 0xC0185500


class Control(ctypes.Structure):
    _fields_ = [('request_type', ctypes.c_uint8), ('request', ctypes.c_uint8),
                ('value', ctypes.c_uint16), ('index', ctypes.c_uint16),
                ('length', ctypes.c_uint16), ('timeout_ms', ctypes.c_uint32),
                ('data', ctypes.c_void_p)]


def api(endpoint):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8765/api/v1/' + endpoint, timeout=3) as response:
        return json.load(response)['data']


def check_idle():
    assert os.geteuid() == 0
    c, a = api('control'), api('experiments')
    assert c['enabled'] is False and c['state'] == 'disabled' and c['armed'] is False and c['fault'] is None
    assert len(c['outputs']) == 4 and all(type(v) is int and v == 0 for v in c['outputs'])
    assert a['active_run_id'] is None and all(a[k] is False for k in ('busy', 'committing', 'closing'))
    assert PORT.is_char_device()
    dev = PORT.stat().st_rdev
    owners = set()
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            for f in (p / 'fd').iterdir():
                try:
                    s = f.stat()
                    if stat.S_ISCHR(s.st_mode) and s.st_rdev == dev:
                        owners.add(int(p.name))
                except OSError:
                    continue
        except OSError:
            continue
    assert not owners, sorted(owners)
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA
    pid = int(subprocess.check_output(['systemctl', 'show', 'rbe201tc-monitor.service', '-p', 'MainPID', '--value'], text=True))
    cmd = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
    assert b'backend/server.py\0' in cmd and b'--motor-port\0' not in cmd
    return pid


def usb_device():
    for p in (Path('/sys/class/tty') / PORT.resolve().name / 'device').resolve().parents:
        if (p / 'idVendor').is_file():
            assert (p / 'idVendor').read_text().strip() == '1a86'
            assert (p / 'idProduct').read_text().strip() == '7522'
            bus, number = int((p / 'busnum').read_text()), int((p / 'devnum').read_text())
            return p, Path(f'/dev/bus/usb/{bus:03d}/{number:03d}')
    raise RuntimeError('USB parent not found')


def read_control(fd, request, value=0):
    # Only known vendor-IN requests. This function cannot send UART data or
    # modify bridge registers, firmware, GPIO configuration or persistence.
    assert request in (0x5F, 0x95)
    assert (request == 0x5F and value == 0) or (request == 0x95 and value in (0x0706, 0x1805, 0x2727))
    buf = (ctypes.c_uint8 * 2)()
    ctrl = Control(0xC0, request, value, 0, 2, 1000, ctypes.cast(buf, ctypes.c_void_p))
    assert ctypes.sizeof(ctrl) == 24
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
    libc.ioctl.restype = ctypes.c_int
    count = libc.ioctl(fd, USBDEVFS_CONTROL, ctypes.byref(ctrl))
    result = {'request': hex(request), 'value': hex(value), 'received_count': count}
    if count < 0:
        error = ctypes.get_errno()
        result.update({'ok': False, 'errno': error, 'error': os.strerror(error)})
    else:
        result.update({'ok': count == 2, 'hex': bytes(buf[:count]).hex()})
    return result


def disable_flow_control(fd):
    # Exact WCH driver runtime configuration: WRITE_REG(0x2727, 0x0000).
    # No payload, UART data, NVM, firmware, or arbitrary register interface.
    ctrl = Control(0x40, 0x9A, 0x2727, 0, 0, 1000, None)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
    libc.ioctl.restype = ctypes.c_int
    count = libc.ioctl(fd, USBDEVFS_CONTROL, ctypes.byref(ctrl))
    if count != 0:
        error = ctypes.get_errno()
        raise OSError(error, 'Explicit bridge noflow request failed: ' + os.strerror(error))
    return {'request_type': '0x40', 'request': '0x9a', 'value': '0x2727',
            'index': '0x0000', 'data_bytes': 0, 'usb_completion_result': count,
            'readback_supported_not_assumed': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--disable-flow-control', action='store_true')
    args = parser.parse_args()
    pid = check_idle()
    sysfs, raw_node = usb_device()
    spec = importlib.util.spec_from_file_location('bridge_diagnostic_transport', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    transport = module.SerialPWM(str(PORT))
    fd = None
    report = {'mode': 'read_only_bridge_registers', 'monitor_pid': pid,
              'usb_sysfs': str(sysfs), 'raw_usb_node': str(raw_node),
              'motor_commands_sent': 0, 'bridge_register_writes': 0,
              'persistent_settings_changed': False}
    try:
        fd = os.open(raw_node, os.O_RDWR | os.O_CLOEXEC)
        report['chip_version'] = read_control(fd, 0x5F)
        report['modem_status_0706'] = read_control(fd, 0x95, 0x0706)
        report['break_lcr_1805'] = read_control(fd, 0x95, 0x1805)
        report['flow_2727_raw_not_yet_interpreted'] = read_control(fd, 0x95, 0x2727)
        if args.disable_flow_control:
            report['mode'] = 'official_runtime_disable_hardware_flow_control'
            report['bridge_register_writes'] = 1
            report['flow_disable_request'] = disable_flow_control(fd)
            transport.serial.rts = False
        report['serial_diagnostics'] = transport.probe()
    finally:
        if fd is not None:
            os.close(fd)
        transport.close()
    report['serial_and_raw_usb_closed'] = True
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
