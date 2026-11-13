"""Bounded metadata-only capture on the user's dedicated wired interface.
No payload dumps, no interface/IP configuration changes, no device commands.
Requires normal macOS administrator authorization; used only to discover peer.
"""
import subprocess

command = ['/usr/sbin/tcpdump', '-ni', 'en7', '-l', '-e', '-c', '40',
           'arp or icmp6 or (udp and (port 67 or port 68 or port 5353))']
try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    print('CAPTURE_EXIT', result.returncode)
    print(result.stdout)
    print(result.stderr)
except subprocess.TimeoutExpired as error:
    print('CAPTURE_BOUNDED_END_15_SECONDS')
    out, err = error.stdout or b'', error.stderr or b''
    print(out.decode(errors='replace') if isinstance(out, bytes) else out)
    print(err.decode(errors='replace') if isinstance(err, bytes) else err)
