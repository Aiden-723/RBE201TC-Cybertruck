"""One-shot passive Yahboom IMU verification; sends no device commands."""
from pathlib import Path
import subprocess,json
O=Path(__file__).resolve().parent
code=r'''import json,sys,time,struct,math,serial,base64
sys.path.insert(0,'/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106/backend')
from motor_control import _AtomicModemLines
class PassiveSerial(_AtomicModemLines,serial.Serial):pass
port=PassiveSerial(port=None,baudrate=115200,timeout=.05,exclusive=True);port.dtr=False;port.rts=False;port.port='/dev/rbe201tc-imu';port.open()
buf=bytearray();raw=bytearray();frames=[];bad=0;started=time.monotonic()
try:
 while time.monotonic()-started<12:
  chunk=port.read(4096);raw.extend(chunk);buf.extend(chunk);now=time.monotonic()
  while len(buf)>=5:
   j=buf.find(b'\x7e\x23')
   if j<0:buf[:]=buf[-1:];break
   if j:del buf[:j]
   if len(buf)<5:break
   n=buf[2]
   if not 5<=n<=64:del buf[0];bad+=1;continue
   if len(buf)<n:break
   f=bytes(buf[:n])
   if (sum(f[:-1])&255)!=f[-1]:del buf[0];bad+=1;continue
   del buf[:n];kind=f[3];x={'t':now-started,'kind':kind,'hex':f.hex()}
   if kind==0x26 and n==17:
    v=struct.unpack('<fff',f[4:-1]);x['rpy_rad']=list(v);x['rpy_deg']=[math.degrees(y) for y in v]
   elif kind==0x16 and n==21:
    q=struct.unpack('<ffff',f[4:-1]);x['quat_wxyz']=list(q);x['norm']=math.sqrt(sum(y*y for y in q));w,a,b,c=q
    x['rpy_deg']=[math.degrees(math.atan2(2*(w*a+b*c),1-2*(a*a+b*b))),math.degrees(math.asin(max(-1,min(1,2*(w*b-c*a))))),math.degrees(math.atan2(2*(w*c+a*b),1-2*(b*b+c*c)))]
   elif kind==4 and n==23:
    words=struct.unpack('<hhhhhhhhh',f[4:-1]);x['accel_g']=[v*16/32767 for v in words[:3]];x['gyro_deg_s']=[v*2000/32767 for v in words[3:6]];x['mag_raw']=list(words[6:])
   frames.append(x)
finally:port.close()
elapsed=time.monotonic()-started;e=[f for f in frames if f['kind']==0x26];q=[f for f in frames if f['kind']==0x16];a=[f for f in frames if f['kind']==4]
summary={'writes_sent':0,'device':'/dev/rbe201tc-imu','baud':115200,'elapsed_s':elapsed,'bytes':len(raw),'valid_frames':len(frames),'bad_candidates':bad,'euler_frames':len(e),'euler_receive_hz':len(e)/elapsed,'first_rpy_deg':e[0]['rpy_deg'] if e else None,'last_rpy_deg':e[-1]['rpy_deg'] if e else None,'yaw_min_deg':min(f['rpy_deg'][2] for f in e) if e else None,'yaw_max_deg':max(f['rpy_deg'][2] for f in e) if e else None,'quat_norm_min':min(f['norm'] for f in q) if q else None,'quat_norm_max':max(f['norm'] for f in q) if q else None,'last_gyro_deg_s':a[-1]['gyro_deg_s'] if a else None}
print(json.dumps({'summary':summary,'frames':frames,'raw_base64':base64.b64encode(raw).decode()}))
'''
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,timeout=22,check=True)
x=json.loads(r.stdout);(O/'imu_capture.json').write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');print(json.dumps(x['summary'],ensure_ascii=False,indent=2))
