"""The Mac-session supervisor restarts SSH after network/power interruptions."""
from pathlib import Path
import os
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
SSH=['/usr/bin/ssh','-F',str(ROOT/'autoboat_ssh_config'),
     '-o','ConnectTimeout=4','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2']
try:
    result=subprocess.run(SSH+['-o','ControlMaster=no','-o','ControlPath=none',
                          '-o','ClearAllForwardings=yes','rbe201tc-wifi',
                          'python3 - '+str(int(time.time()))],
                          input=(ROOT/'sync_clock_before_connect.py').read_text(),text=True,
                          capture_output=True,timeout=15)
    if result.returncode:
        # Do not open a fresh tunnel on an unverified clock correction failure.
        print('Waiting for robot network/monitor readiness: '+result.stderr[-240:],flush=True)
        sys.exit(1)
    print(result.stdout.strip(),flush=True)
except (OSError,subprocess.TimeoutExpired) as error:
    print('Waiting for robot connection: '+type(error).__name__,flush=True)
    sys.exit(1)
os.execv('/usr/bin/ssh',SSH+['-o','ControlMaster=yes','-o','ControlPersist=no',
                            '-N','-T','rbe201tc-wifi'])
