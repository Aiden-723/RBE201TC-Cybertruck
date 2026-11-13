"""Thread-safe observational state and explicitly opt-in passive serial reader."""
from datetime import datetime, timezone
import sys
import threading
import time

from parser import FrameParser
from readings import SensorStore, host_identity


class MonitorService:
    def __init__(self, collector, telemetry, sensors=None):
        self.collector = collector
        self.telemetry = telemetry
        self.sensors = sensors or SensorStore()

    def health(self):
        return {'status': 'ok', 'scope': 'backend_process_only', 'read_only': True,
                'capabilities': {'system_metrics': 'read_only', 'device_inventory': 'discovery_only',
                                 'serial_telemetry': 'optional_passive', 'actuation': 'unsupported',
                                 'camera_capture': 'snapshot_feed' if self.sensors.directory else 'disabled',
                                 'gpio_sampling': 'separate_worker' if self.sensors.directory else 'disabled'}}

    def get(self, endpoint):
        if endpoint == 'health':
            return self.health()
        if endpoint == 'system':
            return self.collector.system()
        if endpoint == 'devices':
            return self.device_snapshot(self.sensors.snapshot())
        if endpoint == 'status':
            sensors = self.sensors.snapshot()
            return {'health': self.health(), 'system': self.collector.system(),
                    'devices': self.device_snapshot(sensors), 'telemetry': self.telemetry.snapshot(),
                    'sensors': sensors, 'host': host_identity()}
        raise KeyError(endpoint)

    def device_snapshot(self, sensors):
        devices = self.collector.devices()
        if self.sensors.directory:
            for name in ('camera', 'line_sensors', 'dht11'):
                sample = sensors[name]
                devices[name].update(status='ok' if sample['status'] == 'live' else 'unavailable',
                                     evidence=sample['evidence'], value=sample.get('value'),
                                     sampling_status=sample['status'])
        return devices



class PassiveSerial:
    """No command writes, no reconnect loop, no port open without explicit opt-in."""
    def __init__(self, port, telemetry, serial_factory=None):
        self.port = port
        self.telemetry = telemetry
        self.serial_factory = serial_factory
        self.serial = None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name='passive-serial', daemon=False)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            factory = self.serial_factory
            if factory is None:
                from serial import Serial
                factory = Serial
                if sys.platform.startswith('linux'):
                    from motor_control import _AtomicModemLines

                    class AtomicSerial(_AtomicModemLines, Serial):
                        pass

                    factory = AtomicSerial
            self.serial = factory(port=None, baudrate=115200, timeout=0.2,
                                  exclusive=True, xonxoff=False, rtscts=False, dsrdtr=False)
            self.serial.dtr = False
            self.serial.rts = False
            self.serial.port = self.port
            self.serial.open()
            self.telemetry.connect()
            while not self.stopping.is_set():
                data = self.serial.read(512)
                if data:
                    self.telemetry.ingest(data)
        except (TypeError, AttributeError):
            self.telemetry.disconnect('serial_configuration_unsupported')
        except ImportError:
            self.telemetry.disconnect('pyserial_unavailable')
        except (OSError, ValueError):
            self.telemetry.disconnect('serial_open_or_read_error')
        finally:
            if self.serial is not None:
                try:
                    self.serial.close()
                except OSError:
                    pass
            if self.stopping.is_set():
                self.telemetry.disconnect('stopped')

    def stop(self):
        self.stopping.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise RuntimeError('serial reader did not stop within timeout')


class Telemetry:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.parser = FrameParser()
        self.connection = 'disabled'
        self.counts = self.last = self.received_at = self.error = None

    def connect(self):
        with self.lock:
            self.connection = 'connected'
            self.counts = self.last = self.received_at = self.error = None
            self.parser = FrameParser()

    def disconnect(self, reason):
        with self.lock:
            self.connection = 'disconnected'
            self.error = reason

    def ingest(self, data):
        with self.lock:
            for counts in self.parser.feed(data):
                self.counts = list(counts)
                self.last = self.clock()
                self.received_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self):
        with self.lock:
            age = None if self.last is None else max(0, self.clock() - self.last)
            status = self.connection
            if status == 'connected':
                status = 'not_sampled' if age is None else ('stale' if age > 2.0 else 'observed')
            return {'status': status, 'connection': self.connection,
                    'evidence': 'observed' if self.counts is not None else 'unverified',
                    'raw_counts': None if self.counts is None else list(self.counts),
                    'received_at': self.received_at, 'age_seconds': age,
                    'stale_after_seconds': 2.0, 'error': self.error,
                    'encoder_validity': 'unverified', 'functional_health': 'unverified',
                    'units': 'raw_counts', 'interpretation': 'unvalidated'}
