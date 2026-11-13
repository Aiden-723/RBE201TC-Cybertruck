"""Reuse reviewed existing-unit transaction with this round's fresh baseline and v4 guards."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,subprocess
QA=Path(__file__).resolve().parent
ROOT=Path(__file__).resolve().parents[4]
FILES=['backend/basic_line_follower.py','backend/tests/test_basic_line_follower.py','backend/tests/test_corner_line_follower.py','backend/tests/test_basic_line_integration.py','frontend/control.js','frontend/tests/corner-control.test.cjs','BASIC_LINE_FOLLOWING.md']

def program():
    spec=importlib.util.spec_from_file_location('reviewed_unit_transaction',QA.parent/'round3/deploy_physical_order.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # Same allowlisted-root guards, candidate RDK tests, atomic install, backup,
    # graceful old-PID exit and ORIGINAL systemd-unit restart/rollback as v3.
    # Only these explicit fresh-round inputs and health markers differ.
    module.QA=QA;module.FILES=FILES
    rendered,payload=module.program()
    old="assert c['autonomy']['fallback_used'] is False"
    assert rendered.count(old)==1
    rendered=rendered.replace(old,old+"\n assert c['autonomy']['corner_speed']==200\n assert c['autonomy']['direction_recovery_enabled'] is False",1)
    compile(rendered,'guarded_turn200_no_direction_guess','exec')
    return rendered,payload

def main():
    cli=argparse.ArgumentParser(description=__doc__);g=cli.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=cli.parse_args()
    text,p=program();summary={'program_sha256':hashlib.sha256(text.encode()).hexdigest(),'source_hashes':{n:x['sha256'] for n,x in p['files'].items()},'files':len(FILES),'protected':len(p['protected']),'controller_service_only':True,'sampler_restarts':0,'motion_commands_sent_by_deployer':0}
    if a.check:
        with (QA/'deployment_dry_check.json').open('x') as f:json.dump(summary,f,indent=2)
        print(json.dumps(summary));return
    checked=json.loads((QA/'deployment_dry_check.json').read_text());assert checked['program_sha256']==summary['program_sha256'],'Payload changed since review/check'
    cmd=['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-']
    r=subprocess.run(cmd,input=text,capture_output=True,text=True,timeout=180)
    with (QA/'robot_deployment.log').open('x') as f:f.write(r.stdout+r.stderr+'\n[exit code: %d]\n'%r.returncode)
    print(r.stdout);print(r.stderr);print('[exit code: %d]'%r.returncode)
    if r.returncode:raise SystemExit(r.returncode)
    with (QA/'robot_deployment_result.json').open('x') as f:json.dump(json.loads(r.stdout),f,ensure_ascii=False,indent=2)

if __name__=='__main__':main()
