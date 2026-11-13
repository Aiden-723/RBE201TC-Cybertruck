import base64
import json
import math
from pathlib import Path
import struct
import unittest
from imu_heading import ImuHeading, EulerDecoder


def frame(yaw):
    packet = bytearray(b'\x7e\x23\x11\x26') + struct.pack('<fff', 0, 0, math.radians(yaw))
    return bytes(packet + bytes([sum(packet) & 255]))


class HeadingTests(unittest.TestCase):
    def test_fragmented_corrupt_and_interleaved_packets(self):
        decoder = EulerDecoder()
        bad = bytearray(frame(50));bad[-1] ^= 1
        packet = b'noise' + bytes(bad) + frame(90) + frame(-179)
        got = []
        for i in range(0, len(packet), 3):
            got += decoder.feed(packet[i:i+3])
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[0][2], 90, places=4)
        self.assertAlmostEqual(got[1][2], -179, places=4)

    def test_zero_and_wrap_preserve_continuous_heading(self):
        imu = ImuHeading(clock=lambda: 1)
        imu.accept((0, 0, 170));self.assertFalse(imu.snapshot()['calibrated'])
        self.assertEqual(imu.calibrate()['heading_deg'], 0)
        for yaw in (179, -179, -100, -10, 0):imu.accept((0, 0, yaw))
        self.assertAlmostEqual(imu.snapshot()['heading_deg'], 190)
        imu.calibrate();self.assertEqual(imu.snapshot()['heading_deg'], 0)

    def test_stale_data_cannot_be_calibrated(self):
        now = [1.]
        imu = ImuHeading(clock=lambda: now[0]);imu.accept((0, 0, 0));now[0] += .5
        self.assertEqual(imu.snapshot()['status'], 'unavailable')
        with self.assertRaisesRegex(ValueError, 'imu_not_live'):imu.calibrate()

    def test_real_passive_capture_decodes_the_same_300_euler_packets(self):
        path = Path(__file__).resolve().parents[4] / 'outputs/RBE201TC_IMU_BRINGUP_20261113/imu_capture.json'
        if not path.exists():self.skipTest('original hardware capture is outside packaged source')
        data = json.loads(path.read_text())
        expected = [f['rpy_deg'] for f in data['frames'] if f['kind'] == 0x26]
        raw = base64.b64decode(data['raw_base64']);decoder = EulerDecoder();got = []
        for i in range(0, len(raw), 7):got.extend(decoder.feed(raw[i:i+7]))
        self.assertEqual(len(got), 300)
        for a, b in zip(got, expected):
            for x, y in zip(a, b):self.assertAlmostEqual(x, y, places=8)

class HeadingHTTPTests(unittest.TestCase):
    def test_read_and_authenticated_zero_never_arm_or_write_motor(self):
        import http.client
        import threading
        from motor_control import MotorControl
        from server import create_server
        class Wire:
            def __init__(self):self.packets=[]
            def send(self,packet):self.packets.append(packet)
            def close(self):pass
        wire=Wire();motor=MotorControl(wire,mode='speed');imu=ImuHeading();imu.accept((0,0,50))
        server=create_server('127.0.0.1',0,motor=motor,imu=imu)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            before=list(wire.packets);host,port=server.server_address
            def request(path,data=None,key=None):
                c=http.client.HTTPConnection(host,port,timeout=2)
                headers={'Origin':f'http://{host}:{port}','Content-Type':'application/json','X-Control-Key':key or server.control_key}
                c.request('POST' if data else 'GET',path,json.dumps(data) if data else None,headers)
                r=c.getresponse();status=r.status;payload=json.loads(r.read());c.close();return status,payload
            self.assertEqual(request('/api/v1/imu')[1]['data']['yaw_deg'],50)
            self.assertEqual(request('/api/v1/control',{'action':'imu_calibrate'},key='wrong')[0],403)
            imu.accept((0,0,50))
            status,reply=request('/api/v1/control',{'action':'imu_calibrate'})
            self.assertEqual(status,200);self.assertEqual(reply['data']['imu']['heading_deg'],0)
            self.assertFalse(reply['data']['armed']);self.assertEqual(wire.packets,before)
        finally:
            server.shutdown();server.server_close();thread.join(2);motor.close()
