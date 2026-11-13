"""Fake GPIO only: prove the sole sampler's faster cadence and cleanup."""
import sys
import types
import unittest
from unittest.mock import patch
import hardware_worker


class LineSamplerCadenceTests(unittest.TestCase):
    def fixture(self, setup_failure=None, input_failure=False, publish_failure=False):
        calls = {'setup': [], 'input': [], 'cleanup': [], 'wait': [], 'publish': []}
        gpio = types.ModuleType('Hobot.GPIO'); gpio.BOARD = 'BOARD'; gpio.IN = 'IN'
        gpio.setmode = lambda value: calls.setdefault('mode', value)
        def setup(pin, mode):
            if pin == setup_failure:
                raise RuntimeError('SOFTWARE_FIXTURE_SETUP_FAILURE')
            calls['setup'].append((pin, mode))
        def read(pin):
            calls['input'].append(pin)
            if input_failure:
                raise RuntimeError('SOFTWARE_FIXTURE_INPUT_FAILURE')
            return {15: 1, 18: 0, 16: 0, 11: 1}[pin]
        gpio.setup, gpio.input = setup, read
        gpio.cleanup = lambda pins: calls['cleanup'].append(list(pins))
        hobot = types.ModuleType('Hobot'); hobot.GPIO = gpio
        class Stop:
            def is_set(self): return len(calls['wait']) >= 2
            def wait(self, seconds): calls['wait'].append(seconds)
        class Publisher:
            def send(self, *args, **kwargs):
                calls['publish'].append((args, kwargs))
                if publish_failure:
                    raise RuntimeError('SOFTWARE_FIXTURE_PUBLISH_FAILURE')
        return calls, {'Hobot': hobot, 'Hobot.GPIO': gpio}, Stop(), Publisher()

    def test_single_sampler_reads_all_four_pins_each_twenty_milliseconds(self):
        calls, modules, stop, publisher = self.fixture()
        with patch.dict(sys.modules, modules), patch.object(hardware_worker, 'STOP', stop):
            hardware_worker.line_worker(publisher)
        self.assertEqual(calls['mode'], 'BOARD')
        self.assertEqual(calls['setup'], [(x, 'IN') for x in (15, 18, 16, 11)])
        self.assertEqual(calls['input'], [15, 18, 16, 11] * 2)
        self.assertEqual(calls['wait'], [.02, .02])
        self.assertEqual(calls['cleanup'], [[15, 18, 16, 11]])
        for args, kwargs in calls['publish']:
            self.assertEqual(args, ('live', [1, 0, 0, 1]))
            self.assertEqual(kwargs, {'physical_pins': [15, 18, 16, 11], 'polarity_verified': False})

    def test_partial_setup_failure_cleans_only_owned_pins(self):
        calls, modules, stop, publisher = self.fixture(setup_failure=16)
        with patch.dict(sys.modules, modules), patch.object(hardware_worker, 'STOP', stop):
            with self.assertRaisesRegex(RuntimeError, 'SETUP_FAILURE'):
                hardware_worker.line_worker(publisher)
        self.assertEqual(calls['cleanup'], [[15, 18]])
        self.assertFalse(calls['publish'])

    def test_read_failure_still_releases_exactly_the_owned_pins(self):
        calls, modules, stop, publisher = self.fixture(input_failure=True)
        with patch.dict(sys.modules, modules), patch.object(hardware_worker, 'STOP', stop):
            with self.assertRaisesRegex(RuntimeError, 'INPUT_FAILURE'):
                hardware_worker.line_worker(publisher)
        self.assertEqual(calls['cleanup'], [[15, 18, 16, 11]])
        self.assertFalse(calls['wait'])

    def test_publisher_failure_does_not_leave_a_second_gpio_owner(self):
        calls, modules, stop, publisher = self.fixture(publish_failure=True)
        with patch.dict(sys.modules, modules), patch.object(hardware_worker, 'STOP', stop):
            with self.assertRaisesRegex(RuntimeError, 'PUBLISH_FAILURE'):
                hardware_worker.line_worker(publisher)
        self.assertEqual(calls['cleanup'], [[15, 18, 16, 11]])
        self.assertFalse(calls['wait'])


if __name__ == '__main__':
    unittest.main()
