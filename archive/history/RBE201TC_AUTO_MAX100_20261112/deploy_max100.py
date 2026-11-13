from pathlib import Path
import json,hashlib,importlib.util,subprocess,argparse
QA=Path(__file__).resolve().parent;ROOT=QA.parents[1]
def program():
 spec=importlib.util.spec_from_file_location('reviewed_unit_transaction',ROOT/'outputs/RBE201TC_RIGHT_ANGLE_20261111/integration/round3/deploy_physical_order.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.QA=QA;m.FILES=list(json.loads((QA/'production_before.json').read_text())['before']);text,p=m.program()
 old="assert c['autonomy']['corner_max_seconds']==1.4 and c['autonomy']['period_s']==.05";assert text.count(old)==1
 text=text.replace(old,"assert c['autonomy']['control_policy']=='continuous_four_digital_v6' and c['autonomy']['period_s']==.02\n assert c['autonomy']['max_correction_speed']==100 and c['autonomy']['line_loss_limit_s']==1.4")
 text=text.replace("assert c['autonomy']['fallback_used'] is False","assert c['autonomy']['direction_recovery_enabled'] is False")
 text=text.replace("not a.get('committing'),'Capture active'","not a.get('committing') and not a.get('closing'),'Capture active'")
 compile(text,'v6_output_fix','exec');return text,p
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');a=parser.parse_args();text,p=program();digest=hashlib.sha256(text.encode()).hexdigest()
 if not a.apply:
  (QA/'deployment_dry_check.json').write_text(json.dumps({'program_sha256':digest,'source_hashes':{n:x['sha256'] for n,x in p['files'].items()}},indent=2));print('V6 output-only payload checked')
 else:
  assert json.loads((QA/'deployment_dry_check.json').read_text())['program_sha256']==digest
  r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=text,text=True,capture_output=True,timeout=180)
  (QA/'robot_deployment.log').write_text(r.stdout+r.stderr+'\nexit='+str(r.returncode));print(r.stdout,r.stderr);assert r.returncode==0
  (QA/'robot_deployment_result.json').write_text(json.dumps(json.loads(r.stdout),indent=2))
