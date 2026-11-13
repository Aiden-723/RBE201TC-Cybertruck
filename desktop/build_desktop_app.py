#!/usr/bin/env python3
"""Build a new, signed macOS app. Refuse all existing targets; never launch it.

Only the named Desktop bundle is created. No launch/login registration, runtime
installation, robot access, browser launch, or service creation is performed.
"""
from pathlib import Path
import os
import plistlib
import shutil
import stat
import subprocess
import sys

APP = (Path.home() / 'Desktop/小车控制台.app')
DESKTOP = (Path.home() / 'Desktop')
SOURCE = Path(__file__).resolve().with_name('desktop_app.swift')


def run(*arguments):
    print('+', ' '.join(map(str, arguments)), flush=True)
    subprocess.run([str(item) for item in arguments], check=True)


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This builder requires macOS and the Swift command-line tools.')
    # Validate absolute paths before any write, and never overwrite a user App.
    if APP != (Path.home() / 'Desktop/小车控制台.app'):
        raise SystemExit('Unexpected output target.')
    if not DESKTOP.is_dir() or DESKTOP.resolve() != DESKTOP or APP.parent != DESKTOP:
        raise SystemExit('Desktop must exist at the expected non-symlink absolute path.')
    if os.path.lexists(APP):
        raise SystemExit(f'Refusing to overwrite an existing file, symlink or bundle: {APP}')
    if not SOURCE.is_file():
        raise SystemExit(f'Swift source is missing: {SOURCE}')

    # mkdir(exist_ok=False) also protects against an intervening creation.
    APP.mkdir(mode=0o755, exist_ok=False)
    contents = APP / 'Contents'
    macos = contents / 'MacOS'
    resources = contents / 'Resources'
    macos.mkdir(parents=True)
    resources.mkdir()
    cache = resources / 'BuildModuleCache'
    executable = macos / 'RBE201TCConsole'
    info = {
        'CFBundleDevelopmentRegion': 'zh_CN',
        'CFBundleLocalizations': ['zh_CN', 'en'],
        'CFBundleDisplayName': '小车控制台',
        'CFBundleName': '小车控制台',
        'CFBundleExecutable': executable.name,
        'CFBundleIdentifier': 'cn.rbe201tc.manual-connection-console',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': '1.0',
        'CFBundleVersion': '1',
        'LSMinimumSystemVersion': '12.0',
        'LSMultipleInstancesProhibited': True,
        'LSUIElement': False,
        'NSHighResolutionCapable': True,
        'NSPrincipalClass': 'NSApplication',
        'NSSupportsAutomaticTermination': False,
        'NSSupportsSuddenTermination': False,
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    }
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info, sort_keys=True))
    (contents / 'PkgInfo').write_bytes(b'APPL????')
    arch = os.uname().machine
    if arch not in ('arm64', 'x86_64'):
        raise SystemExit(f'Unsupported native architecture: {arch}')
    run('/usr/bin/xcrun', 'swiftc', '-swift-version', '5', '-O',
        '-target', f'{arch}-apple-macosx12.0',
        '-module-cache-path', cache,
        '-framework', 'Cocoa', SOURCE, '-o', executable)
    executable.chmod(0o755)

    # This directory was created by this invocation, inside the new exact App.
    # Revalidate the resolved absolute deletion target immediately before removing it.
    expected_cache = (APP / 'Contents/Resources/BuildModuleCache')
    if cache.exists():
        if cache.is_symlink() or cache.resolve() != expected_cache or APP.resolve() != APP:
            raise SystemExit('Refusing to delete an unexpected module-cache path.')
        print(f'Removing verified compiler cache only: {expected_cache}', flush=True)
        shutil.rmtree(expected_cache)

    run('/usr/bin/plutil', '-lint', contents / 'Info.plist')
    run('/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', APP)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', APP)
    run('/usr/bin/codesign', '-d', '--verbose=2', APP)
    run('/usr/bin/file', executable)
    mode = stat.S_IMODE(executable.stat().st_mode)
    if mode != 0o755:
        raise SystemExit(f'Unexpected executable mode: {mode:o}')
    with (contents / 'Info.plist').open('rb') as stream:
        validated = plistlib.load(stream)
    if validated != info or validated['CFBundleExecutable'] != executable.name:
        raise SystemExit('Info.plist verification failed.')
    print(f'Installed new app: {APP}')
    print(f'Executable mode: {mode:o}; native architecture: {arch}; ad-hoc signature verified.')
    print('App has NOT been launched. Runtime deployment and live tests remain with the parent agent.')


if __name__ == '__main__':
    main()
