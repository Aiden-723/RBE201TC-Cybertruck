import importlib.util
import tempfile
from pathlib import Path
import unittest


class CollectorTests(unittest.TestCase):
    def collector(self, root):
        self.assertIsNotNone(importlib.util.find_spec('collectors'), 'collectors missing')
        from collectors import LinuxCollector
        # Canonicalize the fixture root: macOS /var resolves to /private/var,
        # matching the resolved synthetic sysfs device links. No production change.
        return LinuxCollector(root=Path(root).resolve())

    def test_system_linux_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path, value in {'proc/uptime': '123.45 9',
                                'proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 250 kB\nSecret: 999 kB',
                                'proc/loadavg': '0.1 0.2 0.3 1/20 456',
                                'sys/class/thermal/thermal_zone0/temp': '42000',
                                'sys/class/thermal/thermal_zone0/type': 'cpu'}.items():
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(value)
            result = self.collector(root).system()
            self.assertEqual(result['uptime_seconds']['value'], 123.45)
            self.assertEqual(result['memory_bytes']['value'], {'total': 1024000, 'available': 256000})
            self.assertEqual(result['load_average']['value'], [0.1, 0.2, 0.3])
            self.assertEqual(result['thermal']['value'][0]['celsius'], 42.0)
            self.assertGreater(result['disk_bytes']['value']['total'], 0)
            self.assertEqual(result['uptime_seconds']['evidence'], 'observed')

    def test_inventory_is_discovery_not_functionality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'dev/serial/by-id').mkdir(parents=True)
            (root / 'dev/ttyUSB0').touch()
            (root / 'dev/video0').touch()
            (root / 'dev/serial/by-id/usb-fixture').symlink_to('../../ttyUSB0')
            usb = root / 'sys/devices/usb1/1-1'
            usb.mkdir(parents=True)
            (usb / 'idVendor').write_text('1a86')
            (usb / 'idProduct').write_text('7523')
            tty = root / 'sys/class/tty/ttyUSB0'
            tty.mkdir(parents=True)
            (tty / 'device').symlink_to(usb)
            collector = self.collector(root)
            self.assertTrue(hasattr(collector, 'devices'), 'device inventory missing')
            result = collector.devices()
            self.assertEqual(result['serial']['value'][0]['by_id'], ['usb-fixture'])
            self.assertEqual(result['serial']['value'][0]['usb_vendor_id'], '1a86')
            self.assertEqual(result['video']['value'][0]['node'], '/dev/video0')
            self.assertEqual(result['camera']['status'], 'not_sampled')
            self.assertEqual(result['line_sensors']['physical_pins'], [15, 18, 16, 11])
            self.assertEqual(result['line_sensors']['status'], 'not_sampled')
            self.assertEqual(result['dht11']['linux_gpio'], 401)
            self.assertEqual(result['dht11']['evidence'], 'configured')
            self.assertEqual(result['motor']['functional_health'], 'unverified')

    def test_malformed_metrics_and_missing_inventory_are_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'proc').mkdir()
            (root / 'proc/loadavg').write_text('0.1')
            (root / 'proc/uptime').write_text('nan')
            collector = self.collector(root)
            self.assertEqual(collector.system()['load_average']['status'], 'unavailable')
            self.assertEqual(collector.system()['uptime_seconds']['status'], 'unavailable')
            self.assertEqual(collector.devices()['serial']['status'], 'unavailable')
            self.assertEqual(collector.devices()['video']['status'], 'unavailable')

    def test_missing_system_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.collector(Path(tmp)).system()
            self.assertIsNone(result['uptime_seconds']['value'])
            self.assertEqual(result['uptime_seconds']['status'], 'unavailable')
            self.assertEqual(result['thermal']['status'], 'unavailable')
