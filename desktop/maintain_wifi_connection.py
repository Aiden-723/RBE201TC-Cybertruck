"""One foreground SSH tunnel, supervised by the Mac login agent.

Only two pre-pinned robot addresses are eligible; do not reselect a healthy
connection. Clock preflight and forwarding always use the same SSH host.
"""
from pathlib import Path
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
HOSTS = ('rbe201tc-wired', 'rbe201tc-wifi')
SSH = ['/usr/bin/ssh', '-F', str(ROOT / 'autoboat_ssh_config'),
       '-o', 'ConnectTimeout=4', '-o', 'ServerAliveInterval=5',
       '-o', 'ServerAliveCountMax=2', '-o', 'ControlMaster=no',
       '-o', 'ControlPath=none', '-o', 'ControlPersist=no']


def connection_commands(host, target):
    if host not in HOSTS:
        raise ValueError('Unapproved robot host')
    preflight = SSH + ['-o', 'ClearAllForwardings=yes', host,
                       'sudo -n /usr/bin/python3 - ' + str(int(target))]
    forward = SSH + ['-N', '-T', host]
    return preflight, forward


def main():
    source = ('CLOCK_SOURCE=' + repr((ROOT / 'sync_clock_before_connect.py').read_text()) + '\n'
              + 'RECOVERY_SOURCE=' + repr((ROOT / 'recover_motor_application.py').read_text()) + '\n'
              + (ROOT / 'prepare_console_before_connect.py').read_text())
    for host in HOSTS:
        preflight, forward = connection_commands(host, time.time())
        try:
            result = subprocess.run(preflight, input=source, text=True,
                                    capture_output=True, timeout=75)
            if result.returncode:
                print('Waiting for ' + host + ': ' + result.stderr[-240:], flush=True)
                continue
            print(host + ': ' + result.stdout.strip(), flush=True)
            # No -f, detached master, host-key learning, or second forward.
            os.execv('/usr/bin/ssh', forward)
        except (OSError, subprocess.TimeoutExpired) as error:
            print('Waiting for ' + host + ': ' + type(error).__name__, flush=True)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
