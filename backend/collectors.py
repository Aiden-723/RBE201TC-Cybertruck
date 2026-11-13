"""Read selected Linux pseudo-files. Never initialize any device."""
import itertools
import math
from pathlib import Path
import shutil


def observation(value, status='ok', source=None):
    return {'status': status, 'evidence': 'observed' if status == 'ok' else 'unverified',
            'source': source, 'value': value}


class LinuxCollector:
    def __init__(self, root=Path('/')):
        self.root = Path(root)

    def text(self, relative):
        with (self.root / relative).open('r', encoding='ascii', errors='replace') as stream:
            return stream.read(16384).strip()

    def paths(self, relative, pattern):
        return list(itertools.islice((self.root / relative).glob(pattern), 128))

    def measure(self, source, fn):
        try:
            return observation(fn(), source=source)
        except (OSError, ValueError, IndexError, KeyError):
            return observation(None, 'unavailable', source)

    def devices(self):
        serial = []
        aliases = {}
        for link in self.paths('dev/serial/by-id', '*'):
            try:
                aliases.setdefault(link.resolve().name, []).append(link.name)
            except (OSError, RuntimeError):
                continue
        nodes = self.paths('dev', 'ttyUSB*') + self.paths('dev', 'ttyACM*')
        for node in sorted(nodes, key=lambda p: p.name)[:128]:
            item = {'node': '/dev/' + node.name, 'by_id': sorted(aliases.get(node.name, [])),
                    'usb_vendor_id': None, 'usb_product_id': None,
                    'functional_health': 'unverified'}
            try:
                device = (self.root / 'sys/class/tty' / node.name / 'device').resolve()
                for parent in [device] + list(device.parents)[:12]:
                    if not parent.is_relative_to(self.root):
                        break
                    if (parent / 'idVendor').exists():
                        for field, filename in [('usb_vendor_id', 'idVendor'), ('usb_product_id', 'idProduct')]:
                            text = self.text(parent.relative_to(self.root) / filename)
                            if len(text) == 4 and all(c in '0123456789abcdefABCDEF' for c in text):
                                item[field] = text.lower()
                        break
            except (OSError, RuntimeError, ValueError):
                pass
            serial.append(item)
        video = [{'node': '/dev/' + p.name, 'functional_health': 'unverified'}
                 for p in sorted(self.paths('dev', 'video[0-9]*'), key=lambda p: p.name)]
        return {
            'serial': observation(serial if (self.root / 'dev').is_dir() else None,
                                  'ok' if (self.root / 'dev').is_dir() else 'unavailable', '/dev and /sys/class/tty'),
            'video': observation(video if (self.root / 'dev').is_dir() else None,
                                 'ok' if (self.root / 'dev').is_dir() else 'unavailable', '/dev/video*; enumeration only'),
            'camera': {'status': 'not_sampled', 'evidence': 'configured', 'interface': 'MIPI CSI1', 'value': None},
            'line_sensors': {'status': 'not_sampled', 'evidence': 'configured', 'physical_pins': [15, 18, 16, 11], 'value': None},
            'dht11': {'status': 'not_sampled', 'evidence': 'configured', 'linux_gpio': 401, 'value': None},
            'motor': {'status': 'unverified', 'evidence': 'configured', 'functional_health': 'unverified', 'encoder_validity': 'unverified'},
        }

    def system(self):
        def finite(value):
            result = float(value)
            if not math.isfinite(result) or result < 0:
                raise ValueError('invalid metric')
            return result

        def memory():
            fields = dict(line.split(':', 1) for line in self.text('proc/meminfo').splitlines() if ':' in line)
            result = {}
            for name, key in [('total', 'MemTotal'), ('available', 'MemAvailable')]:
                number, unit = fields[key].split()
                if unit != 'kB' or int(number) < 0:
                    raise ValueError('invalid memory')
                result[name] = int(number) * 1024
            return result

        def load():
            parts = self.text('proc/loadavg').split()[:3]
            if len(parts) != 3:
                raise ValueError('incomplete load averages')
            return [finite(x) for x in parts]

        def disk():
            value = shutil.disk_usage(self.root)
            return {'total': value.total, 'used': value.used, 'free': value.free}

        def thermal():
            values = []
            for zone in self.paths('sys/class/thermal', 'thermal_zone*'):
                try:
                    temperature = float(self.text(zone.relative_to(self.root) / 'temp')) / 1000
                    if not math.isfinite(temperature) or not -100 <= temperature <= 250:
                        continue
                    values.append({'zone': zone.name, 'celsius': temperature})
                except (OSError, ValueError):
                    continue
            if not values:
                raise ValueError('no thermal readings')
            return values

        return {
            'uptime_seconds': self.measure('/proc/uptime', lambda: finite(self.text('proc/uptime').split()[0])),
            'memory_bytes': self.measure('/proc/meminfo', memory),
            'load_average': self.measure('/proc/loadavg', load),
            'disk_bytes': self.measure('root filesystem', disk),
            'thermal': self.measure('/sys/class/thermal', thermal),
        }
