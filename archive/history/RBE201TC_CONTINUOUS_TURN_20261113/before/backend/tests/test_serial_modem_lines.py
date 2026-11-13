"""No real hardware: verify Linux modem-line updates are single-mask ioctls."""
import errno
import sys
import types
import unittest
from unittest.mock import patch

from motor_control import SerialPWM, _AtomicModemLines


@unittest.skipUnless(sys.platform != 'win32', 'POSIX modem ioctls')
class AtomicModemTests(unittest.TestCase):
    def test_each_update_applies_the_complete_desired_pair_once(self):
        import termios

        class Lines(_AtomicModemLines):
            fd = 47

        for dtr in (False, True):
            for rts in (False, True):
                for update in ('_update_dtr_state', '_update_rts_state'):
                    with self.subTest(dtr=dtr, rts=rts, update=update):
                        port = Lines()
                        port._dtr_state, port._rts_state = dtr, rts
                        expected = (termios.TIOCM_DTR if dtr else 0) | (termios.TIOCM_RTS if rts else 0)
                        with patch('fcntl.ioctl') as ioctl:
                            getattr(port, update)()
                        self.assertEqual(ioctl.call_count, 1)
                        args = ioctl.call_args.args
                        self.assertEqual(args[:2], (47, termios.TIOCMSET))
                        self.assertEqual(list(args[2]), [expected])
                        self.assertIs(args[3], True)

    def test_ioctl_errors_are_not_hidden(self):
        class Lines(_AtomicModemLines):
            fd = 47
            _dtr_state = False
            _rts_state = False

        with patch('fcntl.ioctl', side_effect=OSError(errno.EIO, 'modem control failed')):
            with self.assertRaises(OSError):
                Lines()._update_dtr_state()

    def test_linux_transport_open_never_applies_a_partial_false_true_pair(self):
        class FakePosixSerial:
            def __init__(self, **kwargs):
                self.settings = kwargs
                self._dtr_state = self._rts_state = True
                self.fd = None
                self.closed = False

            @property
            def dtr(self):
                return self._dtr_state

            @dtr.setter
            def dtr(self, value):
                self._dtr_state = value

            @property
            def rts(self):
                return self._rts_state

            @rts.setter
            def rts(self, value):
                self._rts_state = value

            def open(self):
                self.before_open = (self.dtr, self.rts)
                self.fd = 47
                self._update_dtr_state()
                self._update_rts_state()

            def close(self):
                self.closed = True

        with patch.dict(sys.modules, {'serial': types.SimpleNamespace(Serial=FakePosixSerial)}):
            with patch('sys.platform', 'linux'), patch('fcntl.ioctl') as ioctl:
                transport = SerialPWM('/software-fixture/no-hardware')
        self.assertIsInstance(transport.serial, _AtomicModemLines)
        self.assertEqual(transport.serial.before_open, (False, False))
        self.assertEqual(transport.serial.settings['baudrate'], 115200)
        self.assertEqual(transport.serial.settings['timeout'], 0)
        self.assertEqual(transport.serial.settings['write_timeout'], .08)
        self.assertTrue(transport.serial.settings['exclusive'])
        self.assertEqual([list(c.args[2]) for c in ioctl.call_args_list], [[0], [0]])
        transport.close()
        self.assertTrue(transport.serial.closed)


if __name__ == '__main__':
    unittest.main()
