"""Install private runtime files for the manually launched desktop application.

No launch agent, login item, boot service, private-key copy, or robot mutation.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess

SOURCE = Path(__file__).resolve().parent
SCRIPTS = ('supervise_wifi_connection.py', 'maintain_wifi_connection.py',
           'sync_clock_before_connect.py', 'prepare_console_before_connect.py',
           'recover_motor_application.py')


def runtime_paths(home):
    home = Path(home)
    return (home / 'Library/Application Support/RBE201TC/connection',
            home / 'Library/Logs/RBE201TC')


def render_ssh_config(text, runtime):
    lines = []
    for line in text.splitlines():
        if line.strip().startswith('UserKnownHostsFile '):
            line = '    UserKnownHostsFile "' + str(runtime / 'known_hosts') + '"'
        lines.append(line)
    rendered = '\n'.join(lines) + '\n'
    for required in ('StrictHostKeyChecking yes', 'BatchMode yes',
                     'ControlMaster no', 'ControlPath none',
                     'ControlPersist no', 'ExitOnForwardFailure yes',
                     'LocalForward 127.0.0.1:8765 127.0.0.1:8765'):
        if required not in rendered:
            raise ValueError('Missing required SSH policy: ' + required)
    return rendered


def install_file(path, data):
    path = Path(path)
    if path.exists() and path.read_bytes() == data:
        return False
    temporary = path.with_name(path.name + '.install-' + str(os.getpid()))
    expected = path.absolute()
    if path.resolve() != expected or temporary.resolve().parent != expected.parent:
        raise ValueError('Unexpected symlink/destination: ' + str(path))
    with temporary.open('xb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, expected)
    return True


def install():
    runtime, logs = runtime_paths(Path.home())
    for folder in (runtime, runtime / 'wifi', logs):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {}
    for name in SCRIPTS:
        data = (SOURCE / name).read_bytes()
        compile(data, str(runtime / name), 'exec')
        install_file(runtime / name, data)
        manifest[name] = hashlib.sha256(data).hexdigest()
    keys = (SOURCE / 'known_hosts').read_bytes()
    records = [line.split() for line in keys.decode().splitlines() if line.strip()]
    if ({line[0] for line in records} != {'192.168.127.10', '192.168.86.5'}
            or len({tuple(line[1:]) for line in records}) != 1):
        raise ValueError('Robot address pins differ or are incomplete')
    install_file(runtime / 'known_hosts', keys)
    config = render_ssh_config((SOURCE / 'autoboat_ssh_config').read_text(), runtime)
    install_file(runtime / 'autoboat_ssh_config', config.encode())
    install_file(runtime / 'runtime_manifest.json', json.dumps(manifest, indent=2).encode())
    # Retire only this project's legacy detached master, not any unknown listener.
    old = ['/usr/bin/ssh', '-F', str(SOURCE / 'autoboat_ssh_config'),
           '-S', str(SOURCE / 'wifi/cm'), '-O']
    check = subprocess.run(old + ['check', 'rbe201tc-wifi'], capture_output=True, timeout=5)
    if check.returncode == 0:
        subprocess.run(old + ['exit', 'rbe201tc-wifi'], check=True, timeout=5)
    print(json.dumps({'runtime': str(runtime), 'manual_launch_only': True,
                      'private_key_copied': False, 'robot_services_changed': False,
                      'scripts_sha256': manifest}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', action='store_true', required=True)
    parser.parse_args()
    install()
