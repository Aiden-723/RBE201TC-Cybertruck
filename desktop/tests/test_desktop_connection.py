import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopConnectionTests(unittest.TestCase):
    def test_runtime_is_outside_documents_and_has_no_login_item(self):
        installer = load('install_desktop_runtime')
        runtime, logs = installer.runtime_paths(Path('/Users/test-user'))
        self.assertEqual(str(runtime), '/Users/test-user/Library/Application Support/RBE201TC/connection')
        self.assertEqual(str(logs), '/Users/test-user/Library/Logs/RBE201TC')
        source = (ROOT / 'install_desktop_runtime.py').read_text()
        self.assertNotIn('launchctl', source)
        self.assertNotIn('RunAtLoad', source)
        self.assertNotIn('plistlib', source)

    def test_config_keeps_strict_pins_and_quotes_runtime_path(self):
        installer = load('install_desktop_runtime')
        runtime, _ = installer.runtime_paths(Path('/Users/test-user'))
        config = installer.render_ssh_config((ROOT / 'autoboat_ssh_config.example').read_text(), runtime)
        self.assertIn('UserKnownHostsFile "' + str(runtime / 'known_hosts') + '"', config)
        self.assertIn('StrictHostKeyChecking yes', config)
        self.assertIn('GlobalKnownHostsFile /dev/null', config)
        self.assertNotIn('Documents', config)
        with self.assertRaises(ValueError):
            installer.render_ssh_config('Host x\n', runtime)

    def test_clock_preflight_and_forward_use_same_approved_host_without_masters(self):
        connection = load('maintain_wifi_connection')
        for host in connection.HOSTS:
            preflight, forward = connection.connection_commands(host, 1794316338)
            self.assertEqual(preflight[-2], host)
            self.assertEqual(forward[-1], host)
            self.assertIn('ClearAllForwardings=yes', preflight)
            self.assertNotIn('ClearAllForwardings=yes', forward)
            for command in (preflight, forward):
                self.assertIn('ControlMaster=no', command)
                self.assertIn('ControlPath=none', command)
                self.assertNotIn('-f', command)
        with self.assertRaises(ValueError):
            connection.connection_commands('unapproved-host', 1794316338)

    def test_supervisor_import_does_not_start_anything(self):
        module = load('supervise_wifi_connection')
        self.assertTrue(callable(module.main))
        self.assertNotIn('launchctl', (ROOT / 'supervise_wifi_connection.py').read_text())

    def test_atomic_runtime_install_is_idempotent(self):
        installer = load('install_desktop_runtime')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'runtime.py'
            self.assertTrue(installer.install_file(target, b'print(1)\n'))
            self.assertFalse(installer.install_file(target, b'print(1)\n'))
            self.assertTrue(installer.install_file(target, b'print(2)\n'))
            self.assertEqual(target.read_bytes(), b'print(2)\n')


class MotorConnectPreflightTests(unittest.TestCase):
    def setUp(self):
        self.module = load('prepare_console_before_connect')
        self.control = {'enabled': True, 'state': 'stopped', 'armed': False,
                        'fault': None, 'outputs': [0, 0, 0, 0], 'drive_commands_sent': 0,
                        'serial_diagnostics': {'configuration': '', 'battery': ''}}
        self.acquisition = {'active_run_id': None, 'busy': False, 'committing': False, 'closing': False}
        self.claims = self.recoveries = 0
        self.granted = True

    def execute(self, fail=False):
        def read(endpoint):
            return self.control if endpoint == 'control' else self.acquisition
        def claim():
            self.claims += 1
            return self.granted
        def recover():
            self.recoveries += 1
            if fail:
                raise OSError('mock failure; no hardware')
            self.control['serial_diagnostics'] = {'configuration': 'Motor_Version:1.7.3',
                                                  'battery': '$Battery:12.2V#'}
        return self.module.prepare_console(1794316338, lambda t: {'clock': 'already_current'},
                                           read, recover, claim)

    def test_valid_application_is_never_reset(self):
        self.control['serial_diagnostics'] = {'configuration': 'Motor_Version:1.7.3', 'battery': '$Battery:12.2V#'}
        self.assertEqual(self.execute()['motor'], 'application_replies_already_present')
        self.assertEqual((self.claims, self.recoveries), (0, 0))

    def test_idle_empty_replies_allow_one_verified_application_start(self):
        self.assertEqual(self.execute()['motor'], 'original_application_recovered')
        self.assertEqual((self.claims, self.recoveries), (1, 1))

    def test_previous_boot_attempt_prevents_reset_loops(self):
        self.granted = False
        self.assertEqual(self.execute()['motor'], 'attention_already_attempted_this_robot_boot')
        self.assertEqual(self.recoveries, 0)

    def test_every_active_or_nonzero_state_blocks_reset(self):
        for key, value in (('armed', True), ('outputs', [1, 0, 0, 0]),
                           ('drive_commands_sent', 1), ('state', 'driving'), ('fault', 'fault')):
            original = self.control[key]
            self.control[key] = value
            self.assertEqual(self.execute()['motor'], 'attention_not_idle_no_reset')
            self.control[key] = original
        for key in ('busy', 'committing', 'closing'):
            self.acquisition[key] = True
            self.assertEqual(self.execute()['motor'], 'attention_not_idle_no_reset')
            self.acquisition[key] = False
        self.assertEqual((self.claims, self.recoveries), (0, 0))

    def test_unknown_nonempty_reply_is_not_overridden(self):
        self.control['serial_diagnostics']['battery'] = 'unknown reply'
        self.assertEqual(self.execute()['motor'], 'attention_nonempty_unrecognized_reply')
        self.assertEqual((self.claims, self.recoveries), (0, 0))

    def test_recovery_failure_is_reported_without_hiding_web_service(self):
        result = self.execute(fail=True)
        self.assertEqual(result['motor'], 'attention_recovery_failed')
        self.assertIn('OSError', result['error'])

    def test_disabled_motor_never_enters_recovery(self):
        self.control['enabled'] = False
        self.assertEqual(self.execute()['motor'], 'disabled')
        self.assertEqual((self.claims, self.recoveries), (0, 0))


if __name__ == '__main__':
    unittest.main()
