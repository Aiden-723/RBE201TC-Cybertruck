"""Select the connected motor interface at service start; never command motion."""
import os
from pathlib import Path
import stat
import sys

PORT='/dev/rbe201tc-motor'

def arguments(port=PORT):
    args=[sys.executable,'-B','-u','backend/server.py','--host','127.0.0.1',
          '--port','8765','--sensor-state-dir','/dev/shm/rbe201tc-sensor-studio',
          '--experiment-dir','/home/sunrise/rbe201tc-sensor-studio/experiments']
    try:
        present=stat.S_ISCHR(Path(port).stat().st_mode)
    except FileNotFoundError:
        present=False
    if present:args+=['--motor-port',port,'--motor-mode','speed']
    args+=['--imu-port','/dev/rbe201tc-imu','--route-config','deploy/imu_route.json']
    return args

if __name__=='__main__':
    args=arguments()
    print('Monitor mode: '+('speed control' if '--motor-port' in args else 'sensor only'),flush=True)
    os.execv(sys.executable,args)
