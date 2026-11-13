"""Manual desktop-app connection owner. Never install or invoke login agents."""
from pathlib import Path
import fcntl
import json
import os
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RUNTIME = Path.home() / 'Library/Application Support/RBE201TC/connection'
STATE = RUNTIME / 'wifi'


def main():
    if '--run' not in sys.argv:
        app = Path.home() / 'Desktop/小车控制台.app'
        if not app.is_dir():
            print('Desktop control application is not installed.', flush=True)
            return 1
        return subprocess.run(['/usr/bin/open', str(app)]).returncode
    if '--owned-by-app' in sys.argv and os.getpgrp() != os.getpid():
        os.setsid()
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (STATE / 'supervisor.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Another robot connection is already running.', flush=True)
        return 73
    (STATE / 'supervisor.pid').write_text(str(os.getpid()) + '\n')
    stopping = False
    child = None

    def record(child_pid):
        path = STATE / 'state.json'
        temporary = STATE / ('state-' + str(os.getpid()) + '.tmp')
        assert path.absolute() == path.resolve() and temporary.resolve().parent == STATE.resolve()
        temporary.write_text(json.dumps({'supervisor_pid': os.getpid(),
                                         'child_pid': child_pid,
                                         'started_by_app': '--owned-by-app' in sys.argv}))
        os.replace(temporary, path)

    def stop(*args):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    record(None)
    try:
        while not stopping:
            child = subprocess.Popen([sys.executable, '-B', '-u',
                                      str(ROOT / 'maintain_wifi_connection.py')])
            record(child.pid)
            while child.poll() is None:
                if stopping:
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                    break
                time.sleep(.2)
            record(None)
            for _ in range(25):
                if stopping:
                    break
                time.sleep(.2)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        record(None)
        lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
