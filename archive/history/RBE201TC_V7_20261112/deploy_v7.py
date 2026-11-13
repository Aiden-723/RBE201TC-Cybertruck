"""Single-use v7 update using the reviewed original-systemd-unit transaction."""
from pathlib import Path
import importlib.util,json,hashlib,subprocess,argparse
QA=Path(__file__).resolve().parent
ROOT=QA.parents[1]
FILES=['backend/basic_line_follower.py','backend/tests/test_basic_line_follower.py','backend/tests/test_corner_line_follower.py','backend/tests/test_basic_line_integration.py','backend/tests/test_line_v7.py','frontend/control.js','frontend/tests/corner-control.test.cjs','BASIC_LINE_FOLLOWING.md']
def program():
 spec=importlib.util.spec_from_file_location('v3_transaction',ROOT/'outputs/RBE201TC_RIGHT_ANGLE_20261111/integration/round3/deploy_physical_order.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.QA=QA;m.FILES=FILES
 text,p=m.program()
 old="assert c['autonomy']['corner_max_seconds']==1.4 and c['autonomy']['period_s']==.05"
 new="assert c['autonomy']['control_policy']=='continuous_four_digital_v7' and c['autonomy']['period_s']==.02\n assert c['autonomy']['min_active_command']==80 and c['autonomy']['command_refresh_s']==.2\n assert c['autonomy']['max_correction_speed']==200 and c['autonomy']['line_loss_limit_s']==1.4\n assert c['autonomy']['line_gap_active'] is False and c['autonomy']['gap_motion'] is None"
 assert text.count(old)==1;text=text.replace(old,new)
 text=text.replace("assert c['autonomy']['fallback_used'] is False","assert c['autonomy']['direction_recovery_enabled'] is False")
 text=text.replace("not a.get('committing'),'Capture active'","not a.get('committing') and not a.get('closing'),'Capture active'")
 compile(text,'guarded_v7','exec');return text,p
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');a=parser.parse_args()
 text,p=program();digest=hashlib.sha256(text.encode()).hexdigest()
 report={'program_sha256':digest,'source_hashes':{n:x['sha256'] for n,x in p['files'].items()},'protected':p['protected'],'motion_commands_sent_by_deployer':0}
 if not a.apply:
  (QA/'deployment_dry_check.json').write_text(json.dumps(report,indent=2));print('Guarded payload checked',digest)
 else:
  assert json.loads((QA/'deployment_dry_check.json').read_text())['program_sha256']==digest
  args=['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-']
  r=subprocess.run(args,input=text,text=True,capture_output=True,timeout=180)
  (QA/'robot_deployment.log').write_text(r.stdout+r.stderr+'\nexit='+str(r.returncode))
  print(r.stdout,r.stderr);assert r.returncode==0
  (QA/'robot_deployment_result.json').write_text(json.dumps(json.loads(r.stdout),indent=2))
