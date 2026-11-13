"""Explicit desktop-connection preflight, streamed to the pinned robot over SSH.

CLOCK_SOURCE and RECOVERY_SOURCE are supplied by the desktop's trusted runtime.
No boot service is installed. Firmware is never erased, flashed or unprotected.
"""
import contextlib
import io
import json
from pathlib import Path
import signal
import sys


def application_live(control):
    diagnostics = control.get('serial_diagnostics') or {}
    return ('Motor_Version:1.7.3' in diagnostics.get('configuration', '')
            and '$Battery:' in diagnostics.get('battery', ''))


def idle_for_recovery(control, acquisition):
    outputs = control.get('outputs')
    return (control.get('enabled') is True and control.get('state') == 'stopped'
            and control.get('armed') is False and control.get('fault') is None
            and isinstance(outputs, list) and len(outputs) == 4
            and all(type(value) is int and value == 0 for value in outputs)
            and control.get('drive_commands_sent') == 0
            and acquisition.get('active_run_id') is None
            and all(acquisition.get(key) is False for key in ('busy', 'committing', 'closing')))


def prepare_console(target, synchronize, read, recover, claim_attempt):
    result = {'clock': synchronize(target), 'motor': 'not_modified'}
    control = read('control')
    if control.get('enabled') is False:
        result['motor'] = 'disabled'
        return result
    if application_live(control):
        result['motor'] = 'application_replies_already_present'
        return result
    diagnostics = control.get('serial_diagnostics') or {}
    if not all(diagnostics.get(key) == '' for key in ('configuration', 'battery')):
        result['motor'] = 'attention_nonempty_unrecognized_reply'
        return result
    if not idle_for_recovery(control, read('experiments')):
        result['motor'] = 'attention_not_idle_no_reset'
        return result
    if not claim_attempt():
        result['motor'] = 'attention_already_attempted_this_robot_boot'
        return result
    try:
        recover()
        fresh = read('control')
        result['motor'] = ('original_application_recovered' if application_live(fresh)
                           else 'attention_recovery_did_not_restore_replies')
    except Exception as error:
        result['motor'] = 'attention_recovery_failed'
        result['error'] = type(error).__name__ + ': ' + str(error)[-240:]
    return result


def main():
    clock_namespace = {'__name__': 'desktop_clock_preflight'}
    exec(compile(CLOCK_SOURCE, '<desktop-clock-preflight>', 'exec'), clock_namespace)
    namespace = {'__name__': 'desktop_known_motor_recovery'}
    exec(compile(RECOVERY_SOURCE, '<desktop-known-motor-recovery>', 'exec'), namespace)

    def claim_attempt():
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not boot or any(char not in '0123456789abcdef-' for char in boot):
            raise ValueError('Invalid kernel boot identity')
        marker = Path('/dev/shm') / ('rbe201tc-desktop-motor-' + boot + '.attempted')
        try:
            with marker.open('x') as output:
                output.write('One explicit desktop-connect recovery attempt; no flash write.\n')
            return True
        except FileExistsError:
            return False

    def recover():
        arguments = sys.argv
        capture = io.StringIO()
        try:
            sys.argv = ['desktop-motor-recovery', '--pause-monitor']
            # Convert session interruption to an exception so the existing
            # helper's finally block restores the stopped monitor service.
            def interrupted(signum, frame):
                raise KeyboardInterrupt('Desktop connection interrupted')
            for signum in (signal.SIGHUP, signal.SIGTERM):
                signal.signal(signum, interrupted)
            with contextlib.redirect_stdout(capture):
                namespace['main']()
        finally:
            sys.argv = arguments
            if capture.tell():
                print('MOTOR_RECOVERY ' + capture.getvalue()[-1200:], flush=True)

    result = prepare_console(int(sys.argv[1]), clock_namespace['synchronize'],
                             namespace['api'], recover, claim_attempt)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
