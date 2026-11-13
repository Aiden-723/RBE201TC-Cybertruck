"""SOFTWARE_FIXTURE_NOT_COURSE_DATA: no SSH, UART, GPIO or live HTTP."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
from contextlib import redirect_stdout, redirect_stderr

PROJECT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('deploy_web_acquisition', PROJECT / 'tools/deploy_web_acquisition.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


# Archived deployment invariants are tested against the exact historical source,
# never by weakening production hashes to admit the current motor/bench release.
_LEGACY_TEMP = None
LEGACY_PROJECT = None

def setUpModule():
    global _LEGACY_TEMP, LEGACY_PROJECT
    _LEGACY_TEMP = tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_LEGACY_SOURCE_')
    LEGACY_PROJECT = Path(_LEGACY_TEMP.name).resolve()
    with zipfile.ZipFile(PROJECT/'tools/tests/fixtures/legacy_audited_source.zip') as z:
        for name, digest in {**deploy.PROTECTED, **deploy.AUDITED_NEW}.items():
            data=z.read(name)
            if deploy.digest(data)!=digest:raise AssertionError('Legacy fixture changed: '+name)
            path=LEGACY_PROJECT/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

def tearDownModule():
    _LEGACY_TEMP.cleanup()


def snapshot():
    return {'fixture': 'SOFTWARE_FIXTURE_NOT_COURSE_DATA', 'pid': 117483, 'start_identity': '12345',
            'model_is_rdk_x5': True, 'machine': 'aarch64', 'root': deploy.REMOTE_ROOT,
            'args': list(deploy.OLD_ARGS), 'serial_is_character_device': True,
            'serial_owners': [117483], 'hashes': {**deploy.PROTECTED, **deploy.OLD},
            'clock_valid': True, 'fresh_clock': True, 'auth_enabled': False, 'pidfd_available': True,
            'control': {'enabled': True, 'armed': False, 'state': 'stopped', 'fault': None,
                        'outputs': [0, 0, 0, 0], 'command_mode': 'speed',
                        'max_hold_seconds': None, 'lease_ms': 450, 'drive_commands_sent': 42}}


class FakeOps:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.records = []
        self.original_alive = True
        self.candidate_alive = False
        self.content = 'SOFTWARE_FIXTURE_ORIGINAL'
        self.inspections = 0

    def inspect(self):
        self.calls.append('inspect'); self.inspections += 1
        info = snapshot()
        if self.inspections == 2 and self.failure == 'operator':
            info['control']['drive_commands_sent'] += 1
        return info

    def prepare(self, bundle, info):
        self.calls.append('backup_verified'); return '/SOFTWARE_FIXTURE_ONLY/backup'

    def stop_old(self, info):
        self.calls.append('stop_old')
        if self.failure == 'stop_old':
            raise deploy.DeployError('SOFTWARE_FIXTURE_stop_not_confirmed')
        self.original_alive = False

    def install(self, bundle):
        assert not self.original_alive
        self.calls.append('install'); self.content = 'SOFTWARE_FIXTURE_CANDIDATE'
        if self.failure == 'install':
            raise OSError('SOFTWARE_FIXTURE_install_failure')

    def start_new(self, info):
        assert not self.original_alive and not self.candidate_alive
        self.calls.append('start_new'); self.candidate_alive = True; return 222

    def verify(self, pid, acquisition):
        self.calls.append('verify_new' if acquisition else 'verify_old')
        if acquisition and self.failure in ('verify', 'unsafe_rollback'):
            raise deploy.DeployError('SOFTWARE_FIXTURE_validation_failure')

    def stop_candidate_if_safe(self):
        self.calls.append('check_candidate_before_stop')
        if self.failure == 'unsafe_rollback':
            raise deploy.DeployError('SOFTWARE_FIXTURE_operator_active')
        self.candidate_alive = False

    def restore(self):
        assert not self.candidate_alive
        self.calls.append('restore'); self.content = 'SOFTWARE_FIXTURE_ORIGINAL'

    def start_old(self, info):
        assert not self.candidate_alive and not self.original_alive
        self.calls.append('start_old'); self.original_alive = True; return 333

    def record(self, record):
        record = {**record, 'fixture': 'SOFTWARE_FIXTURE_NOT_COURSE_DATA'}
        self.records.append(record); return record


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = deploy.build_bundle(LEGACY_PROJECT)

    def test_current_release_still_refused_by_frozen_historical_deployer(self):
        with self.assertRaisesRegex(deploy.DeployError, 'protected_source_changed'):
            deploy.build_bundle(PROJECT)

    def test_payload_is_exactly_six_audited_files(self):
        data = deploy.decode_bundle(self.bundle)
        self.assertEqual(set(data), set(deploy.AUDITED_NEW))
        self.assertTrue(set(data).isdisjoint(deploy.PROTECTED))
        self.assertEqual({p: deploy.digest(b) for p, b in data.items()}, deploy.AUDITED_NEW)

    def test_payload_unknown_filename_or_traversal_is_rejected(self):
        for name in ('../server.py', '/tmp/source.py', 'backend/motor_control.py'):
            with self.subTest(name=name):
                bundle = copy.deepcopy(self.bundle); bundle['files'][name] = ''
                with self.assertRaises(deploy.DeployError): deploy.decode_bundle(bundle)

    def test_payload_modified_bytes_or_bad_base64_is_rejected(self):
        for value in ('!', base64.b64encode(b'SOFTWARE_FIXTURE_UNAUDITED').decode()):
            bundle = copy.deepcopy(self.bundle); bundle['files']['backend/server.py'] = value
            with self.assertRaises(deploy.DeployError): deploy.decode_bundle(bundle)

    def test_clean_disarmed_snapshot_passes_without_mutation(self):
        info = snapshot(); before = copy.deepcopy(info)
        deploy.validate_snapshot(info)
        self.assertEqual(info, before)

    def test_motion_fault_session_disabled_or_hold_timer_are_rejected(self):
        for changes in ({'outputs': [200] * 4}, {'armed': True}, {'fault': 'serial_fault'},
                        {'state': 'armed'}, {'enabled': False}, {'max_hold_seconds': 3},
                        {'command_mode': 'pwm'}, {'lease_ms': 300}, {'outputs': [False] * 4},
                        {'outputs': [0.0] * 4}, {'drive_commands_sent': True}):
            with self.subTest(changes=changes):
                info = snapshot(); info['control'].update(changes)
                with self.assertRaises(deploy.DeployError): deploy.validate_snapshot(info)

    def test_malformed_or_missing_control_never_becomes_stopped(self):
        for value in (None, [], 'stopped', {}):
            info = snapshot(); info['control'] = value
            with self.assertRaises(deploy.DeployError): deploy.validate_snapshot(info)

    def test_wrong_identity_hash_clock_or_duplicate_owner_rejected(self):
        cases = [('model_is_rdk_x5', False), ('machine', 'arm64'), ('clock_valid', False),
                 ('fresh_clock', False), ('serial_is_character_device', False),
                 ('serial_owners', [117483, 2]), ('root', '/tmp/SOFTWARE_FIXTURE'),
                 ('hashes', {}), ('pid', True), ('pid', 1), ('start_identity', ''), ('pidfd_available', False)]
        for key, value in cases:
            with self.subTest(key=key):
                info = snapshot(); info[key] = value
                with self.assertRaises(deploy.DeployError): deploy.validate_snapshot(info)

    def test_changed_original_arguments_are_not_silently_reconstructed(self):
        info = snapshot(); info['args'] += ['--serial-port', '/dev/SOFTWARE_FIXTURE']
        with self.assertRaises(deploy.DeployError): deploy.validate_snapshot(info)

    def test_second_snapshot_detects_operator_or_pid_change(self):
        for change in ('counter', 'pid', 'identity'):
            first = snapshot(); second = copy.deepcopy(first)
            if change == 'counter': second['control']['drive_commands_sent'] += 1
            if change == 'pid': second['pid'] += 1; second['serial_owners'] = [second['pid']]
            if change == 'identity': second['start_identity'] = '67890'
            with self.subTest(change=change), self.assertRaises(deploy.DeployError):
                deploy.validate_snapshot(second, first)

    def test_client_clock_requires_explicit_timezone_and_five_second_window(self):
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        for delta in (-5, 0, 5): deploy.validate_client_clock((now + timedelta(seconds=delta)).isoformat(), now)
        for stamp in (None, 'bad', now.replace(tzinfo=None).isoformat(), (now + timedelta(seconds=6)).isoformat()):
            with self.subTest(stamp=stamp), self.assertRaises(deploy.DeployError): deploy.validate_client_clock(stamp, now)

    def test_transaction_orders_backup_two_checks_exit_install_and_start(self):
        ops = FakeOps(); result = deploy.run_transaction(ops, self.bundle)
        self.assertEqual(ops.calls, ['inspect', 'backup_verified', 'inspect', 'stop_old', 'install', 'start_new', 'verify_new'])
        self.assertEqual(result['status'], 'deployed'); self.assertTrue(result['fixture'])
        self.assertEqual(result['nonzero_motor_commands_sent_by_tool'], 0)

    def test_operator_activity_after_backup_never_stops_or_installs(self):
        ops = FakeOps('operator')
        with self.assertRaises(deploy.DeployError): deploy.run_transaction(ops, self.bundle)
        self.assertNotIn('stop_old', ops.calls); self.assertNotIn('install', ops.calls)
        self.assertTrue(ops.original_alive)

    def test_old_stop_uncertainty_never_installs_or_starts_second_owner(self):
        ops = FakeOps('stop_old')
        with self.assertRaisesRegex(deploy.DeployError, 'old_stop_not_confirmed'):
            deploy.run_transaction(ops, self.bundle)
        self.assertNotIn('install', ops.calls); self.assertNotIn('start_new', ops.calls)
        self.assertNotIn('start_old', ops.calls)
        self.assertEqual(ops.records[-1]['status'], 'old_stop_not_confirmed_no_install_or_second_server')

    def test_partial_install_failure_restores_original_without_duplicate_owner(self):
        ops = FakeOps('install')
        with self.assertRaisesRegex(deploy.DeployError, 'original_restored'):
            deploy.run_transaction(ops, self.bundle)
        self.assertEqual(ops.content, 'SOFTWARE_FIXTURE_ORIGINAL')
        self.assertTrue(ops.original_alive); self.assertFalse(ops.candidate_alive)
        self.assertEqual(ops.records[-1]['status'], 'failed_rolled_back')

    def test_candidate_validation_failure_stops_candidate_before_restoring(self):
        ops = FakeOps('verify')
        with self.assertRaises(deploy.DeployError): deploy.run_transaction(ops, self.bundle)
        self.assertLess(ops.calls.index('check_candidate_before_stop'), ops.calls.index('restore'))
        self.assertLess(ops.calls.index('restore'), ops.calls.index('start_old'))
        self.assertTrue(ops.original_alive); self.assertFalse(ops.candidate_alive)

    def test_operator_active_candidate_refuses_unsafe_automatic_rollback(self):
        ops = FakeOps('unsafe_rollback')
        with self.assertRaisesRegex(deploy.DeployError, 'do_not_retry_apply'):
            deploy.run_transaction(ops, self.bundle)
        self.assertNotIn('restore', ops.calls); self.assertNotIn('start_old', ops.calls)
        self.assertTrue(ops.candidate_alive)
        self.assertEqual(ops.records[-1]['status'], 'indeterminate_requires_inspection')

    def test_offline_prepare_archive_has_no_protected_replacements(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            target = Path(folder) / 'candidate.zip'
            with patch.object(deploy, 'execute_remote', side_effect=AssertionError('network forbidden')), redirect_stdout(io.StringIO()):
                self.assertEqual(deploy.main(['--project', str(LEGACY_PROJECT), '--prepare', str(target)]), 0)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(set(archive.namelist()), set(deploy.AUDITED_NEW) | {'deployment_bundle.json', 'README.txt'})
                for name, sha in deploy.AUDITED_NEW.items(): self.assertEqual(deploy.digest(archive.read(name)), sha)
            before = target.read_bytes()
            with redirect_stderr(io.StringIO()):
                self.assertEqual(deploy.main(['--prepare', str(target)]), 2)
            self.assertEqual(target.read_bytes(), before)

    def test_missing_master_refuses_before_any_ssh_or_other_network(self):
        runner = Mock(side_effect=AssertionError('must not call SSH'))
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            with self.assertRaisesRegex(deploy.DeployError, 'existing_authenticated_ssh_master'):
                deploy.execute_remote('apply', self.bundle, Path(folder) / 'absent', Path(folder) / 'absent_hosts', runner)
        runner.assert_not_called()

    def test_regular_file_is_not_accepted_as_control_socket(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            file = Path(folder) / 'not-a-socket'; file.write_text('SOFTWARE_FIXTURE')
            with self.assertRaises(deploy.DeployError): deploy.execute_remote('apply', self.bundle, file, file, Mock())

    def local_socket(self, folder):
        file = Path(folder) / 'cm'; connection = socket.socket(socket.AF_UNIX)
        connection.bind(str(file)); self.addCleanup(connection.close)
        known = Path(folder) / 'known_hosts'; known.write_text('SOFTWARE_FIXTURE_NOT_REAL_HOST_KEY')
        return file, known

    def test_dead_master_is_not_reconnected_or_replayed(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            sock, known = self.local_socket(folder); runner = Mock(return_value=Mock(returncode=255))
            with self.assertRaisesRegex(deploy.DeployError, 'master_not_live'):
                deploy.execute_remote('apply', self.bundle, sock, known, runner)
            self.assertEqual(runner.call_count, 1)

    def test_remote_timeout_is_indeterminate_and_never_replayed(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            sock, known = self.local_socket(folder)
            runner = Mock(side_effect=[Mock(returncode=0), subprocess.TimeoutExpired('SOFTWARE_FIXTURE', 120)])
            with self.assertRaisesRegex(deploy.DeployError, 'indeterminate_do_not_retry'):
                deploy.execute_remote('apply', self.bundle, sock, known, runner)
            self.assertEqual(runner.call_count, 2)

    def test_mock_remote_uses_pinned_host_and_strict_noninteractive_options(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            sock, known = self.local_socket(folder)
            runner = Mock(side_effect=[Mock(returncode=0), Mock(returncode=0, stdout='{"status":"SOFTWARE_FIXTURE_PREFLIGHT"}')])
            result = deploy.execute_remote('preflight', self.bundle, sock, known, runner)
            self.assertEqual(result['status'], 'SOFTWARE_FIXTURE_PREFLIGHT')
            args = runner.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', args); self.assertIn('BatchMode=yes', args)
            self.assertIn('ControlMaster=no', args); self.assertIn('ProxyCommand=false', args)
            self.assertIn('ControlPersist=no', args)
            self.assertIn(deploy.TARGET, args); self.assertEqual(args[-3:], ['python3', '-', '--remote'])
            program = runner.call_args.kwargs['input']; self.assertIn('_REMOTE_REQUEST = ', program)
            self.assertIn('client_time', program)

    def test_missing_explicit_remote_options_exit_without_connection(self):
        with redirect_stderr(io.StringIO()), patch.object(deploy, 'execute_remote') as remote:
            self.assertEqual(deploy.main(['--preflight']), 2)
            self.assertEqual(deploy.main(['--apply']), 2)
            remote.assert_not_called()

    def test_preflight_public_result_does_not_expose_private_environment(self):
        fake = Mock(); fake.inspect.return_value = snapshot()
        request = {'action': 'preflight', 'client_time': datetime.now(timezone.utc).isoformat()}
        with patch.object(deploy, 'RemoteOps', return_value=fake): result = deploy.remote_request(request)
        self.assertEqual(result['status'], 'preflight_passed_no_changes')
        self.assertNotIn('control', result); self.assertNotIn('environment', result); fake.prepare.assert_not_called()

    def test_atomic_replace_only_accepts_whitelisted_candidate_targets(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            ops = deploy.RemoteOps.__new__(deploy.RemoteOps); ops.root = Path(folder).resolve()
            (ops.root / 'frontend').mkdir(); (ops.root / 'backend').mkdir()
            target = ops.path('frontend/index.html'); target.write_bytes(b'SOFTWARE_FIXTURE_ORIGINAL')
            ops.atomic(target, b'SOFTWARE_FIXTURE_CANDIDATE'); self.assertEqual(target.read_bytes(), b'SOFTWARE_FIXTURE_CANDIDATE')
            protected = ops.path('frontend/control.js'); protected.write_bytes(b'SOFTWARE_FIXTURE_PROTECTED')
            with self.assertRaises(deploy.DeployError): ops.atomic(protected, b'BAD')
            self.assertEqual(protected.read_bytes(), b'SOFTWARE_FIXTURE_PROTECTED')

    def test_symlink_parent_or_target_refuses_before_replace(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            root = Path(folder).resolve(); (root / 'actual').mkdir(); (root / 'frontend').symlink_to(root / 'actual', target_is_directory=True)
            ops = deploy.RemoteOps.__new__(deploy.RemoteOps); ops.root = root
            with self.assertRaises(deploy.DeployError): ops.path('frontend/index.html')

    def test_restore_preserves_inert_additions_and_never_deletes_data(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            ops = deploy.RemoteOps.__new__(deploy.RemoteOps); ops.root = Path(folder).resolve()
            (ops.root / 'frontend').mkdir(); (ops.root / 'backend').mkdir()
            original = ops.path('frontend/index.html'); original.write_bytes(b'SOFTWARE_FIXTURE_NEW')
            addition = ops.path('frontend/acquisition.js'); addition.write_bytes(b'SOFTWARE_FIXTURE_ADDITION')
            raw = ops.root / 'do_not_delete.csv'; raw.write_text('SOFTWARE_FIXTURE_KEEP')
            ops.originals = {'frontend/index.html': b'SOFTWARE_FIXTURE_OLD', 'frontend/acquisition.js': None}
            ops.restore()
            self.assertEqual(original.read_bytes(), b'SOFTWARE_FIXTURE_OLD'); self.assertTrue(addition.exists())
            self.assertEqual(raw.read_text(), 'SOFTWARE_FIXTURE_KEEP')

    def test_reused_pid_is_never_signalled(self):
        ops = deploy.RemoteOps.__new__(deploy.RemoteOps)
        with patch.object(deploy.os, 'pidfd_open', return_value=987654, create=True), patch.object(deploy.signal, 'pidfd_send_signal', create=True) as send, patch.object(deploy.os, 'close'), patch.object(ops, 'proc', return_value={'start_identity': 'changed', 'cwd': deploy.REMOTE_ROOT}), patch.object(deploy.os, 'kill') as kill:
            with self.assertRaises(deploy.DeployError): ops.terminate_exact(222, 'original')
            kill.assert_not_called(); send.assert_not_called()

    def test_candidate_pid_fingerprint_mismatch_never_signalled(self):
        ops = deploy.RemoteOps.__new__(deploy.RemoteOps); ops.new_pid = 222; ops.launched = {222: 'original'}; ops.private = {'token': ''}
        ops.children = {222: Mock(poll=Mock(return_value=None))}
        state = {'start_identity': 'reused', 'cwd': deploy.REMOTE_ROOT}
        with patch.object(ops, 'proc', return_value=state), patch.object(ops, 'get', return_value={'control': {'armed': False, 'outputs': [0]*4, 'drive_commands_sent': 0}}), patch.object(ops, 'terminate_exact') as terminate:
            with self.assertRaisesRegex(deploy.DeployError, 'pid_changed'): ops.stop_candidate_if_safe()
            terminate.assert_not_called()


class DeploymentAdapterTests(unittest.TestCase):
    """Adapter-level probes remain mocked: never connect or send OS signals."""
    def ops(self):
        ops = deploy.RemoteOps.__new__(deploy.RemoteOps)
        ops.root = LEGACY_PROJECT
        ops.private = {'token': 'SOFTWARE_FIXTURE_TOKEN', 'exe': sys.executable, 'environment': {}}
        ops.new_pid = None; ops.launched = {}; ops.children = {}
        ops.run_dir = Path('/SOFTWARE_FIXTURE_ONLY_NOT_ACCESSED')
        return ops

    def status(self):
        control = snapshot()['control']; control['drive_commands_sent'] = 0
        return {'control': control, 'host': {'is_rdk': True, 'clock_valid': True,
                'model': 'D-Robotics RDK X5 V1.0', 'device_time': datetime.now(timezone.utc).isoformat()}}

    def test_redirect_is_refused_before_second_request_or_bearer_forwarding(self):
        import urllib.request
        from email.message import Message
        parent = Mock(); attempts = []
        def builder(*handlers):
            redirect = next(h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
            redirect.parent = parent
            opener = Mock()
            def open_request(request, timeout):
                attempts.append(request)
                headers = Message(); headers['Location'] = 'https://SOFTWARE-FIXTURE.invalid/leak'
                return redirect.http_error_302(request, Mock(), 302, 'Found', headers)
            opener.open.side_effect = open_request
            return opener
        with patch('urllib.request.build_opener', side_effect=builder):
            with self.assertRaisesRegex(deploy.DeployError, 'redirect_refused'):
                deploy.RemoteOps.get('/api/v1/status', 'SOFTWARE_FIXTURE_TOKEN')
        self.assertEqual(len(attempts), 1); parent.open.assert_not_called()
        self.assertEqual(attempts[0].full_url, 'http://127.0.0.1:8765/api/v1/status')

    def response(self, packet, *, status=200, url='http://127.0.0.1:8765/api/v1/status', mime='application/json'):
        from email.message import Message
        headers = Message(); headers['Content-Type'] = mime
        response = Mock(status=status, headers=headers)
        response.geturl.return_value = url
        response.read.return_value = json.dumps(packet).encode()
        context = Mock(); context.__enter__ = Mock(return_value=response); context.__exit__ = Mock(return_value=False)
        opener = Mock(); opener.open.return_value = context
        return opener

    def packet(self):
        return {'schema_version': '1.0', 'generated_at': datetime.now(timezone.utc).isoformat(), 'data': self.status()}

    def test_http_success_requires_exact_url_mime_and_envelope(self):
        packet = self.packet()
        with patch('urllib.request.build_opener', return_value=self.response(packet)):
            self.assertEqual(deploy.RemoteOps.get('/api/v1/status', ''), packet['data'])
        for options in ({'status': 302}, {'url': 'https://SOFTWARE-FIXTURE.invalid/'}, {'mime': 'text/html'}):
            with self.subTest(options=options), patch('urllib.request.build_opener', return_value=self.response(packet, **options)):
                with self.assertRaises(deploy.DeployError): deploy.RemoteOps.get('/api/v1/status', '')

    def test_http_missing_or_wrong_schema_and_stale_envelope_are_refused(self):
        for change in ({'schema_version': None}, {'data': []}, {'generated_at': None}, {'generated_at': '2000-01-01T00:00:00+00:00'}):
            packet = self.packet(); packet.update(change)
            with self.subTest(change=change), patch('urllib.request.build_opener', return_value=self.response(packet)):
                with self.assertRaises(deploy.DeployError): deploy.RemoteOps.get('/api/v1/status', '')

    def test_unknown_http_path_is_refused_before_building_opener(self):
        with patch('urllib.request.build_opener') as builder:
            with self.assertRaises(deploy.DeployError): deploy.RemoteOps.get('//SOFTWARE-FIXTURE.invalid/', '')
            builder.assert_not_called()

    def test_adapter_verify_accepts_strict_contract_and_known_source_bytes(self):
        ops = self.ops(); ops.launched = {222: 'stamp'}
        with patch.object(ops, 'proc', return_value={'start_identity': 'stamp', 'cwd': deploy.REMOTE_ROOT}), patch.object(ops, 'owners', return_value=[222]), patch.object(ops, 'get', side_effect=[self.status(), {'enabled': True}]):
            ops.verify(222, acquisition=True)

    def test_adapter_verify_rejects_boolean_zero_wrong_lease_and_missing_host(self):
        for change in ('bool_outputs', 'float_outputs', 'bool_counter', 'wrong_lease', 'host_missing', 'stale_clock'):
            ops = self.ops(); ops.launched = {222: 'stamp'}; status = self.status()
            if change == 'bool_outputs': status['control']['outputs'] = [False] * 4
            if change == 'float_outputs': status['control']['outputs'] = [0.0] * 4
            if change == 'bool_counter': status['control']['drive_commands_sent'] = False
            if change == 'wrong_lease': status['control']['lease_ms'] = 999
            if change == 'host_missing': del status['host']
            if change == 'stale_clock': status['host']['device_time'] = '2000-01-01T00:00:00+00:00'
            with self.subTest(change=change), patch.object(ops, 'proc', return_value={'start_identity': 'stamp', 'cwd': deploy.REMOTE_ROOT}), patch.object(ops, 'get', return_value=status):
                with self.assertRaises(deploy.DeployError): ops.verify(222, acquisition=True)

    def test_log_close_failure_cannot_lose_a_live_spawned_child(self):
        ops = self.ops(); child = Mock(pid=222); child.poll.return_value = None
        class BadLog:
            def __enter__(self): return self
            def __exit__(self, *args): raise OSError('SOFTWARE_FIXTURE_LOG_CLOSE_FAILURE')
        with patch.object(ops, 'owners', return_value=[]), patch.object(ops, 'proc', return_value=None), patch.object(Path, 'open', return_value=BadLog()), patch.object(deploy.subprocess, 'Popen', return_value=child):
            with self.assertRaises(OSError): ops.launch(snapshot(), True)
            self.assertEqual(ops.new_pid, 222); self.assertIs(ops.children[222], child)
            with self.assertRaisesRegex(deploy.DeployError, 'unreadable_do_not_signal'):
                ops.stop_candidate_if_safe()
            child.wait.assert_not_called()

    def test_missing_child_handle_is_not_treated_as_confirmed_exit(self):
        ops = self.ops(); ops.new_pid = 222
        with patch.object(ops, 'owners', return_value=[]):
            with self.assertRaisesRegex(deploy.DeployError, 'missing_owned_child_handle'):
                ops.stop_candidate_if_safe()

    def test_unreadable_proc_does_not_allow_restart_of_a_live_owned_child(self):
        ops = self.ops(); ops.new_pid = 222; ops.launched = {222: 'stamp'}
        ops.children = {222: Mock(poll=Mock(return_value=None))}
        with patch.object(ops, 'proc', return_value=None), patch.object(ops, 'owners', return_value=[]):
            with self.assertRaises(deploy.DeployError): ops.stop_candidate_if_safe()

    def test_owned_child_exit_and_no_owner_allows_rollback_without_signals(self):
        ops = self.ops(); ops.new_pid = 222; ops.children = {222: Mock(poll=Mock(return_value=1))}
        with patch.object(ops, 'owners', return_value=[]), patch.object(ops, 'terminate_exact') as terminate:
            ops.stop_candidate_if_safe(); terminate.assert_not_called()

    def test_pidfd_binds_before_fingerprint_and_never_signals_numeric_pid(self):
        import select
        ops = self.ops(); events = []
        def bind(pid, flags): events.append('bind_original_process'); return 987654
        def observe(pid): events.append('verify_identity'); return {'start_identity': 'stamp', 'cwd': deploy.REMOTE_ROOT}
        def send(fd, sig, info, flags): events.append('signal_bound_handle_even_if_numeric_pid_reused'); self.assertEqual(fd, 987654)
        poller = Mock(); poller.poll.return_value = [(987654, 1)]
        with patch.object(deploy.os, 'pidfd_open', side_effect=bind, create=True), patch.object(deploy.signal, 'pidfd_send_signal', side_effect=send, create=True), patch.object(deploy.os, 'close') as close, patch.object(select, 'poll', return_value=poller, create=True), patch.object(select, 'POLLIN', 1, create=True), patch.object(ops, 'proc', side_effect=observe), patch.object(deploy.os, 'kill') as kill:
            ops.terminate_exact(222, 'stamp'); kill.assert_not_called(); close.assert_called_once_with(987654)
        self.assertEqual(events, ['bind_original_process', 'verify_identity', 'signal_bound_handle_even_if_numeric_pid_reused'])

    def test_missing_pidfd_support_refuses_without_numeric_signal_fallback(self):
        ops = self.ops()
        with patch.object(deploy.os, 'pidfd_open', None, create=True), patch.object(deploy.os, 'kill') as kill:
            with self.assertRaisesRegex(deploy.DeployError, 'no_numeric_signal_fallback'):
                ops.terminate_exact(222, 'stamp')
            kill.assert_not_called()

    def test_master_loss_after_check_keeps_no_fallback_options_and_never_retries(self):
        with tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_DEPLOY_') as folder:
            file = Path(folder) / 'cm'; connection = socket.socket(socket.AF_UNIX)
            try:
                connection.bind(str(file)); known = Path(folder) / 'known_hosts'; known.write_text('SOFTWARE_FIXTURE')
                runner = Mock(side_effect=[Mock(returncode=0), Mock(returncode=255)])
                with self.assertRaises(deploy.DeployError): deploy.execute_remote('preflight', {}, file, known, runner)
                self.assertEqual(runner.call_count, 2)
                options = runner.call_args.args[0]
                self.assertIn('ProxyCommand=false', options); self.assertIn('ControlMaster=no', options)
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
