"""Run over SSH before the new local tunnel opens; never adjust during a run."""
import json
import math
import subprocess
import sys
import time
import urllib.request


def synchronize(target, *, now=time.time, read=None, execute=subprocess.run):
    if type(target) not in (int, float) or not math.isfinite(target) or not 1735689600 <= target <= 4102444800:
        raise ValueError('Invalid computer clock')
    if abs(now()-target) <= 5:
        return {'clock': 'already_current'}
    if read is None:
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def read(endpoint):
            with http.open('http://127.0.0.1:8765/api/v1/'+endpoint, timeout=3) as response:
                return json.load(response)['data']
    acquisition = read('experiments')
    control = read('control')
    if (acquisition.get('active_run_id') is not None or acquisition.get('busy') is not False
            or acquisition.get('sensor_only') is not True
            or control.get('enabled') is not False or control.get('armed') is not False
            or control.get('state') != 'disabled'):
        return {'clock': 'unchanged_active_run_or_control', 'attention_required': True}
    execute(['sudo', '-n', '/bin/date', '-u', '-s', '@'+str(int(target))],
            check=True, stdout=subprocess.DEVNULL, timeout=5)
    if abs(now()-target) > 5:
        raise RuntimeError('Clock correction was not verified')
    return {'clock': 'synchronized_from_mac', 'no_active_run': True}


if __name__ == '__main__':
    print(json.dumps(synchronize(int(sys.argv[1]))), flush=True)
