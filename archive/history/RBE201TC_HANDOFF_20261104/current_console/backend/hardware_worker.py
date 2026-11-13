"""Explicit sensor acquisition, isolated from HTTP and from motor control.

Uses the supplied DHT11 helper and installed RDK libraries. No serial device is
opened. Each sensor owns a separate process so a native camera call cannot block
GPIO reads, DHT requests, or web requests.
"""
import argparse
from collections import deque
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time

STOP = threading.Event()


def atomic_bytes(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Publisher:
    def __init__(self, directory, name):
        self.directory, self.name = Path(directory), name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.successes = self.failures = 0
        self.last_good = None

    def send(self, status, value=None, error=None, **extra):
        if status == 'live':
            self.successes += 1
            self.last_good = {'value': value, 'monotonic': time.monotonic(),
                              'sampled_at': datetime.now().astimezone().isoformat()}
        elif status == 'error':
            self.failures += 1
        data = {'status': status, 'value': value, 'error': error,
                'monotonic': time.monotonic(),
                'sampled_at': datetime.now().astimezone().isoformat(),
                'successes': self.successes, 'failures': self.failures,
                'last_good': self.last_good, **extra}
        atomic_bytes(self.directory / (self.name + '.json'),
                     json.dumps(data, ensure_ascii=False, allow_nan=False).encode())


def parse_dht(line, point):
    fields = line.rstrip('\r\n').split('\t')
    if len(fields) < 4 or fields[:2] != ['DHT', str(point)]:
        raise ValueError('温湿度响应格式不正确')
    if fields[2] != 'OK':
        raise ValueError('传感器未返回有效读数：' + fields[3][:180])
    if len(fields) != 5:
        raise ValueError('温湿度响应缺少字段')
    humidity, temperature = float(fields[3]), float(fields[4])
    if not all(math.isfinite(x) for x in (humidity, temperature)):
        raise ValueError('读数不是有效数字')
    if not 0 <= humidity <= 100 or not -20 <= temperature <= 80:
        raise ValueError('读数超出可接受范围')
    return {'temperature_c': temperature, 'humidity_pct': humidity}


def line_worker(pub):
    import Hobot.GPIO as GPIO
    pins = [15, 18, 16, 11]
    configured = []
    GPIO.setmode(GPIO.BOARD)
    try:
        for pin in pins:
            GPIO.setup(pin, GPIO.IN)
            configured.append(pin)
        while not STOP.is_set():
            values = [int(GPIO.input(pin)) for pin in pins]
            pub.send('live', values, physical_pins=pins, polarity_verified=False)
            STOP.wait(0.15)
    finally:
        if configured:
            GPIO.cleanup(configured)


def dht_worker(pub, binary):
    if Path('/sys/class/gpio/gpio401').exists():
        raise RuntimeError('GPIO401已被其他程序导出；请先停止独立DHT11或循线程序。')
    process = subprocess.Popen(['chrt', '-f', '50', 'taskset', '-c', '7',
                                str(Path(binary).resolve()), '--server'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True, bufsize=1)
    history = deque(maxlen=80)
    try:
        point = 0
        while not STOP.is_set():
            point += 1
            try:
                if process.poll() is not None:
                    raise RuntimeError('DHT11采样程序已退出')
                process.stdin.write('READ ' + str(point) + '\n')
                process.stdin.flush()
                if not select.select([process.stdout], [], [], 2.0)[0]:
                    raise RuntimeError('DHT11读取超时')
                values = parse_dht(process.stdout.readline(), point)
                history.append({**values, 'monotonic': time.monotonic()})
                pub.send('live', values, history=list(history), linux_gpio=401)
            except (ValueError, OSError) as exc:
                pub.send('error', error=str(exc), history=list(history), linux_gpio=401)
            STOP.wait(2.5)
    finally:
        try:
            if process.poll() is None:
                process.stdin.write('QUIT\n')
                process.stdin.flush()
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def camera_worker(pub):
    import cv2
    import numpy as np
    from hobot_vio import libsrcampy
    camera = libsrcampy.Camera()
    width, height, frames = 640, 360, 0
    frame_times = deque(maxlen=80)
    target_fps = 20.0
    try:
        result = camera.open_cam(0, -1, 30, [width], [height], 1080, 1920)
        if result != 0:
            raise RuntimeError('摄像头初始化失败，返回值 ' + str(result))
        while not STOP.is_set():
            tick = time.monotonic()
            image = camera.get_img(2, width, height)
            if image is None or len(image) != width * height * 3 // 2:
                pub.send('error', error='未收到完整的相机画面')
                STOP.wait(0.5)
                continue
            nv12 = np.frombuffer(image, dtype=np.uint8).reshape(height * 3 // 2, width)
            bgr = cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
            ok, jpeg = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if not ok:
                raise RuntimeError('相机画面压缩失败')
            atomic_bytes(pub.directory / 'camera.jpg', jpeg.tobytes())
            frames += 1
            frame_times.append(time.monotonic())
            measured_fps = ((len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
                            if len(frame_times) > 1 else 0.0)
            pub.send('live', {'width': width, 'height': height, 'frames': frames,
                             'fps': round(measured_fps, 1), 'target_fps': target_fps},
                     interface='MIPI CSI1', pipeline=0)
            STOP.wait(max(0.0, 1.0 / target_fps - (time.monotonic() - tick)))
    finally:
        camera.close_cam()


def run_one(kind, directory, binary):
    pub = Publisher(directory, kind)
    lock_directory = Path('/run/lock/rbe201tc-console')
    lock_directory.mkdir(mode=0o755, exist_ok=True)
    fd = os.open(str(lock_directory / (kind + '.lock')),
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pub.send('starting')
        try:
            if kind == 'line_sensors':
                line_worker(pub)
            elif kind == 'dht11':
                dht_worker(pub, binary)
            else:
                camera_worker(pub)
        except Exception as exc:
            while not STOP.is_set():
                pub.send('error', error=str(exc)[:220])
                STOP.wait(3)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--kind', choices=['all', 'line_sensors', 'dht11', 'camera'], default='all')
    cli.add_argument('--state-dir', required=True)
    cli.add_argument('--dht-binary', required=True)
    args = cli.parse_args()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: STOP.set())
    if args.kind != 'all':
        run_one(args.kind, args.state_dir, args.dht_binary)
        return
    Path(args.state_dir).mkdir(parents=True, exist_ok=True)
    atomic_bytes(Path(args.state_dir) / 'supervisor.pid', (str(os.getpid()) + '\n').encode())
    processes = [subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()),
                 '--kind', kind, '--state-dir', args.state_dir,
                 '--dht-binary', args.dht_binary])
                 for kind in ('line_sensors', 'dht11', 'camera')]
    try:
        STOP.wait()
    finally:
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == '__main__':
    main()
