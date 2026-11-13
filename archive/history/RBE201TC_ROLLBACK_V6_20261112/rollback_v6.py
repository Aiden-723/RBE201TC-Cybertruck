"""Guarded exact-v6 rollback using existing-unit transaction; archive future code."""
from pathlib import Path
import json,importlib.util,hashlib,subprocess,argparse
QA=Path(__file__).resolve().parent;ROOT=QA.parents[1]
def program():
 manifest=json.loads((QA/'rollback_manifest.json').read_text())
 spec=importlib.util.spec_from_file_location('reviewed_transaction',ROOT/'outputs/RBE201TC_RIGHT_ANGLE_20261111/integration/round3/deploy_physical_order.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.QA=QA;m.FILES=manifest['files']
 text,p=m.program();p['deleted']=manifest['deleted']
 text='import json\nPAYLOAD=json.loads('+repr(json.dumps(p))+')\n'+text.split('\n',2)[2]
 old="assert c['autonomy']['corner_max_seconds']==1.4 and c['autonomy']['period_s']==.05";assert text.count(old)==1
 text=text.replace(old,"assert c['autonomy']['control_policy']=='continuous_four_digital_v6' and c['autonomy']['period_s']==.02\n assert c['autonomy']['max_correction_speed']==200 and c['autonomy']['line_loss_limit_s']==1.4\n assert 'source_commit' not in c['autonomy'] and 'min_active_command' not in c['autonomy']")
 text=text.replace("assert c['autonomy']['fallback_used'] is False","assert c['autonomy']['direction_recovery_enabled'] is False")
 text=text.replace("not a.get('committing'),'Capture active'","not a.get('committing') and not a.get('closing'),'Capture active'")
 text=text.replace("tests=subprocess.run(","for name,wanted in PAYLOAD['deleted'].items():\n p=target(stage,name);assert sha(p.read_bytes())==wanted;p.unlink()\ntests=subprocess.run(",1)
 text=text.replace('new=None;installed=[]',"new=None;installed=[];removed=[]\nfor name,wanted in PAYLOAD['deleted'].items():\n p=target(root,name);assert sha(p.read_bytes())==wanted\n b=target(backup,'removed/'+name);b.parent.mkdir(parents=True,exist_ok=True);b.write_bytes(p.read_bytes())")
 text=text.replace(" new=launch('new_server')"," for name,wanted in PAYLOAD['deleted'].items():\n  p=target(root,name);assert sha(p.read_bytes())==wanted;p.unlink();removed.append(name)\n new=launch('new_server')",1)
 text=text.replace(' for name in reversed(installed):'," for name in reversed(removed):\n  p=target(root,name);assert not p.exists(),('Foreign change',name)\n  b=target(backup,'removed/'+name);assert sha(b.read_bytes())==PAYLOAD['deleted'][name];os.replace(b,p)\n for name in reversed(installed):",1)
 text=text.replace("journal.update(result='success'", "assert all(not target(root,n).exists() for n in PAYLOAD['deleted'])\n journal.update(result='success'",1)
 compile(text,'rollback_exact_v6','exec');return text,p
if __name__=='__main__':
 cli=argparse.ArgumentParser();cli.add_argument('--apply',action='store_true');a=cli.parse_args();text,p=program();digest=hashlib.sha256(text.encode()).hexdigest()
 if not a.apply:
  (QA/'deployment_dry_check.json').write_text(json.dumps({'program_sha256':digest,'restored_hashes':{n:v['sha256'] for n,v in p['files'].items()},'archived_future_files':p['deleted']},indent=2));print('Exact-v6 rollback payload checked')
 else:
  assert json.loads((QA/'deployment_dry_check.json').read_text())['program_sha256']==digest
  r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=text,text=True,capture_output=True,timeout=180)
  (QA/'robot_rollback.log').write_text(r.stdout+r.stderr+'\nexit='+str(r.returncode));print(r.stdout,r.stderr);assert r.returncode==0
  (QA/'robot_rollback_result.json').write_text(json.dumps(json.loads(r.stdout),indent=2))
