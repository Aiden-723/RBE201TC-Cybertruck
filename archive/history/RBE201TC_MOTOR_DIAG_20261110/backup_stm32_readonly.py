"""Read-only STM32F103 ROM backup through documented Quad-MD auto ISP.
No erase/program/option-byte/unprotect commands. Memory commands are limited
strictly to documented factory metadata, option bytes and main Flash reads.
Always attempt normal boot/reset and close; web motor owner must be disabled.
"""
from pathlib import Path
from serial import Serial, PARITY_EVEN, PARITY_NONE
import hashlib
import json
import os
import stat
import struct
import time
import urllib.request

PORT = Path('/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0')
BACKUP_DIR = Path('/home/sunrise/rbe201tc-sensor-studio/diagnostics/rom_backup_20261110')


def api(ep):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8765/api/v1/' + ep, timeout=3) as response:
        return json.load(response)['data']


def main():
    c, a = api('control'), api('experiments')
    assert os.geteuid() == 0 and PORT.is_char_device()
    assert c['enabled'] is False and c['state'] == 'disabled' and c['armed'] is False and c['fault'] is None
    assert len(c['outputs']) == 4 and all(type(x) is int and x == 0 for x in c['outputs'])
    assert a['active_run_id'] is None and all(a[k] is False for k in ('busy', 'committing', 'closing'))
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
    port = Serial(port=None, baudrate=115200, parity=PARITY_EVEN, timeout=.05,
                  write_timeout=.5, exclusive=True)
    port.dtr, port.rts, port.port = False, False, str(PORT)
    report = {'purpose': 'read-only original Flash backup', 'flash_writes': 0,
              'option_writes': 0, 'erase': False, 'unprotect': False, 'motion_commands': 0}

    def read_n(n, seconds=2):
        data, end = bytearray(), time.monotonic() + seconds
        while len(data) < n and time.monotonic() < end:
            data.extend(port.read(n - len(data)))
        if len(data) != n:
            raise RuntimeError(f'Short ROM reply: expected {n}, received {len(data)} ({data.hex()})')
        return bytes(data)

    def write(data):
        assert port.write(data) == len(data)

    def ack(label):
        byte = read_n(1)
        if byte != b'\x79':
            raise RuntimeError(label + ': expected ACK79, received ' + byte.hex())

    def command(op):
        assert op in (0x00, 0x02, 0x11), 'No write/erase/unprotect/GO command permitted'
        write(bytes([op, op ^ 0xFF]))
        ack('command ' + hex(op))

    def counted_reply(op):
        command(op)
        count = read_n(1)[0]
        assert count < 64
        data = read_n(count + 1)
        ack('counted reply')
        return data

    def memory_read(address, count, flash_size):
        assert 1 <= count <= 256
        allowed = ((0x1FFFF7E0 <= address and address + count <= 0x1FFFF7F4)
                   or (0x1FFFF800 <= address and address + count <= 0x1FFFF810)
                   or (0x08000000 <= address and address + count <= 0x08000000 + flash_size))
        assert allowed, hex(address)
        command(0x11)
        raw = address.to_bytes(4, 'big')
        checksum = 0
        for byte in raw:
            checksum ^= byte
        write(raw + bytes([checksum]))
        ack('read address')
        write(bytes([count - 1, (count - 1) ^ 0xFF]))
        ack('read count')
        return read_n(count)

    port.open()
    try:
        port.rts = True
        port.dtr = False
        time.sleep(.1)
        port.dtr = True
        time.sleep(.1)
        port.reset_input_buffer()
        write(b'\x7f')
        ack('ROM sync')
        pid = counted_reply(0x02)
        report['pid_hex'] = pid.hex()
        assert pid == b'\x04\x14', 'Unexpected MCU family; do not assume flash geometry'
        capabilities = counted_reply(0x00)
        report['bootloader_version_hex'] = capabilities[:1].hex()
        report['supported_commands_hex'] = capabilities[1:].hex()
        assert 0x11 in capabilities[1:], 'ROM does not advertise READ_MEMORY'
        factory = memory_read(0x1FFFF7E0, 20, 0)
        flash_kib = int.from_bytes(factory[:2], 'little')
        report.update({'factory_metadata_hex': factory.hex(), 'flash_kib': flash_kib,
                       'uid_hex': factory[8:20].hex()})
        assert flash_kib in (128, 256, 384, 512), 'Implausible factory flash size'
        size = flash_kib * 1024
        vector = memory_read(0x08000000, 64, size)
        sp, pc = struct.unpack('<II', vector[:8])
        report.update({'vector_first64_hex': vector.hex(), 'initial_sp': hex(sp),
                       'reset_vector': hex(pc),
                       'vector_looks_executable': 0x20000000 <= sp <= 0x20010000 and pc & 1 == 1 and 0x08000000 <= (pc & ~1) < 0x08000000 + size})
        assert BACKUP_DIR.is_absolute() and str(BACKUP_DIR).startswith('/home/sunrise/rbe201tc-sensor-studio/diagnostics/')
        BACKUP_DIR.parent.mkdir(exist_ok=True)
        BACKUP_DIR.mkdir(mode=0o700, exist_ok=False)
        owner = Path('/home/sunrise').stat()
        os.chown(BACKUP_DIR, owner.st_uid, owner.st_gid)
        backups = []
        for index in (1, 2):
            data = bytearray()
            path = BACKUP_DIR / f'original_flash_read{index}.bin'
            with path.open('xb') as output:
                for offset in range(0, size, 256):
                    chunk = memory_read(0x08000000 + offset, min(256, size - offset), size)
                    data.extend(chunk)
                    output.write(chunk)
                    if (offset + len(chunk)) % 32768 == 0:
                        print('BACKUP_PROGRESS', json.dumps({'pass': index, 'bytes': len(data), 'total': size}), flush=True)
            path.chmod(0o600)
            os.chown(path, owner.st_uid, owner.st_gid)
            backups.append({'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                            'ff_bytes': data.count(255), 'zero_bytes': data.count(0)})
            if index == 1:
                report['contains_literal_1_7_3'] = b'1.7.3' in data
                for marker in (b'Motor_Version', b'read_flash', b'read_vol', b'Battery:'):
                    offset = data.find(marker)
                    report['marker_' + marker.decode('ascii')] = {'offset': offset, 'context_hex': bytes(data[max(0, offset - 16):offset + 80]).hex() if offset >= 0 else ''}
            print('BACKUP_PASS_COMPLETE', json.dumps(backups[-1]), flush=True)
        report['backups'] = backups
        report['double_read_matches'] = backups[0]['sha256'] == backups[1]['sha256']
        assert report['double_read_matches'], 'Flash read mismatch; do not attempt execution/programming'
        try:
            report['option_bytes_read_only_hex'] = memory_read(0x1FFFF800, 16, size).hex()
        except RuntimeError as error:
            report['option_bytes_read_error'] = str(error)
        metadata = BACKUP_DIR / 'read_only_metadata.json'
        with metadata.open('x') as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
        metadata.chmod(0o600)
        os.chown(metadata, owner.st_uid, owner.st_gid)
    except Exception as error:
        report['error'] = repr(error)
    finally:
        try:
            port.rts = False
            port.dtr = False
            time.sleep(.1)
            port.parity = PARITY_NONE
            port.dtr = True
            time.sleep(.3)
        finally:
            port.close()
    report['serial_closed'] = True
    print('READ_ONLY_BACKUP_RESULT', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
