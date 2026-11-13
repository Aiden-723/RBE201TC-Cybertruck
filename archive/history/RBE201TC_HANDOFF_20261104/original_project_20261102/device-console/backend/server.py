"""Small read-only JSON HTTP server; no filesystem or command routes."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import ipaddress
import json
import os
import socket
import threading
from urllib.parse import urlsplit
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent.parent / 'frontend'
STATIC_FILES = {'/': ('index.html', 'text/html'),
                '/index.html': ('index.html', 'text/html'),
                '/styles.css': ('styles.css', 'text/css'),
                '/app.js': ('app.js', 'text/javascript')}

from collectors import LinuxCollector
from service import MonitorService, Telemetry


class BoundedHTTPServer(HTTPServer):
    # One active request, eight queued TCP connections, one deadline timer.
    request_queue_size = 8
    request_deadline = 3.0

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
            pass  # Disconnected/malformed clients cannot emit traceback or secrets.
        finally:
            timer.cancel()
            timer.join()


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

    do_POST = do_PUT = do_PATCH = do_DELETE = reject_method
    do_OPTIONS = do_TRACE = do_CONNECT = do_HEAD = reject_method

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
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        if self.server.token:
            supplied = self.headers.get_all('Authorization', [])
            expected = ('Bearer ' + self.server.token).encode('utf-8')
            if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode('utf-8'), expected):
                self.reply(401, {'code': 'unauthorized'})
                return
        path = urlsplit(self.path).path
        prefix = '/api/v1/'
        if not path.startswith(prefix) or path[len(prefix):] not in ('health', 'system', 'devices', 'status'):
            self.reply(404, {'code': 'not_found'})
            return
        self.reply(200, self.server.service.get(path[len(prefix):]))


def create_server(host='127.0.0.1', port=8765, collector=None, telemetry=None):
    token = os.environ.get('DEVICE_CONSOLE_TOKEN') or None
    address = ipaddress.ip_address(host)
    if not address.is_loopback and not token:
        raise ValueError('non-loopback binding requires DEVICE_CONSOLE_TOKEN')
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    class AddressServer(BoundedHTTPServer):
        address_family = family
    server = AddressServer((host, port), Handler)
    server.token = token
    server.service = MonitorService(collector or LinuxCollector(), telemetry or Telemetry())
    return server


def main(argv=None):
    import argparse
    import signal
    from service import PassiveSerial

    cli = argparse.ArgumentParser(description='Read-only robot device monitor (no actuator commands)')
    cli.add_argument('--host', default='127.0.0.1', help='numeric IP; non-loopback requires environment token')
    cli.add_argument('--port', type=int, default=8765)
    cli.add_argument('--serial-port', default=None, help='explicitly opt into passive 115200 baud reads')
    args = cli.parse_args(argv)
    telemetry = Telemetry()
    try:
        server = create_server(args.host, args.port, telemetry=telemetry)
    except (ValueError, OSError, OverflowError) as exc:
        cli.error(str(exc))
    stop = threading.Event()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda *_: stop.set())
    reader = PassiveSerial(args.serial_port, telemetry) if args.serial_port else None
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, name='http-server')
    try:
        thread.start()
        if reader:
            reader.start()
        print(json.dumps({'event': 'listening', 'host': args.host, 'port': server.server_address[1],
                          'serial_enabled': reader is not None, 'authentication': bool(server.token)}), flush=True)
        stop.wait()
    finally:
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
