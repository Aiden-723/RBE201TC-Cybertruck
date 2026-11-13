"""Deploy the verified candidate only to an authenticated, idle RDK."""
from pathlib import Path
import argparse,base64,json,hashlib,subprocess
cli=argparse.ArgumentParser();cli.add_argument('--connection',default='rbe201tc-wifi');args=cli.parse_args()
o=Path(__file__).resolve().parent;r=o.parents[1];p=r/'work/device-console-v2';sha=lambda b:hashlib.sha256(b).hexdigest()
modified=['backend/track_imu_follower.py', 'backend/tests/test_track_imu_follower.py']
new=[]
manifest=json.loads((o/'manifest.json').read_text())
for n,h in manifest.items():assert sha((p/n).read_bytes())==h,('Candidate changed',n)
payload={'files':{n:base64.b64encode((p/n).read_bytes()).decode() for n in modified+new},'before':{n:sha((o/'before'/n).read_bytes()) for n in modified},'protected':json.loads((o/'protected_hashes.json').read_text())}
program='import json\nP=json.loads('+repr(json.dumps(payload))+')\n'+(o/'install_remote.py').read_text()
x=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4',args.connection,'python3','-B','-'],input=program,text=True,capture_output=True,timeout=95)
(o/'deployment.log').write_text(x.stdout+x.stderr+'\nexit='+str(x.returncode))
if x.returncode:print(x.stderr);raise SystemExit(x.returncode)
d=json.loads(x.stdout);(o/'deployment.json').write_text(json.dumps(d,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in d.items() if k not in ['protected_hashes','installed_hashes']},ensure_ascii=False,indent=2))
