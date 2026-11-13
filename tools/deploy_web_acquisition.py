"""Guarded six-file CSV web deployment. No action runs without an explicit mode.

Does not configure networking, create SSH connections, send motor POSTs, or run
sensor workers. --apply requires a pre-existing authenticated SSH master and a
fresh, healthy, disarmed zero-command state. Software preparation is not evidence
of a successful physical deployment. A failed/ambiguous remote operation is never
replayed automatically. Historical CSVs are never deleted during rollback.
"""
import argparse
import ast
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
import zipfile

TARGET = 'sunrise@192.168.127.10'
REMOTE_ROOT = '/home/sunrise/rbe201tc-sensor-studio/device-console-v2'
EXPERIMENT_ROOT = '/home/sunrise/rbe201tc-sensor-studio/data/web_captures'
SERIAL_PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
OLD_ARGS = ['-u', 'backend/server.py', '--host', '127.0.0.1', '--port', '8765',
            '--sensor-state-dir', '/dev/shm/rbe201tc-sensor-studio',
            '--motor-port', SERIAL_PORT, '--motor-mode', 'speed']
PROTECTED = {
    'backend/motor_control.py': 'b243f30a2fd214c192439b3528fcad574218519cb24275905bcd4de3afc0081b',
    'backend/hardware_worker.py': '5437d50a9f7e72982ae667aa6c65873862537d22f0be778815d91831e08cff53',
    'frontend/control.js': '8cccd85db333304e42d9f8e0fc8cc17ed58525158e04ce09aa9aab1bcb857ccd',
}
OLD = {
    'backend/server.py': '6210a2174a77ccc310bf95d4561a514865f3fa2a1967988021fc020ab4e2cec0',
    'frontend/index.html': '2e088a3f1cc464615fb2c3087900b93a2ae9d82fd2f99bdbb9905b8c30ca54e7',
}
AUDITED_NEW = {
    'backend/acquisition.py': '0a5a5f03f0a758918da03e91315bd757463c5272d851d14ed0fe7e06b3b446f0',
    'backend/experiment_log.py': 'bfef941f4039bdc61bad9a38969811d55c6e935fe80eca884455829e6e74984d',
    'backend/server.py': 'e801f73bfe5a4a1445120feb2b7acbb16997f9a6379cf91e659ea38da653cd48',
    'frontend/acquisition.js': 'b4415dd5bad6486860a1a124a5ee03f4c7324c6f413f6b4ad215a85be357f2ed',
    'frontend/acquisition.css': '163ecf39e7c80cdbf9627954d1709144fe9e98ee150ffe5efe36e8380386b266',
    'frontend/index.html': 'b4ac6a98e5fa3cbabe6261214b574bd728302a606f9debc00bd2a9000bd01ef1',
}


class DeployError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {'schema_version', 'files'} or bundle['schema_version'] != 1:
        raise DeployError('invalid_bundle')
    if not isinstance(bundle['files'], dict) or set(bundle['files']) != set(AUDITED_NEW):
        raise DeployError('invalid_file_whitelist')
    result = {}
    for name, encoded in bundle['files'].items():
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise DeployError('invalid_file_encoding') from exc
        if digest(data) != AUDITED_NEW[name]:
            raise DeployError('candidate_not_the_audited_version:' + name)
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8'), filename=name)
        result[name] = data
    return result


def build_bundle(project):
    project = Path(project).resolve()
    for name, expected in PROTECTED.items():
        file = project / name
        if file.is_symlink() or digest(file.read_bytes()) != expected:
            raise DeployError('protected_source_changed:' + name)
    bundle = {'schema_version': 1, 'files': {name: base64.b64encode((project / name).read_bytes()).decode('ascii') for name in AUDITED_NEW}}
    decode_bundle(bundle)
    return bundle


def validate_client_clock(stamp, now=None):
    try:
        value = datetime.fromisoformat(stamp)
        now = now or datetime.now(timezone.utc)
        valid = value.tzinfo is not None and abs((now - value).total_seconds()) <= 5
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise DeployError('client_and_board_clock_not_within_five_seconds')


def validate_control(control, *, counter=None, allow_fault=False):
    required = {'enabled', 'armed', 'state', 'fault', 'outputs', 'command_mode',
                'max_hold_seconds', 'lease_ms', 'drive_commands_sent'}
    if not isinstance(control, dict) or not required.issubset(control):
        raise DeployError('incomplete_control_contract')
    values = control.get('outputs')
    if (not isinstance(control, dict) or not isinstance(values, list) or len(values) != 4
            or any(type(value) is not int or value != 0 for value in values)
            or control.get('enabled') is not True or control.get('armed') is not False
            or control.get('state') not in (('stopped', 'fault') if allow_fault else ('stopped',))
            or (not allow_fault and control.get('fault') is not None)
            or control.get('command_mode') != 'speed' or control.get('max_hold_seconds', 'missing') is not None
            or control.get('lease_ms') != 450 or type(control.get('drive_commands_sent')) is not int
            or control['drive_commands_sent'] < 0 or (counter is not None and control['drive_commands_sent'] != counter)):
        raise DeployError('motor_not_verified_disarmed_zero')


def validate_status(status, *, counter=None):
    host = status.get('host') if isinstance(status, dict) else None
    if (not isinstance(host, dict) or host.get('is_rdk') is not True
            or host.get('clock_valid') is not True or 'RDK X5' not in str(host.get('model', ''))):
        raise DeployError('post_install_host_not_verified')
    validate_client_clock(host.get('device_time'))
    validate_control(status.get('control'), counter=counter)


def validate_snapshot(info, previous=None):
    if (not isinstance(info, dict) or type(info.get('pid')) is not int or info['pid'] <= 1
            or not isinstance(info.get('start_identity'), str) or not info['start_identity'].isdigit()):
        raise DeployError('invalid_process_identity')
    if info.get('model_is_rdk_x5') is not True or info.get('machine') != 'aarch64':
        raise DeployError('unexpected_device')
    if info.get('root') != REMOTE_ROOT or info.get('args') != OLD_ARGS:
        raise DeployError('unexpected_production_command')
    if info.get('serial_is_character_device') is not True or info.get('serial_owners') != [info.get('pid')]:
        raise DeployError('serial_owner_not_unique')
    if info.get('hashes') != {**PROTECTED, **OLD}:
        raise DeployError('production_changed')
    if info.get('pidfd_available') is not True:
        raise DeployError('pidfd_unavailable_no_numeric_signal_fallback')
    c = info.get('control', {})
    validate_control(c)
    if info.get('clock_valid') is not True or info.get('fresh_clock') is not True:
        raise DeployError('clock_not_healthy')
    if previous and any(info.get(k) != previous.get(k) for k in ('pid', 'start_identity', 'hashes')):
        raise DeployError('production_changed_during_preparation')
    if previous and c['drive_commands_sent'] != previous['control']['drive_commands_sent']:
        raise DeployError('operator_activity_during_preparation')


def run_transaction(ops, bundle):
    """Orchestrator isolated for fake-ops fault-injection tests."""
    decode_bundle(bundle)
    first = ops.inspect()
    validate_snapshot(first)
    backup = ops.prepare(bundle, first)
    second = ops.inspect()
    validate_snapshot(second, first)
    # Nothing in production is replaced before the exact old owner has exited.
    try:
        ops.stop_old(second)
    except Exception as exc:
        ops.record({'status': 'old_stop_not_confirmed_no_install_or_second_server',
                    'backup': backup, 'error_type': type(exc).__name__})
        raise DeployError('old_stop_not_confirmed_inspect_before_retry') from exc
    try:
        ops.install(bundle)
        pid = ops.start_new(first)
        ops.verify(pid, acquisition=True)
        return ops.record({'status': 'deployed', 'new_pid': pid, 'backup': backup,
                           'nonzero_motor_commands_sent_by_tool': 0})
    except Exception as exc:
        try:
            # Unknown/active candidate state refuses automatic rollback; no second
            # server is started while a candidate may still own the serial port.
            ops.stop_candidate_if_safe()
            ops.restore()
            pid = ops.start_old(first)
            ops.verify(pid, acquisition=False)
            ops.record({'status': 'failed_rolled_back', 'restored_pid': pid,
                        'backup': backup, 'error_type': type(exc).__name__})
        except Exception as rollback:
            ops.record({'status': 'indeterminate_requires_inspection', 'backup': backup,
                        'error_type': type(exc).__name__, 'rollback_error_type': type(rollback).__name__})
            raise DeployError('rollback_not_completed_do_not_retry_apply') from rollback
        raise DeployError('deployment_failed_original_restored') from exc


class RemoteOps:
    """Linux adapter; instantiated only by an explicit remote request."""
    def __init__(self):
        import platform
        self.root = Path(REMOTE_ROOT)
        if self.root.is_symlink() or self.root.resolve() != self.root or not self.root.is_dir():
            raise DeployError('unexpected_project_root')
        self.machine = platform.machine()
        self.run_dir = None
        self.originals = {}
        self.new_pid = None
        self.launched = {}
        self.children = {}
        self.private = {}

    def path(self, name):
        if name not in set(AUDITED_NEW) | set(PROTECTED):
            raise DeployError('file_not_whitelisted')
        target = self.root / name
        if target.is_symlink() or target.parent.resolve() != self.root / Path(name).parent:
            raise DeployError('symlink_or_unexpected_target')
        return target

    @staticmethod
    def proc(pid):
        proc = Path('/proc') / str(pid)
        try:
            raw = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if raw[0] == 'Z':
                return None
            args = (proc / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            return {'start_identity': raw[19], 'args': [a.decode() for a in args],
                    'cwd': str((proc / 'cwd').resolve()), 'exe': str((proc / 'exe').resolve())}
        except (OSError, ValueError, IndexError):
            return None

    def owners(self):
        device = Path(SERIAL_PORT).resolve(strict=True)
        owners = set()
        # Expected application owners run under this account. Root services are
        # outside this visibility proof; pyserial's existing exclusive lock is
        # retained. We never claim protection against privileged/malicious owners.
        for directory in Path('/proc').iterdir():
            if not directory.name.isdigit():
                continue
            try:
                if directory.stat().st_uid != os.getuid():
                    continue
                for fd in (directory / 'fd').iterdir():
                    try:
                        if fd.resolve(strict=True) == device:
                            owners.add(int(directory.name))
                    except FileNotFoundError:
                        continue
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                raise DeployError('same_user_owner_scan_incomplete') from exc
        return sorted(owners)

    @staticmethod
    def get(path, token):
        import urllib.request
        if path not in ('/api/v1/status', '/api/v1/experiments'):
            raise DeployError('unexpected_observation_path')

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise DeployError('http_redirect_refused')

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        expected = 'http://127.0.0.1:8765' + path
        request = urllib.request.Request(expected, headers={'Authorization': 'Bearer ' + token} if token else {})
        with opener.open(request, timeout=2) as response:
            if response.status != 200 or response.geturl() != expected:
                raise DeployError('unexpected_http_response')
            if response.headers.get_content_type() != 'application/json':
                raise DeployError('unexpected_content_type')
            body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise DeployError('oversized_response')
            def invalid_constant(value):
                raise ValueError('nonfinite_json_constant')
            packet = json.loads(body, parse_constant=invalid_constant)
            if not isinstance(packet, dict) or packet.get('schema_version') != '1.0' or not isinstance(packet.get('data'), dict):
                raise DeployError('invalid_response_schema')
            validate_client_clock(packet.get('generated_at'))
            return packet['data']

    def inspect(self):
        found = []
        for directory in Path('/proc').iterdir():
            if not directory.name.isdigit():
                continue
            item = self.proc(int(directory.name))
            if item and item['cwd'] == REMOTE_ROOT and 'backend/server.py' in item['args']:
                found.append((int(directory.name), item))
        if len(found) != 1:
            raise DeployError('expected_exactly_one_web_owner')
        pid, process = found[0]
        environment = dict(item.split('=', 1) for item in (Path('/proc') / str(pid) / 'environ').read_bytes().decode().split('\0') if '=' in item)
        token = environment.get('DEVICE_CONSOLE_TOKEN', '')
        status = self.get('/api/v1/status', token)
        host = status.get('host', {})
        try:
            stamp = datetime.fromisoformat(host['device_time'])
            fresh = stamp.tzinfo is not None and abs((datetime.now(timezone.utc) - stamp).total_seconds()) <= 5
        except (ValueError, TypeError, KeyError):
            fresh = False
        self.private = {'environment': environment, 'token': token, 'exe': process['exe']}
        model = Path('/proc/device-tree/model').read_bytes()[:256]
        pidfd_available = False
        if callable(getattr(os, 'pidfd_open', None)) and callable(getattr(signal, 'pidfd_send_signal', None)):
            try:
                fd = os.pidfd_open(os.getpid(), 0)
                os.close(fd)
                pidfd_available = True
            except OSError:
                pass
        return {'pidfd_available': pidfd_available, 'pid': pid, 'start_identity': process['start_identity'], 'root': process['cwd'],
                'args': process['args'][1:], 'machine': self.machine,
                'model_is_rdk_x5': b'RDK X5' in model and host.get('is_rdk') is True,
                'serial_is_character_device': stat.S_ISCHR(Path(SERIAL_PORT).stat().st_mode),
                'serial_owners': self.owners(), 'hashes': {n: digest(self.path(n).read_bytes()) for n in {**PROTECTED, **OLD}},
                'clock_valid': host.get('clock_valid'), 'fresh_clock': fresh,
                'control': status.get('control', {}), 'auth_enabled': bool(token)}

    @staticmethod
    def sync_directory(path):
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def atomic(self, target, data):
        # Verify absolute destinations before every replace. No deletion is used.
        expected = {self.path(name) for name in AUDITED_NEW}
        if target not in expected or target.resolve(strict=False) != target:
            raise DeployError('unexpected_replace_target')
        temporary = target.with_name(target.name + '.deploy-' + uuid.uuid4().hex)
        with temporary.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if temporary.parent.resolve() != target.parent or target.is_symlink():
            raise DeployError('replace_target_changed')
        os.replace(str(temporary), str(target))
        fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def prepare(self, bundle, info):
        decode_bundle(bundle)
        base = self.root.parent / 'deployments'
        if base.is_symlink() or base.resolve(strict=False) != base:
            raise DeployError('unexpected_backup_root')
        base.mkdir(exist_ok=True)
        tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8]
        self.run_dir = base / ('web_csv_' + tag)
        self.run_dir.mkdir(mode=0o700)
        for name in AUDITED_NEW:
            target = self.path(name)
            original = target.read_bytes() if target.exists() else None
            self.originals[name] = original
            if original is not None:
                dest = self.run_dir / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open('xb') as stream:
                    stream.write(original); stream.flush(); os.fsync(stream.fileno())
                if digest(dest.read_bytes()) != digest(original):
                    raise DeployError('backup_verification_failed')
                self.sync_directory(dest.parent)
        self.sync_directory(self.run_dir)
        self.sync_directory(base)
        self.record({'status': 'prepared_not_installed', 'old_pid': info['pid'],
                     'original_sha256': {n: digest(b) if b is not None else None for n, b in self.originals.items()},
                     'candidate_sha256': AUDITED_NEW, 'auth_enabled': info['auth_enabled'],
                     'owner_visibility': 'current UID plus retained pyserial exclusive lock; privileged processes not proven'})
        return str(self.run_dir)

    def terminate_exact(self, pid, expected_start):
        import select
        if not callable(getattr(os, 'pidfd_open', None)) or not callable(getattr(signal, 'pidfd_send_signal', None)):
            raise DeployError('pidfd_unavailable_no_numeric_signal_fallback')
        try:
            fd = os.pidfd_open(pid, 0)
        except ProcessLookupError:
            return
        try:
            current = self.proc(pid)
            if current is None or current['start_identity'] != expected_start or current['cwd'] != REMOTE_ROOT:
                raise DeployError('pid_changed_do_not_signal')
            # The handle is bound BEFORE verification; numeric PID reuse between
            # verification and send cannot redirect this signal to another process.
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
            except ProcessLookupError:
                return
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            if not poller.poll(8000):
                raise DeployError('web_owner_did_not_exit_no_second_server_started')
        finally:
            os.close(fd)

    def stop_old(self, info):
        self.terminate_exact(info['pid'], info['start_identity'])
        if self.owners():
            raise DeployError('serial_still_owned_no_install_or_second_server')
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', 8765)) == 0:
                raise DeployError('port_still_owned_no_install_or_second_server')

    def install(self, bundle):
        for name, data in decode_bundle(bundle).items():
            self.atomic(self.path(name), data)
        self.record({'status': 'installed_not_yet_verified'})

    def launch(self, info, acquisition):
        if self.owners():
            raise DeployError('serial_owned_refuse_second_server')
        argv = [self.private['exe']] + info['args']
        if acquisition:
            argv += ['--experiment-dir', EXPERIMENT_ROOT]
        log_path = self.run_dir / ('candidate.log' if acquisition else 'restored.log')
        with log_path.open('xb') as log:
            child = subprocess.Popen(argv, cwd=REMOTE_ROOT, env=self.private['environment'],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True)
            # Track the owned child before log __exit__/close or any other fallible
            # operation. A missing /proc entry alone is never proof of child exit.
            self.children[child.pid] = child
            if acquisition:
                self.new_pid = child.pid
            self.launched[child.pid] = None
            process = self.proc(child.pid)
            if process:
                self.launched[child.pid] = process['start_identity']
        return child.pid

    def start_new(self, info):
        return self.launch(info, True)

    def start_old(self, info):
        return self.launch(info, False)

    def verify(self, pid, acquisition):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                process = self.proc(pid)
                if process is None:
                    raise DeployError('web_exited')
                if (not self.launched.get(pid) or process['start_identity'] != self.launched[pid]
                        or process['cwd'] != REMOTE_ROOT):
                    raise DeployError('launched_process_identity_changed')
                status = self.get('/api/v1/status', self.private['token'])
                validate_status(status, counter=0)
                if self.owners() == [pid]:
                    expected = AUDITED_NEW if acquisition else OLD
                    if any(digest(self.path(n).read_bytes()) != sha for n, sha in {**PROTECTED, **expected}.items()):
                        raise DeployError('post_install_hash_mismatch')
                    if acquisition and self.get('/api/v1/experiments', self.private['token']).get('enabled') is not True:
                        raise DeployError('acquisition_not_enabled')
                    return
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(.1)
        raise DeployError('verification_failed_no_physical_movement_claim')

    def stop_candidate_if_safe(self):
        if self.new_pid is None:
            if self.children or self.owners():
                raise DeployError('unknown_owner_do_not_restart')
            return
        child = self.children.get(self.new_pid)
        if child is None:
            raise DeployError('missing_owned_child_handle_do_not_restart')
        if child.poll() is not None:
            if self.owners():
                raise DeployError('candidate_exit_with_unknown_owner')
            return
        current = self.proc(self.new_pid)
        expected = self.launched.get(self.new_pid)
        if current is None or not expected or current['start_identity'] != expected:
            raise DeployError('candidate_pid_changed_or_unreadable_do_not_signal')
        c = self.get('/api/v1/status', self.private['token']).get('control', {})
        validate_control(c, counter=0, allow_fault=True)
        self.terminate_exact(self.new_pid, expected)
        child.wait(timeout=2)
        if self.owners():
            raise DeployError('candidate_still_owns_serial')

    def restore(self):
        for name, data in self.originals.items():
            if data is not None:
                self.atomic(self.path(name), data)
        # Additive files which did not exist in the old version can remain inert;
        # restoring original server/index stops loading them. Never delete data.

    def record(self, data):
        if self.run_dir is None:
            raise DeployError('missing_backup_journal')
        data = {**data, 'observed_at': datetime.now(timezone.utc).isoformat(), 'backup_directory': str(self.run_dir)}
        path = self.run_dir / ('journal_' + uuid.uuid4().hex + '.json')
        with path.open('x') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2); stream.flush(); os.fsync(stream.fileno())
        self.sync_directory(self.run_dir)
        return data


def remote_request(request):
    validate_client_clock(request.get('client_time'))
    ops = RemoteOps()
    if request['action'] == 'preflight':
        info = ops.inspect(); validate_snapshot(info)
        # No token, environment, session, or control key is returned.
        return {'status': 'preflight_passed_no_changes', 'pid': info['pid'],
                'protected_and_original_hashes': info['hashes'], 'zero_commands': True,
                'auth_enabled': info['auth_enabled']}
    if request['action'] != 'apply':
        raise DeployError('unknown_action')
    import fcntl
    lock_path = Path(REMOTE_ROOT).parent / '.web_csv_deploy.lock'
    if lock_path.is_symlink():
        raise DeployError('unexpected_lock_path')
    with lock_path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        return run_transaction(ops, request['bundle'])


def execute_remote(action, bundle, control_socket, known_hosts, runner=subprocess.run):
    control_socket, known_hosts = Path(control_socket), Path(known_hosts)
    if not control_socket.exists() or not stat.S_ISSOCK(control_socket.stat().st_mode) or control_socket.stat().st_uid != os.getuid():
        raise DeployError('existing_authenticated_ssh_master_required_no_network_changes_made')
    if not known_hosts.is_file():
        raise DeployError('pinned_known_hosts_required')
    args = ['ssh', '-S', str(control_socket), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ControlMaster=no', '-o', 'ControlPersist=no', '-o', 'ProxyCommand=false',
            '-o', 'UserKnownHostsFile=' + str(known_hosts), '-o', 'ConnectTimeout=4']
    check = runner(args + ['-O', 'check', TARGET], capture_output=True, text=True, timeout=5)
    if check.returncode:
        raise DeployError('ssh_master_not_live')
    request = {'action': action, 'bundle': bundle, 'client_time': datetime.now(timezone.utc).isoformat()}
    program = '_REMOTE_REQUEST = ' + repr(json.dumps(request)) + '\n' + Path(__file__).read_text()
    try:
        result = runner(args + [TARGET, 'python3', '-', '--remote'], input=program,
                        capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise DeployError('remote_state_indeterminate_do_not_retry_apply') from exc
    if result.returncode:
        raise DeployError('remote_operation_failed_or_indeterminate_inspect_remote_journal_no_automatic_retry')
    return json.loads(result.stdout)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', type=Path, metavar='NEW_ZIP', help='offline bundle only; never contacts the robot')
    mode.add_argument('--preflight', action='store_true', help='read-only remote checks through an existing SSH master')
    mode.add_argument('--apply', action='store_true', help='explicit guarded deployment; no motor movement POSTs')
    cli.add_argument('--control-socket', type=Path)
    cli.add_argument('--known-hosts', type=Path)
    args = cli.parse_args(argv)
    try:
        bundle = build_bundle(args.project)
        if args.prepare:
            with zipfile.ZipFile(args.prepare, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in decode_bundle(bundle).items():
                    archive.writestr(name, data)
                archive.writestr('deployment_bundle.json', json.dumps(bundle, indent=2))
                archive.writestr('README.txt', 'Offline audited six-file candidate, NOT proof of deployment. Use the separate guarded tool with an existing authenticated SSH master. Do not start a second server or modify motor/sampler sources.\n')
            result = {'status': 'prepared_offline_not_deployed', 'zip': str(args.prepare.resolve()),
                      'files': list(AUDITED_NEW), 'sha256': AUDITED_NEW}
        else:
            if not args.control_socket or not args.known_hosts:
                raise DeployError('explicit_existing_control_socket_and_known_hosts_required')
            result = execute_remote('apply' if args.apply else 'preflight', bundle, args.control_socket, args.known_hosts)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (DeployError, OSError, ValueError, SyntaxError) as exc:
        print(json.dumps({'status': 'not_completed', 'reason': str(exc), 'no_automatic_retry': True}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if '--remote' in sys.argv:
        try:
            print(json.dumps(remote_request(json.loads(globals()['_REMOTE_REQUEST'])), ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'status': 'remote_not_completed', 'error_type': type(exc).__name__,
                              'reason': str(exc), 'do_not_retry_apply_without_inspection': True}), file=sys.stderr)
            raise SystemExit(2)
    else:
        raise SystemExit(main())
