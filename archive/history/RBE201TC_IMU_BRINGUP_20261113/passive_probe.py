import json,sys,time,struct,pathlib,glob,serial
sys.path.insert(0,'/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106/backend')
from motor_control import _AtomicModemLines
class PassiveSerial(_AtomicModemLines,serial.Serial):pass
results=[]
for path in sorted(glob.glob('/dev/serial/by-path/*')):
 for baud in [115200,9600,57600]:
  port=None
  try:
   port=PassiveSerial(port=None,baudrate=baud,timeout=.05,exclusive=True)
   port.dtr=False;port.rts=False;port.port=path;port.open()
   data=bytearray();end=time.monotonic()+1.1
   while time.monotonic()<end:data.extend(port.read(4096))
   frames=[];i=0
   while i+11<=len(data):
    if data[i]==0x55 and (sum(data[i:i+10])&255)==data[i+10]:
     f=bytes(data[i:i+11]);frames.append({'type':hex(f[1]),'raw':f.hex(),'words':list(struct.unpack('<hhhh',f[2:10]))});i+=11
    else:i+=1
   item={'path':path,'device':str(pathlib.Path(path).resolve()),'baud':baud,'bytes':len(data),'valid_wit_frames':len(frames),'types':sorted(set(f['type'] for f in frames)),'first_bytes':bytes(data[:66]).hex(),'examples':frames[:8]};results.append(item)
   if len(frames)>=5:break
  except Exception as e:results.append({'path':path,'baud':baud,'error':type(e).__name__+': '+str(e)})
  finally:
   if port is not None and port.is_open:port.close()
print(json.dumps({'passive_only':True,'writes_sent':0,'results':results}))
