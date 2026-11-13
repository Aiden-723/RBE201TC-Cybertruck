import http.client
import json
import os
import select
import subprocess
import sys
import unittest


class CLITests(unittest.TestCase):
    def test_cli_http_default_read_only_and_sigterm(self):
        import server
        self.assertTrue(hasattr(server, 'main'), 'CLI entry point missing')
        env = {k: v for k, v in os.environ.items() if k != 'DEVICE_CONSOLE_TOKEN'}
        process = subprocess.Popen([sys.executable, 'server.py', '--port', '0'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0], 'CLI never became ready')
            ready = json.loads(process.stdout.readline())
            self.assertEqual(ready['host'], '127.0.0.1')
            self.assertFalse(ready['serial_enabled'])
            connection = http.client.HTTPConnection(ready['host'], ready['port'], timeout=3)
            try:
                connection.request('GET', '/api/v1/status')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                data = json.loads(response.read())['data']
                self.assertEqual(data['telemetry']['status'], 'disabled')
                self.assertEqual(data['health']['scope'], 'backend_process_only')
                # The application deliberately observes Linux pseudo-files only.
                # A macOS offline test must not require invented Linux metrics.
                uptime = data['system']['uptime_seconds']
                self.assertEqual(uptime['status'], 'ok' if sys.platform == 'linux' else 'unavailable')
                if sys.platform != 'linux':
                    self.assertIsNone(uptime['value'])
            finally:
                connection.close()
            process.terminate()
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stderr, '')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_cli_refuses_unsafe_bind(self):
        import server
        self.assertTrue(hasattr(server, 'main'), 'CLI entry point missing')
        env = {k: v for k, v in os.environ.items() if k != 'DEVICE_CONSOLE_TOKEN'}
        run = subprocess.run([sys.executable, 'server.py', '--host', '0.0.0.0', '--port', '0'],
                             capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(run.returncode, 2)
        self.assertIn('DEVICE_CONSOLE_TOKEN', run.stderr)
