import importlib.util
import unittest


class TelemetryTests(unittest.TestCase):
    def test_passive_reader_configuration_disconnect_and_shutdown(self):
        import service
        import threading
        self.assertTrue(hasattr(service, 'PassiveSerial'), 'passive reader missing')
        opened = threading.Event()
        class FakeSerial:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.dtr = self.rts = True
                self.calls = 0
                self.closed = False
            def open(inner):
                self.assertIsNone(inner.kwargs['port'])
                self.assertTrue(inner.kwargs['exclusive'])
                self.assertEqual(inner.kwargs['baudrate'], 115200)
                self.assertEqual(inner.kwargs['timeout'], 0.2)
                self.assertFalse(inner.dtr)
                self.assertFalse(inner.rts)
                self.assertEqual(inner.port, '/dev/fake-test-only')
                opened.set()
            def read(inner, size):
                self.assertLessEqual(size, 512)
                inner.calls += 1
                if inner.calls == 1:
                    return b'$MAll:2,-2,-191,22#'
                raise OSError('disconnect')
            def write(inner, data):
                self.fail('serial writes forbidden')
            def close(inner):
                inner.closed = True
        state = service.Telemetry()
        reader = service.PassiveSerial('/dev/fake-test-only', state, serial_factory=FakeSerial)
        reader.start()
        self.assertTrue(opened.wait(1))
        reader.thread.join(1)
        reader.stop()
        self.assertFalse(reader.thread.is_alive())
        self.assertTrue(reader.serial.closed)
        self.assertEqual(state.snapshot()['status'], 'disconnected')
        self.assertEqual(state.snapshot()['raw_counts'], [2, -2, -191, 22])

    def test_unsupported_serial_driver_is_reported_not_crashed(self):
        import service
        def incompatible(**kwargs):
            raise TypeError('exclusive unsupported')
        state = service.Telemetry()
        reader = service.PassiveSerial('/dev/fake-test-only', state, serial_factory=incompatible)
        errors = []
        try:
            reader.run()
        except TypeError as exc:
            errors.append(str(exc))
        self.assertEqual(errors, [], 'unsupported serial driver must fail closed')
        self.assertEqual(state.snapshot()['status'], 'disconnected')
        self.assertEqual(state.snapshot()['error'], 'serial_configuration_unsupported')

    def test_missing_dependency_busy_port_and_idle_shutdown(self):
        import service
        import threading
        from unittest.mock import patch
        state = service.Telemetry()
        with patch.dict('sys.modules', {'serial': None}):
            service.PassiveSerial('/dev/fake-test-only', state).run()
        self.assertEqual(state.snapshot()['error'], 'pyserial_unavailable')
        def busy(**kwargs):
            raise OSError('locked by another process')
        service.PassiveSerial('/dev/fake-test-only', state, serial_factory=busy).run()
        self.assertEqual(state.snapshot()['error'], 'serial_open_or_read_error')
        ready = threading.Event()
        class IdleSerial:
            def __init__(self, **kwargs):
                self.closed = False
            def open(self):
                ready.set()
            def read(self, size):
                threading.Event().wait(0.01)
                return b''
            def close(self):
                self.closed = True
        reader = service.PassiveSerial('/dev/fake-test-only', state, serial_factory=IdleSerial)
        reader.start()
        self.assertTrue(ready.wait(1))
        reader.stop()
        self.assertFalse(reader.thread.is_alive())
        self.assertTrue(reader.serial.closed)
        self.assertEqual(state.snapshot()['error'], 'stopped')

    def test_raw_counts_stale_and_disconnect(self):
        self.assertIsNotNone(importlib.util.find_spec('service'), 'service missing')
        from service import Telemetry
        now = [10.0]
        state = Telemetry(clock=lambda: now[0])
        self.assertEqual(state.snapshot()['status'], 'disabled')
        state.connect()
        self.assertEqual(state.snapshot()['status'], 'not_sampled')
        state.ingest(b'$MAll:2,-2,-191,22#')
        value = state.snapshot()
        self.assertEqual(value['raw_counts'], [2, -2, -191, 22])
        self.assertEqual(value['status'], 'observed')
        self.assertEqual(value['encoder_validity'], 'unverified')
        self.assertEqual(value['functional_health'], 'unverified')
        self.assertIsNotNone(value['received_at'])
        now[0] = 12.01
        state.ingest(b'$MAll:bad#')
        self.assertEqual(state.snapshot()['status'], 'stale')
        self.assertAlmostEqual(state.snapshot()['age_seconds'], 2.01)
        state.disconnect('read_error')
        self.assertEqual(state.snapshot()['status'], 'disconnected')
        self.assertEqual(state.snapshot()['raw_counts'], [2, -2, -191, 22])
        state.connect()
        self.assertEqual(state.snapshot()['status'], 'not_sampled')
        self.assertIsNone(state.snapshot()['raw_counts'])
