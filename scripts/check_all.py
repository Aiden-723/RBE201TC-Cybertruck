"""Run offline tests only; no GPIO, real UART, SSH or deployment commands."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    node = shutil.which('node')
    if node is None:
        print('Node.js 22+ is required for frontend tests.', file=sys.stderr)
        return 2
    suites = [
        ([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'backend/tests'], ['backend', 'backend/tests']),
        ([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tools/tests'], ['backend', 'tools']),
        ([node, '--test', *[str(p.relative_to(ROOT)) for p in sorted((ROOT/'frontend/tests').glob('*.test.cjs'))]], []),
        ([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'desktop/tests'], ['desktop']),
    ]
    failed = False
    for command, module_paths in suites:
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(str(ROOT/p) for p in module_paths)
        print('\nRunning:', ' '.join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env)
        failed |= result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
