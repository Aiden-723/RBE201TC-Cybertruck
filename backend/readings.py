"""Bounded reads of sampler snapshots; the HTTP process never opens hardware."""
from datetime import datetime
import copy
import json
import os
from pathlib import Path
import socket
import time


LIMITS = {'line_sensors': 2.0, 'dht11': 9.0, 'camera': 3.0}


class SensorStore:
    def __init__(self, directory=None, clock=time.monotonic):
        self.directory = Path(directory) if directory else None
        self.clock = clock

    def one(self, name):
        result = {'status': 'disabled', 'value': None, 'age_seconds': None,
                  'error': None, 'evidence': 'unverified'}
        if self.directory is None:
            return result
        try:
            path = self.directory / (name + '.json')
            if path.is_symlink() or path.stat().st_size > 131072:
                raise ValueError('invalid sampler snapshot')
            item = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(item, dict) or item.get('status') not in ('live', 'error', 'starting'):
                raise ValueError('invalid sampler status')
            sampled = item['monotonic']
            if not isinstance(sampled, (int, float)) or not 0 <= sampled <= self.clock() + 1:
                raise ValueError('invalid sample clock')
            result = copy.deepcopy(item)
            result['age_seconds'] = max(0, self.clock() - sampled)
            result['evidence'] = 'observed' if item.get('status') == 'live' else 'unverified'
            if result['age_seconds'] > LIMITS[name]:
                result['status'] = 'stale'
                result['value'] = None
                result['error'] = '采样进程未及时更新，请检查设备连接或采样日志。'
            return result
        except (OSError, ValueError, KeyError, TypeError):
            result.update(status='starting', error='正在等待采样进程。')
            return result

    def snapshot(self):
        return {name: self.one(name) for name in LIMITS}

    def frame(self):
        packet = self.frame_packet()
        return packet[0] if packet else None

    def frame_packet(self):
        if self.directory is None or self.one('camera')['status'] != 'live':
            return None
        try:
            path = self.directory / 'camera.jpg'
            if path.is_symlink() or path.stat().st_size > 1048576:
                return None
            with path.open('rb') as stream:
                data = stream.read(1048577)
                stat = os.fstat(stream.fileno())
            if len(data) > 1048576 or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                return None
            # The inode belongs to the same immutable, atomically replaced JPEG
            # we just read, unlike a separately read sampler sequence number.
            return data, str(stat.st_mtime_ns) + '-' + str(stat.st_ino)
        except OSError:
            return None


def host_identity():
    model_path = Path('/proc/device-tree/model')
    try:
        model = model_path.read_bytes()[:256].rstrip(b'\0').decode('utf-8', errors='replace')
    except OSError:
        model = None
    now = datetime.now().astimezone()
    return {'hostname': socket.gethostname(), 'model': model,
            'is_rdk': bool(model and 'RDK X5' in model),
            'device_time': now.isoformat(), 'clock_valid': now.year >= 2025}
