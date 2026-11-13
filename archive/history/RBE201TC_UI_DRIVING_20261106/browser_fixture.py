"""SOFTWARE FIXTURE ONLY. Fake serial transport; never opens robot or real serial."""
import sys,time,threading,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'work/device-console-v2/backend'))
from motor_control import MotorControl
from server import create_server
class FakeSerial:
    def send(self,packet):
        with open(Path(__file__).parent/'fake-motor-packets.log','a') as f:f.write(repr(packet)+'\n')
    def close(self): pass
motor=MotorControl(FakeSerial(),mode='speed'); motor.start()
server=create_server('127.0.0.1',8877,motor=motor)
print('SOFTWARE FIXTURE http://127.0.0.1:8877; no hardware transport',flush=True)
server.serve_forever()
