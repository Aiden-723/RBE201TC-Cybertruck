"""SOFTWARE FIXTURE ONLY. Fake serial transport; never opens robot or real serial."""
import sys,time,threading,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'work/device-console-v2/backend'))
from motor_control import MotorControl
from server import create_server
class FakeSerial:
    def send(self,packet): pass
    def close(self): pass
motor=MotorControl(FakeSerial(),mode='speed'); motor.start()
server=create_server('127.0.0.1',8877,motor=motor)
def run():
    while True:
        for direction in ['forward','backward','left','right','forward_right','forward_left','backward_right','backward_left','m1']:
            try:
                session=motor.command({'action':'arm'})['session']
                for seq in range(1,61):
                    motor.command({'action':'drive','session':session,'seq':seq,'direction':direction,'speed':200});time.sleep(.1)
                motor.command({'action':'stop'});time.sleep(1)
            except Exception:time.sleep(1)
# Automatic movement disabled for manual browser interaction checks.
print('SOFTWARE FIXTURE http://127.0.0.1:8877; no hardware transport',flush=True)
server.serve_forever()
