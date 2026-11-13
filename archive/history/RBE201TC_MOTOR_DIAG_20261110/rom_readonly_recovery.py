"""Board-specific automatic ISP identification, never erase/program/unprotect.
Based on Yahboom Quad-MD FlyMcu settings: reset at DTR Low, ISP at RTS High;
100ms reset pulse, then DTR High. Booleans are host logical modem states,
not measured voltages. Always attempt a normal-mode reset and close UART.
"""
from pathlib import Path
from serial import Serial, PARITY_EVEN, PARITY_NONE
import json
import os
import stat
import time
import urllib.request

PORT = Path('/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0')


def api(ep):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8765/api/v1/' + ep, timeout=3) as response:
        return json.load(response)['data']


def main():
    assert os.geteuid() == 0
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
    s = Serial(port=None, baudrate=115200, parity=PARITY_EVEN, timeout=.05,
               write_timeout=.5, exclusive=True)
    s.dtr, s.rts, s.port = False, False, str(PORT)
    report = {'method': 'Yahboom Quad-MD documented automatic ISP, read-ID only',
              'external_doc': 'https://www.yahboom.net/study/Quad-MD-Module/15475',
              'logical_line_transitions': [], 'nonzero_motor_commands': 0,
              'flash_writes': False, 'option_writes': False, 'erase': False,
              'read_unprotect': False}

    def lines(dtr, rts, label):
        s.rts, s.dtr = rts, dtr
        report['logical_line_transitions'].append({'label': label, 'dtr': dtr, 'rts': rts})

    def read_n(n, seconds=1):
        out, end = bytearray(), time.monotonic() + seconds
        while len(out) < n and time.monotonic() < end:
            out.extend(s.read(n - len(out)))
        return bytes(out)

    def rom_send(packet):
        assert packet in (b'\x7f', b'\x02\xfd')
        assert s.write(packet) == len(packet)

    s.open()
    try:
        lines(False, True, 'documented reset low with ISP high')
        time.sleep(.1)
        lines(True, True, 'release reset while ISP remains high')
        time.sleep(.1)
        s.reset_input_buffer()
        rom_send(b'\x7f')
        report['rom_sync_hex'] = read_n(1).hex()
        s.reset_input_buffer()
        rom_send(b'\x02\xfd')
        ack = read_n(1)
        report['rom_get_id_ack_hex'] = ack.hex()
        if ack == b'\x79':
            count = read_n(1)
            report['rom_count_hex'] = count.hex()
            if count and count[0] < 32:
                identity, tail = read_n(count[0] + 1), read_n(1)
                report['rom_identity_hex'], report['rom_trailing_ack_hex'] = identity.hex(), tail.hex()
                report['valid_rom_id_frame'] = len(identity) == count[0] + 1 and tail == b'\x79'
            else:
                report['valid_rom_id_frame'] = False
        else:
            report['valid_rom_id_frame'] = False
    finally:
        try:
            if s.is_open:
                lines(False, False, 'normal boot selected, reset asserted')
                time.sleep(.1)
                s.parity = PARITY_NONE
                lines(True, False, 'normal boot selected, reset released')
                time.sleep(1)
                results = []
                for name, packet in (('configuration', b'$read_flash#'), ('battery', b'$read_vol#')):
                    s.reset_input_buffer()
                    assert s.write(packet) == len(packet)
                    data, end = bytearray(), time.monotonic() + 2
                    while time.monotonic() < end and len(data) < 4096:
                        data.extend(s.read(min(256, 4096 - len(data))))
                    results.append({'query': name, 'hex': data.hex(), 'ascii': data.decode('ascii', errors='replace'), 'bytes': len(data)})
                report['normal_run_queries_dtr_true_rts_false'] = results
        finally:
            s.close()
    report['serial_closed'] = True
    report['final_monitor_state'] = api('control')['state']
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
