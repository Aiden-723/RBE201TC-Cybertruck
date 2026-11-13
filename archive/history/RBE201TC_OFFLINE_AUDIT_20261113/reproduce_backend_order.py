"""No hardware: demonstrate why two reordered manual requests can end in STOP."""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'work/device-console-v2/backend'))
from motor_control import MotorControl,ControlError
class Wire:
 def __init__(self):self.packets=[]
 def send(self,p):self.packets.append(p.decode())
 def close(self):pass
wire=Wire();m=MotorControl(wire,clock=lambda:1000,mode='speed');owner=m.command({'action':'arm'})['session']
def drive(seq,direction):return m.command(dict(action='drive',session=owner,seq=seq,direction=direction,speed=200))
drive(1,'forward');steering=drive(3,'forward_left')
try:drive(2,'forward')
except ControlError as e:reason=str(e)
else:raise AssertionError('expected stale sequence rejection')
# This is the deployed old frontend's catch branch after that rejected request.
stopped=m.command({'action':'stop'})
assert reason=='stale_command' and not stopped['armed'] and stopped['outputs']==[0]*4
result={'kind':'constructed ordering, not a recorded physical incident',
 'order':['forward seq1','new left seq3 arrives before old straight seq2','old seq2 -> stale_command','old client catch -> global STOP'],
 'before_rejection_outputs':steering['outputs'],'rejection':reason,'after_old_catch_outputs':stopped['outputs'],'after_old_catch_armed':stopped['armed']}
Path(__file__).with_name('backend_order_proof.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));m.close()
