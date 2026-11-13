"""Sensor monitor, unchanged leased wheel controls, and optional station logging."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import ipaddress
import json
import os
import re
import socket
import secrets
import threading
from urllib.parse import urlsplit
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent.parent / 'frontend'
STATIC_FILES = {'/': ('index.html', 'text/html'),
                '/index.html': ('index.html', 'text/html'),
                '/styles.css': ('styles.css', 'text/css'),
                '/app.js': ('app.js', 'text/javascript')}
STATIC_FILES['/cybertruck-photo.jpg'] = ('cybertruck-photo.jpg', 'image/jpeg')
STATIC_FILES['/vehicle.js'] = ('vehicle.js', 'text/javascript')
STATIC_FILES['/cockpit.css'] = ('cockpit.css', 'text/css')
STATIC_FILES['/control.js'] = ('control.js', 'text/javascript')
STATIC_FILES['/acquisition.js'] = ('acquisition.js', 'text/javascript')
STATIC_FILES['/acquisition.css'] = ('acquisition.css', 'text/css')

from basic_line_follower import DEFAULT_CHANNEL_PERMUTATION
from collectors import LinuxCollector
from service import MonitorService, Telemetry
from readings import SensorStore
from motor_control import ControlError, MotorControl, SerialPWM
from driving_mode import DrivingMode
from acquisition import AcquisitionManager, AcquisitionError
from experiment_log import LogPersistenceError


from socketserver import ThreadingMixIn


class BoundedHTTPServer(ThreadingMixIn, HTTPServer):
    # Eight bounded handlers prevent a slow CSV download blocking control.
    # MotorControl retains its existing lock/lease/sequence arbitration.
    request_queue_size = 8
    request_deadline = 3.0
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args, **kwargs):
        self.request_slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def finish_request(self, request, client_address):
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(self.request_deadline, expire)
        timer.start()
        request.settimeout(self.request_deadline)
        try:
            super().finish_request(request, client_address)
        except (OSError, ValueError):
            pass
        finally:
            timer.cancel()
            timer.join()

    def server_close(self):
        manager = getattr(self, 'acquisition', None)
        try:
            if manager:
                manager.close()
        finally:
            super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'DeviceConsole/1.0'
    sys_version = ''

    def log_message(self, format, *args):
        pass  # Never log tokens, request targets or untrusted header values.

    def reply(self, code, data):
        body = json.dumps({'schema_version': '1.0',
                           'generated_at': datetime.now(timezone.utc).isoformat(),
                           'data' if code == 200 else 'error': data},
                          allow_nan=False, separators=(',', ':')).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        if code == 405:
            self.send_header('Allow', 'GET')
        if code == 401:
            self.send_header('WWW-Authenticate', 'Bearer')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
        self.close_connection = True

    def reject_method(self):
        self.reply(405, {'code': 'read_only', 'allowed': ['GET']})

    do_PUT = do_PATCH = do_DELETE = reject_method
    do_OPTIONS = do_TRACE = do_CONNECT = do_HEAD = reject_method

    def authenticated(self):
        if not self.server.token:
            return True
        supplied = self.headers.get_all('Authorization', [])
        return len(supplied) == 1 and hmac.compare_digest(
            supplied[0].encode('utf-8'), ('Bearer ' + self.server.token).encode('utf-8'))

    def local_control_host(self):
        hosts = self.headers.get_all('Host', [])
        if len(hosts) != 1:
            return False
        try:
            parsed = urlsplit('http://' + hosts[0])
            return (parsed.hostname in ('127.0.0.1', 'localhost', '::1') and
                    parsed.port is not None and not parsed.username and
                    not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:
            return False

    def acquisition_post(self):
        if not self.authenticated():
            self.reply(401, {'code': 'unauthorized'})
            return
        origins = self.headers.get_all('Origin', [])
        keys = self.headers.get_all('X-Experiment-Key', [])
        if (not self.local_control_host() or len(origins) != 1 or
                origins[0] != 'http://' + self.headers['Host'] or len(keys) != 1 or
                not hmac.compare_digest(keys[0].encode('utf-8'), self.server.experiment_key.encode('utf-8'))):
            self.reply(403, {'code': 'experiment_origin_rejected'})
            return
        if self.server.acquisition is None:
            self.reply(409, {'code': 'recorder_unavailable'})
            return
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if (len(lengths) != 1 or self.headers.get('Transfer-Encoding') or
                    self.headers.get('Content-Type') != 'application/json'):
                raise ValueError('invalid_body')
            length = int(lengths[0])
            if not 2 <= length <= 32768:
                raise ValueError('invalid_body')
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('invalid_body')
            data = json.loads(raw)
            result = self.server.acquisition.command(data)
        except AcquisitionError as exc:
            self.reply(409, {'code': str(exc)})
            return
        except (LogPersistenceError, OSError):
            self.reply(409, {'code': 'record_persistence_failed'})
            return
        except (ValueError, TypeError, UnicodeError):
            self.reply(400, {'code': 'invalid_body'})
            return
        self.reply(200, {**result, 'experiment_key': self.server.experiment_key})

    def do_POST(self):
        if self.path == '/api/v1/experiments':
            self.acquisition_post()
            return
        # Mode selection shares the existing motor endpoint authentication.
        if self.path != '/api/v1/control':
            self.reject_method()
            return
        if not self.authenticated():
            self.reply(401, {'code': 'unauthorized'})
            return
        origins = self.headers.get_all('Origin', [])
        keys = self.headers.get_all('X-Control-Key', [])
        if (not self.local_control_host() or len(origins) != 1 or
                origins[0] != 'http://' + self.headers['Host'] or len(keys) != 1 or
                not hmac.compare_digest(keys[0].encode('utf-8'), self.server.control_key.encode('utf-8'))):
            self.reply(403, {'code': 'control_origin_rejected'})
            return
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if (len(lengths) != 1 or self.headers.get('Transfer-Encoding') or
                    self.headers.get('Content-Type') != 'application/json'):
                raise ValueError('invalid_body')
            length = int(lengths[0])
            if not 2 <= length <= 1024:
                raise ValueError('invalid_body')
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('invalid_body')
            data = json.loads(raw)
            result = self.server.driving_mode.command(data)
        except ControlError as exc:
            self.reply(409, {'code': str(exc)})
            return
        except (ValueError, TypeError, UnicodeError):
            self.reply(400, {'code': 'invalid_body'})
            return
        self.reply(200, result)

    def acquisition_get(self, path):
        if not self.local_control_host():
            self.reply(403, {'code': 'experiment_origin_rejected'})
            return
        if self.server.acquisition is None:
            self.reply(200, {'enabled': False})
            return
        try:
            export = re.fullmatch(r'/api/v1/experiments/([A-Za-z0-9_-]{1,100})/(csv|manifest)', path)
            if export:
                run_id, kind = export.groups()
                filename, body = self.server.acquisition.export(run_id, kind)
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8' if kind == 'csv' else 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
                return
            detail = re.fullmatch(r'/api/v1/experiments/([A-Za-z0-9_-]{1,100})', path)
            if path != '/api/v1/experiments' and not detail:
                raise AcquisitionError('not_found')
            result = self.server.acquisition.snapshot(detail.group(1) if detail else None)
        except (AcquisitionError, OSError, ValueError, KeyError):
            self.reply(404, {'code': 'experiment_not_found'})
            return
        self.reply(200, {**result, 'experiment_key': self.server.experiment_key})

    def do_GET(self):
        # Match the unnormalised request target, never join a client path.
        target = self.requestline.split()[1]
        if target in STATIC_FILES:
            filename, mime = STATIC_FILES[target]
            try:
                file = FRONTEND / filename
                if file.is_symlink() or file.resolve().parent != FRONTEND.resolve():
                    raise OSError('invalid static file')
                body = file.read_bytes()
            except OSError:
                self.reply(404, {'code': 'not_found'})
                return
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        if not self.authenticated():
            self.reply(401, {'code': 'unauthorized'})
            return
        path = urlsplit(self.path).path
        if path == '/api/v1/experiments' or path.startswith('/api/v1/experiments/'):
            self.acquisition_get(path)
            return
        if path == '/api/v1/line-sensors':
            reading = self.server.service.sensors.one('line_sensors')
            follower = self.server.driving_mode.follower
            order = follower.channel_permutation if follower is not None else DEFAULT_CHANNEL_PERMUTATION
            raw = reading.get('value')
            valid = isinstance(raw, (list, tuple)) and len(raw) == 4 and all(type(v) is int and v in (0, 1) for v in raw)
            self.reply(200, {**{key: reading.get(key) for key in ('status', 'age_seconds', 'monotonic', 'successes', 'error')},
                             'value': [raw[index] for index in order] if valid else None,
                             'raw_value': raw, 'channel_order': ['X' + str(index + 1) for index in order]})
            return
        if path == '/api/v1/control':
            if not self.local_control_host():
                self.reply(403, {'code': 'control_origin_rejected'})
                return
            state = self.server.driving_mode.snapshot()
            self.reply(200, {**state, 'control_key': self.server.control_key})
            return
        if path == '/api/v1/camera/frame':
            packet = self.server.service.sensors.frame_packet()
            if packet is None:
                self.reply(503, {'code': 'camera_unavailable'})
                return
            frame, frame_id = packet
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(frame)))
            self.send_header('X-Frame-Id', frame_id)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(frame)
            self.close_connection = True
            return
        prefix = '/api/v1/'
        if not path.startswith(prefix) or path[len(prefix):] not in ('health', 'system', 'devices', 'status'):
            self.reply(404, {'code': 'not_found'})
            return
        result = self.server.service.get(path[len(prefix):])
        if self.server.motor and path.endswith(('status', 'health')):
            health = result['health'] if path.endswith('status') else result
            health['read_only'] = False
            health['capabilities']['actuation'] = 'leased_wheel_test'
            if path.endswith('status'):
                result['control'] = self.server.motor.snapshot()
        self.reply(200, result)


def create_server(host='127.0.0.1', port=8765, collector=None, telemetry=None, sensors=None, motor=None, acquisition_dir=None):
    token = os.environ.get('DEVICE_CONSOLE_TOKEN') or None
    address = ipaddress.ip_address(host)
    if (motor is not None or acquisition_dir is not None) and not address.is_loopback:
        raise ValueError('control and station logging require loopback binding and SSH forwarding')
    if not address.is_loopback and not token:
        raise ValueError('non-loopback binding requires DEVICE_CONSOLE_TOKEN')
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    class AddressServer(BoundedHTTPServer):
        address_family = family

        def server_close(self):
            mode = getattr(self, 'driving_mode', None)
            if mode is not None:
                mode.close()
            super().server_close()
    server = AddressServer((host, port), Handler)
    server.token = token
    server.motor = motor
    server.control_key = secrets.token_urlsafe(32)
    server.experiment_key = secrets.token_urlsafe(32)
    server.acquisition = None
    server.service = MonitorService(collector or LinuxCollector(), telemetry or Telemetry(), sensors)
    configured_line = bool(getattr(server.service.sensors, 'directory', None)) and callable(getattr(server.service.sensors, 'one', None))
    line_source = (lambda: server.service.sensors.one('line_sensors')) if configured_line else None
    server.driving_mode = DrivingMode(motor, line_source=line_source)
    if acquisition_dir is not None:
        def observed_status(timeout):
            state = server.service.get('status')
            if server.motor:
                state['control'] = server.motor.snapshot()
            else:
                state['control'] = {'enabled': False, 'armed': False, 'state': 'disabled'}
            return state
        try:
            server.acquisition = AcquisitionManager(acquisition_dir, observed_status,
                                                    sensor_only=server.motor is None)
        except Exception:
            server.server_close()
            raise
    return server


def main(argv=None):
    import argparse
    import signal
    from service import PassiveSerial

    cli = argparse.ArgumentParser(description='Robot monitor; wheel testing requires --motor-port')
    cli.add_argument('--host', default='127.0.0.1', help='numeric IP; non-loopback requires environment token')
    cli.add_argument('--port', type=int, default=8765)
    cli.add_argument('--serial-port', default=None, help='explicitly opt into passive 115200 baud reads')
    cli.add_argument('--sensor-state-dir', default=None, help='fixed snapshot directory from hardware_worker.py')
    cli.add_argument('--motor-port', default=None, help='explicitly enable leased wheel tests on this serial port')
    cli.add_argument('--motor-mode', choices=('speed', 'pwm'), default='speed', help='speed matches the previously verified course motor test')
    cli.add_argument('--experiment-dir', default=None, help='fixed persistent station-log directory; optional and independent of motor controls')
    args = cli.parse_args(argv)
    telemetry = Telemetry()
    if args.motor_port and args.serial_port:
        cli.error('passive serial and motor control cannot run together')
    motor = None
    try:
        if args.motor_port:
            if not ipaddress.ip_address(args.host).is_loopback:
                raise ValueError('wheel testing requires loopback binding')
            motor = MotorControl(SerialPWM(args.motor_port), mode=args.motor_mode, startup_boost=True)
        server = create_server(args.host, args.port, telemetry=telemetry,
                               sensors=SensorStore(args.sensor_state_dir), motor=motor,
                               acquisition_dir=args.experiment_dir)
    except (ValueError, OSError, OverflowError) as exc:
        if motor:
            motor.close()
        cli.error(str(exc))
    stop = threading.Event()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda *_: stop.set())
    reader = PassiveSerial(args.serial_port, telemetry) if args.serial_port else None
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, name='http-server')
    try:
        if motor:
            motor.start()
        thread.start()
        if reader:
            reader.start()
        print(json.dumps({'event': 'listening', 'host': args.host, 'port': server.server_address[1],
                          'serial_enabled': reader is not None, 'motor_enabled': motor is not None,
                          'authentication': bool(server.token)}), flush=True)
        stop.wait()
    finally:
        server.driving_mode.close()  # Revoke/join autonomous execution before the motor owner closes.
        if motor:
            motor.close()
        server.shutdown()
        server.server_close()
        thread.join()
        if reader:
            reader.stop()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
