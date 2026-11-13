"""SOFTWARE_FIXTURE_NOT_COURSE_DATA: localhost:0, FakeSerial only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT / 'work/device-console-v2/backend'),
                str(ROOT / 'work/device-console-v2/backend/tests')]
from server import create_server, Handler
from motor_control import MotorControl
from test_motor_control import FakeSerial
from test_acquisition import Feed


def main():
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    serial = FakeSerial()
    motor = MotorControl(serial, mode='speed')
    base = Feed()
    mode_lock = threading.Lock()
    scenario = {'mode': 'hold', 'calls': 0}
    with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_WEB_') as directory:
        server = create_server(port=0, motor=motor, acquisition_dir=directory)
        assert server.server_address[1] != 8765

        def observe(timeout):
            with mode_lock:
                scenario['calls'] += 1
                mode = scenario['mode']
                if mode != 'hold' and scenario['calls'] % 2 == 0:
                    base.publish(failure=mode == 'failure')
            state = base(timeout)
            state['control'] = motor.snapshot() if server.motor else {'enabled': False}
            if mode == 'failure':
                state['sensors']['dht11']['error'] = '<img src=x onerror="window.__acq_xss=1"> SOFTWARE_FIXTURE_FAILURE'
            return state
        server.acquisition.fetch_status = observe

        class FixtureHandler(Handler):
            def do_POST(self):
                if self.path != '/__fixture/mode':
                    return super().do_POST()
                length = int(self.headers.get('Content-Length', '0'))
                if not 2 <= length <= 1024:
                    return self.reply(400, {'code': 'invalid_fixture'})
                data = json.loads(self.rfile.read(length))
                with mode_lock:
                    if data.get('mode') in ('hold', 'success', 'failure'):
                        scenario.update(mode=data['mode'], calls=0)
                    if type(data.get('motor_enabled')) is bool:
                        server.motor = motor if data['motor_enabled'] else None
                self.reply(200, {'fixture': 'SOFTWARE_FIXTURE_NOT_COURSE_DATA'})

            def do_GET(self):
                if self.path != '/__fixture/evidence':
                    return super().do_GET()
                log = server.acquisition.log
                rows = list(log.records.values()) if log else []
                self.reply(200, {'fixture': 'SOFTWARE_FIXTURE_NOT_COURSE_DATA',
                                 'run_id': log.run_id if log else None,
                                 'closed': bool(log and log.closed),
                                 'row_count': len(rows),
                                 'statuses': [r['Status'] for r in rows],
                                 'purposes': sorted({r['RunPurpose'] for r in rows}),
                                 'failure_values_blank': all(r['Temperature_°C'] == r['Humidity_%RH'] == '' for r in rows if r['Status'] != 'success'),
                                 'coordinates_blank': all(r[k] == '' for r in rows for k in ('X_m', 'Y_m', 'SensorHeight_m')),
                                 'raw_csv_sha256': hashlib.sha256(log.path.read_bytes()).hexdigest() if log else None,
                                 'virtual_nonzero_packets': sum(p not in (b'$pwm:0,0,0,0#', b'$spd:0,0,0,0#', b'$stop#') for p in serial.packets)})

        server.RequestHandlerClass = FixtureHandler
        motor.start()
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        print(json.dumps({'fixture': 'SOFTWARE_FIXTURE_NOT_COURSE_DATA',
                          'url': 'http://127.0.0.1:%d' % server.server_address[1],
                          'temporary_root': directory,
                          'started_at': datetime.now(timezone.utc).isoformat()}), flush=True)
        try:
            stopped.wait()
        finally:
            motor.close()
            server.shutdown()
            server.server_close()
            thread.join(3)
            assert not thread.is_alive()


if __name__ == '__main__':
    main()
