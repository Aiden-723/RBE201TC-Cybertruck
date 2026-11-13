"""Startup port selection uses device presence only; never opens a UART."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('monitor_startup',Path(__file__).resolve().parents[2]/'deploy/start_monitor.py')
startup=importlib.util.module_from_spec(spec);spec.loader.exec_module(startup)

class StartupTests(unittest.TestCase):
    def test_motor_uses_its_own_alias_instead_of_the_shared_usb_name(self):
        self.assertEqual(startup.PORT,'/dev/rbe201tc-motor')
        self.assertNotIn('by-id',startup.PORT)
    def test_real_character_device_is_passed_without_opening_or_moving_it(self):
        args=startup.arguments('/dev/null')
        self.assertEqual(args[args.index('--motor-port')+1],'/dev/null')
        self.assertEqual(args[args.index('--motor-mode')+1],'speed')
        self.assertEqual(args[args.index('--imu-port')+1],'/dev/rbe201tc-imu')
        self.assertEqual(args[args.index('--route-config')+1],'deploy/imu_route.json')
    def test_missing_port_or_regular_file_cannot_enable_motor_control(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'not-a-motor';p.write_text('fixture')
            for port in [str(p),str(p)+'-missing']:
                self.assertNotIn('--motor-port',startup.arguments(port))
