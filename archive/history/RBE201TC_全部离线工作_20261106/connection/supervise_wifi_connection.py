"""Single Mac-session reconnect supervisor; start from the console launcher."""
from pathlib import Path
import fcntl
import os
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
if '--run' not in sys.argv:
    with (ROOT/'wifi/reconnect.log').open('ab') as log:
        subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--run'],
                         stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    raise SystemExit(0)
lock=(ROOT/'wifi/supervisor.lock').open('a')
try:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)
(ROOT/'wifi/supervisor.pid').write_text(str(os.getpid())+'\n')
stopping=False
child=None
def stop(*args):
    global stopping
    stopping=True
    if child is not None and child.poll() is None:child.terminate()
for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,stop)
try:
    while not stopping:
        child=subprocess.Popen([sys.executable,str(ROOT/'maintain_wifi_connection.py')])
        while child.poll() is None:
            if stopping:
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait()
                break
            time.sleep(.2)
        for _ in range(25):
            if stopping:break
            time.sleep(.2)
finally:
    if child is not None and child.poll() is None:child.terminate();child.wait(timeout=5)
