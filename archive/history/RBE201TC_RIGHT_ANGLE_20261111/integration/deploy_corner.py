"""Single-use corner update; reuses reviewed transaction, never starts driving."""
from pathlib import Path
import argparse
import base64
import hashlib
import importlib.util
import json
import subprocess

ROOT = Path(__file__).resolve().parents[3]
QA = Path(__file__).resolve().parent
PROJECT = ROOT/'work/device-console-v2'
FILES = ['backend/basic_line_follower.py', 'backend/line_transition_gate.py',
         'backend/hardware_worker.py', 'backend/tests/test_basic_line_follower.py',
         'backend/tests/test_corner_line_follower.py', 'backend/tests/test_line_transition_gate.py',
         'backend/tests/test_line_sampler_cadence.py', 'backend/tests/test_basic_line_integration.py', 'frontend/control.js',
         'frontend/tests/corner-control.test.cjs', 'BASIC_LINE_FOLLOWING.md']

SAMPLER_HELPER = r'''
def restart_samplers(expected_interval):
 global sampler_touched
 assert pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/readonly-current').resolve()==root
 service='rbe201tc-sensors.service'
 old_main=subprocess.run(['systemctl','show',service,'-p','MainPID','--value'],capture_output=True,text=True,check=True).stdout.strip()
 sampler_touched=True
 result=subprocess.run(['sudo','-n','/usr/bin/systemctl','restart',service],capture_output=True,text=True,timeout=25)
 assert result.returncode==0,('Sampler service restart failed',result.stderr[-1000:])
 new_main=subprocess.run(['systemctl','show',service,'-p','MainPID','--value'],capture_output=True,text=True,check=True).stdout.strip()
 assert new_main.isdigit() and int(new_main)>1 and new_main!=old_main
 directory=pathlib.Path('/dev/shm/rbe201tc-sensor-studio');assert directory.resolve()==directory
 deadline=time.monotonic()+16
 while time.monotonic()<deadline:
  try:
   snapshots={k:json.loads((directory/(k+'.json')).read_text()) for k in ('line_sensors','camera','dht11')}
   assert all(v.get('status')=='live' and 0<=time.monotonic()-v['monotonic']<8 for v in snapshots.values())
   break
  except (OSError,ValueError,KeyError,AssertionError):time.sleep(.1)
 else:raise RuntimeError('Existing sampler service did not recover all three sensors')
 samples=[];last=None;deadline=time.monotonic()+3
 while len(samples)<16 and time.monotonic()<deadline:
  item=json.loads((directory/'line_sensors.json').read_text())
  if item['successes']!=last:
   last=item['successes'];samples.append(item['monotonic'])
  time.sleep(.005)
 assert len(samples)>=5
 differences=sorted(b-a for a,b in zip(samples,samples[1:]));median=differences[len(differences)//2]
 assert .005<median<max(.06,expected_interval*1.8),('Unexpected actual line cadence',median)
 pids=subprocess.run(['pgrep','-f','[h]ardware_worker.py'],capture_output=True,text=True,check=True).stdout.split()
 line_workers=[]
 for text in pids:
  base=pathlib.Path('/proc')/text
  try:args=[a for a in (base/'cmdline').read_bytes().decode().split('\0') if a]
  except FileNotFoundError:continue
  if '--kind' in args and args[args.index('--kind')+1]=='line_sensors':line_workers.append(int(text))
 assert len(line_workers)==1,line_workers
 journal.setdefault('sampler_restarts',[]).append({'old_main_pid':int(old_main),'new_main_pid':int(new_main),'line_owner_pid':line_workers[0],'line_median_interval_s':median,'all_three_live':True})
 write_journal()
'''


def once(text, old, new):
    assert text.count(old)==1, old
    return text.replace(old,new)


def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--apply',action='store_true',required=True);cli.parse_args()
    prior=ROOT/'outputs/RBE201TC_BASIC_LINE_FOLLOWING_20261111/integration/deploy_basic_line.py'
    spec=importlib.util.spec_from_file_location('reviewed_basic_deployer',prior);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    remote=module.REMOTE
    remote=once(remote,'assert len(allowed)==10','assert len(allowed)==%d'%len(FILES))
    remote=once(remote,'new=None;installed=[]',SAMPLER_HELPER+'\nsampler_touched=False\nnew=None;installed=[]')
    remote=once(remote,"new=launch('new_server');", "restart_samplers(.02)\n new=launch('new_server');")
    remote=once(remote,"if not pathlib.Path('/proc/%d'%pid).exists():\n  old=launch('rollback_server');", "if sampler_touched:restart_samplers(.15)\n if not pathlib.Path('/proc/%d'%pid).exists():\n  old=launch('rollback_server');")
    remote=once(remote,'wait_healthy(old,False)','wait_healthy(old,True)')
    remote=once(remote,'c,a,s=wait_healthy(new,True)',"c,a,s=wait_healthy(new,True)\n assert c['autonomy']['period_s']==.05\n assert c['autonomy']['corner_max_seconds']==1.4")
    pre=json.loads((QA/'robot_preflight_for_deploy.json').read_text())
    baseline=json.loads((QA.parent/'source_baseline.json').read_text())
    for name in ('backend/basic_line_follower.py','backend/hardware_worker.py','frontend/control.js'):
        assert pre['hashes'][name]==baseline['before'][name]['sha256'], ('Unexpected live version',name)
    payload={'root':pre['root'],'pid':pre['pid'],'argv':pre['argv'],
             'before':{name:pre['hashes'][name] for name in ('backend/basic_line_follower.py','backend/hardware_worker.py','frontend/control.js')},
             'protected':{name:pre['hashes'][name] for name in ('backend/motor_control.py','backend/server.py','backend/driving_mode.py','backend/readings.py','backend/acquisition.py','frontend/app.js','frontend/index.html','frontend/styles.css')},
             'files':{n:{'content':base64.b64encode((PROJECT/n).read_bytes()).decode(),'sha256':hashlib.sha256((PROJECT/n).read_bytes()).hexdigest()} for n in FILES}}
    program='import json\nPAYLOAD=json.loads('+repr(json.dumps(payload))+')\n'+remote
    compile(program,'guarded_corner_deployment','exec')
    args=['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-']
    result=subprocess.run(args,input=program,capture_output=True,text=True,timeout=180)
    with (QA/'robot_corner_deployment.log').open('x') as f:f.write(result.stdout+result.stderr+'\n[exit code: %d]\n'%result.returncode)
    print(result.stdout);print(result.stderr);print('[exit code: %d]'%result.returncode)
    if result.returncode:raise SystemExit(result.returncode)
    with (QA/'robot_corner_deployment_result.json').open('x') as f:json.dump(json.loads(result.stdout),f,ensure_ascii=False,indent=2)


if __name__=='__main__':main()
