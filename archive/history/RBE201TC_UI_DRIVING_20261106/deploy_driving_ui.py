from pathlib import Path
import subprocess,tarfile,json,hashlib
ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/'work/device-console-v2'; OUT=Path(__file__).resolve().parent
SSH=['/usr/bin/ssh','-F',str(ROOT/'work/rdk_connection/autoboat_ssh_config'),'rbe201tc-wifi']
release='/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106'
files=[p for p in (SRC/'frontend').rglob('*') if p.is_file() and '__pycache__' not in str(p)]
files += [SRC/'backend/server.py',SRC/'backend/driving_mode.py',SRC/'backend/tests/test_static.py',SRC/'backend/tests/test_driving_mode.py']
archive=OUT/'driving-ui-patch.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
 for p in files:tar.add(p,arcname=str(p.relative_to(SRC)),recursive=False)
def run(code):
 r=subprocess.run(SSH+['python3','-'],input=code,text=True,capture_output=True,check=True)
 return r.stdout
before=run('''from pathlib import Path
import json,urllib.request,hashlib
base=Path('/home/sunrise/rbe201tc-sensor-studio')
def get(n):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+n,timeout=4))['data']
c=get('control');a=get('experiments')
assert not c['armed'] and not any(c['outputs']) and not c['fault']
assert not a['active_run_id'] and not a['busy'] and not a['committing'] and not a['closing']
data={str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (base/'experiments').rglob('*') if p.is_file()}
print(json.dumps({'previous_release':str((base/'readonly-current').resolve()),'data_hashes':data,'control':{k:c[k] for k in ['state','armed','outputs','fault']}}))
''')
(OUT/'pre-deploy.json').write_text(before)
previous=json.loads(before)['previous_release']
run(f'''from pathlib import Path
import shutil
src=Path({previous!r}); dst=Path({release!r})
assert not dst.exists(), 'release already exists'
shutil.copytree(src,dst,symlinks=True)
print('staged')
''')
subprocess.run(['/usr/bin/scp','-F',str(ROOT/'work/rdk_connection/autoboat_ssh_config'),str(archive),'rbe201tc-wifi:'+release+'/driving-ui-patch.tar.gz'],check=True,capture_output=True)
expected={str(p.relative_to(SRC)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
print(run(f'''from pathlib import Path
import tarfile,hashlib,json,subprocess
root=Path({release!r})
with tarfile.open(root/'driving-ui-patch.tar.gz') as tar:
 for m in tar.getmembers():
  assert not m.issym() and not m.islnk() and '..' not in Path(m.name).parts and not Path(m.name).is_absolute()
 tar.extractall(root)
expected={expected!r}
assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in expected.items())
r=subprocess.run(['python3','-B','-W','error','-m','unittest','discover','-s','tests','-v'],cwd=root/'backend',capture_output=True,text=True)
print(r.stdout+r.stderr)
assert r.returncode==0
'''))
print(run(f'''from pathlib import Path
import os,subprocess,json,urllib.request
base=Path('/home/sunrise/rbe201tc-sensor-studio')
def get(n):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+n,timeout=4))['data']
c=get('control');a=get('experiments')
assert not c['armed'] and not any(c['outputs']) and not c['fault']
assert not a['active_run_id'] and not a['busy'] and not a['committing'] and not a['closing']
subprocess.run(['sudo','-n','systemctl','stop','rbe201tc-monitor.service'],check=True)
tmp=base/'driving-ui-next';tmp.symlink_to({release!r});os.replace(tmp,base/'readonly-current')
subprocess.run(['sudo','-n','systemctl','start','rbe201tc-monitor.service'],check=True)
print('activated')
'''))
(OUT/'deployment.json').write_text(json.dumps({'release':release,'previous_release':previous,'file_hashes':expected},indent=2))
