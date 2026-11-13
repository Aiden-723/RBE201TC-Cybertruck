"""Passive Yahboom 7E23 Euler reader and a software-only heading origin."""
import math
import struct
import threading
import time


def angle_delta(angle):
    return (angle + 180.0) % 360.0 - 180.0


class EulerDecoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        result = []
        while len(self.buffer) >= 5:
            pos = self.buffer.find(b'\x7e\x23')
            if pos < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if pos:
                del self.buffer[:pos]
            if len(self.buffer) < 5:
                break
            length = self.buffer[2]
            if not 5 <= length <= 64:
                del self.buffer[0]
                continue
            if len(self.buffer) < length:
                break
            frame = bytes(self.buffer[:length])
            if sum(frame[:-1]) & 255 != frame[-1]:
                del self.buffer[0]
                continue
            del self.buffer[:length]
            if frame[3] == 0x26 and length == 17:
                angles = tuple(math.degrees(x) for x in struct.unpack('<fff', frame[4:-1]))
                if all(math.isfinite(x) for x in angles):
                    result.append(angles)
        return result


class ImuHeading:
    max_age = .4

    def __init__(self, port='/dev/rbe201tc-imu', *, left_sign=1, clock=time.monotonic, serial_factory=None):
        if type(left_sign) is not int or left_sign not in (-1, 1):
            raise ValueError('invalid_yaw_left_sign')
        self.port, self.left_sign, self.clock = port, left_sign, clock
        self.serial_factory = serial_factory
        self.lock = threading.RLock()
        self.exit = threading.Event()
        self.thread = None
        self.last_yaw = self.unwrapped = self.origin = self.sample_time = None
        self.samples = self.revision = 0
        self.error = 'imu_connecting'

    def accept(self, angles, now=None):
        with self.lock:
            yaw = angles[2]
            self.unwrapped = yaw if self.last_yaw is None else self.unwrapped + angle_delta(yaw - self.last_yaw)
            self.last_yaw = yaw
            self.sample_time = self.clock() if now is None else now
            self.samples += 1
            self.error = None

    def snapshot(self):
        with self.lock:
            age = None if self.sample_time is None else self.clock() - self.sample_time
            live = self.error is None and age is not None and 0 <= age <= self.max_age
            return dict(status='live' if live else 'unavailable', calibrated=self.origin is not None,
                        heading_deg=(self.unwrapped - self.origin) * self.left_sign if self.origin is not None else None,
                        yaw_deg=self.last_yaw, monotonic=self.sample_time, age_seconds=age,
                        successes=self.samples, revision=self.revision, error=self.error if not live else None)

    def calibrate(self):
        with self.lock:
            if self.snapshot()['status'] != 'live':
                raise ValueError('imu_not_live')
            self.origin = self.unwrapped
            self.revision += 1
            return self.snapshot()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='imu-heading', daemon=True)
            self.thread.start()

    def _open(self):
        if self.serial_factory:
            return self.serial_factory()
        import serial
        from motor_control import _AtomicModemLines
        class PassiveSerial(_AtomicModemLines, serial.Serial):
            pass
        port = PassiveSerial(port=None, baudrate=115200, timeout=.05, exclusive=True)
        port.dtr = False
        port.rts = False
        port.port = self.port
        port.open()
        return port

    def _run(self):
        while not self.exit.is_set():
            try:
                port = self._open()
                try:
                    decoder = EulerDecoder()
                    while not self.exit.is_set():
                        for angles in decoder.feed(port.read(min(4096, max(1, port.in_waiting)))):
                            self.accept(angles)
                finally:
                    port.close()
            except Exception:
                with self.lock:
                    self.error = 'imu_disconnected'
                    self.last_yaw = self.unwrapped = self.origin = self.sample_time = None
                    self.revision += 1
                self.exit.wait(1)

    def close(self):
        self.exit.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
