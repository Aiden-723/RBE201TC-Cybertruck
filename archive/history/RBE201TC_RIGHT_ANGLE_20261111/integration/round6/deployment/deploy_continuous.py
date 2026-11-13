"""Reviewed existing-unit transaction, fresh v4 baseline, continuous-v5 health guards."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,subprocess
QA=Path(__file__).resolve().parent

def program():
    spec=importlib.util.spec_from_file_location('reviewed_unit_transaction',QA.parent.parent/'round3/deploy_physical_order.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.QA=QA;m.FILES=list(json.loads((QA/'production_before.json').read_text())['before'])
    text,p=m.program()
    old="assert c['autonomy']['corner_max_seconds']==1.4 and c['autonomy']['period_s']==.05"
    new="assert c['autonomy']['control_policy']=='continuous_four_digital_v5' and c['autonomy']['period_s']==.05\n assert c['autonomy']['max_correction_speed']==200 and c['autonomy']['line_loss_limit_s']==1.4\n assert c['autonomy']['line_gap_active'] is False\n assert 'corner_phase' not in c['autonomy'] and 'corner_max_seconds' not in c['autonomy']"
    assert text.count(old)==1;text=text.replace(old,new,1)
    old="assert c['autonomy']['fallback_used'] is False";assert text.count(old)==1
    text=text.replace(old,"assert c['autonomy']['direction_recovery_enabled'] is False",1)
    assert p['files']['backend/basic_line_follower.py']['sha256']=='777c9a81c8e025f6ea28e2dbdf320e52edd29345a06cbf261efc92fe3aca6451'
    compile(text,'guarded_existing_unit_continuous_v5','exec');return text,p

def main():
    c=argparse.ArgumentParser(description=__doc__);g=c.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=c.parse_args()
    text,p=program();report={'program_sha256':hashlib.sha256(text.encode()).hexdigest(),'source_hashes':{n:v['sha256'] for n,v in p['files'].items()},'files':len(p['files']),'protected':len(p['protected']),'existing_service_only':True,'sampler_restarts':0,'motion_commands_sent_by_deployer':0,'policy':'continuous_four_digital_v5'}
    if a.check:
        with (QA/'deployment_dry_check.json').open('x') as f:json.dump(report,f,indent=2)
        print(json.dumps(report));return
    assert json.loads((QA/'deployment_dry_check.json').read_text())['program_sha256']==report['program_sha256'],'Source changed since checked payload'
    cmd=['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-']
    r=subprocess.run(cmd,input=text,capture_output=True,text=True,timeout=180)
    with (QA/'robot_deployment.log').open('x') as f:f.write(r.stdout+r.stderr+'\n[exit code: %d]\n'%r.returncode)
    print(r.stdout);print(r.stderr);print('[exit code: %d]'%r.returncode)
    if r.returncode:raise SystemExit(r.returncode)
    with (QA/'robot_deployment_result.json').open('x') as f:json.dump(json.loads(r.stdout),f,ensure_ascii=False,indent=2)
if __name__=='__main__':main()
