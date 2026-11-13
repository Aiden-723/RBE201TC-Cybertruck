from pathlib import Path
import argparse,importlib.util,json,hashlib,subprocess
QA=Path(__file__).resolve().parent;ROOT=QA.parents[1]
PROFILE=r'''
profile_attempted=False
wanted_mode='speed'
wanted_type=1

def configure_motor_type(wanted, expected=None):
 import importlib.util
 spec=importlib.util.spec_from_file_location('owned_motor_transport',root/'backend/motor_control.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 port=argv[argv.index('--motor-port')+1]
 assert port=='/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
 transport=module.SerialPWM(port)
 try:
  for packet in (b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'):transport.send(packet)
  before=transport.probe()
  match=re.search(r'Motor_type:(\d+)',before['configuration'])
  assert match,'Driver profile query did not reply'
  if expected is not None:assert int(match.group(1))==expected,('Profile changed since read-only observation',before)
  if int(match.group(1))==wanted:return dict(before=before,after=before,write_sent=False)
  (backup/('motor_config_before_type%d.json'%wanted)).write_text(json.dumps(before))
  transport.serial.reset_input_buffer()
  transport.send(('$mtype:%d#'%wanted).encode())
  time.sleep(.5)
  acknowledgement=transport.serial.read(4096).decode('ascii',errors='replace')
  after=transport.probe()
  assert re.search(r'Motor_type:%d\b'%wanted,after['configuration']),('Profile read-back mismatch',after,acknowledgement)
  # Dead zone/PID/reduction/wheel geometry are not tuned in this change.
  for key in ('Dead_Zone','Pulse_Line','Pulse_Phase','wheel_diameter'):
   pattern=key+r':([^\r\n]+)'
   assert re.search(pattern,before['configuration']).group(1)==re.search(pattern,after['configuration']).group(1),(key,before,after)
  for packet in (b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'):transport.send(packet)
  return dict(before=before,after=after,acknowledgement=acknowledgement,write_sent=True)
 finally:transport.close()
'''
def once(s,old,new):
 assert s.count(old)==1,old
 return s.replace(old,new,1)
def program():
 spec=importlib.util.spec_from_file_location('reviewed_managed_deploy',ROOT/'outputs/RBE201TC_RIGHT_ANGLE_20261111/integration/round3/deploy_physical_order.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.QA=QA;m.FILES=list(json.loads((QA/'production_before.json').read_text())['before']);s,p=m.program()
 s=once(s,"assert '--motor-mode' in argv and argv[argv.index('--motor-mode')+1]=='speed'","assert '--motor-mode' in argv and argv[argv.index('--motor-mode')+1]=='pwm'")
 s=once(s,"for directory in ('backend','frontend','tools','config'):","for directory in ('backend','frontend','tools','config','deploy'):")
 s=once(s,"assert c['autonomy']['corner_max_seconds']==1.4 and c['autonomy']['period_s']==.05", "assert c['autonomy']['control_policy']=='continuous_four_digital_v6' and c['autonomy']['period_s']==.02\n assert c['autonomy']['max_correction_speed']==50 and c['autonomy']['line_loss_limit_s']==1.4\n assert c['command_mode']=='speed'\n assert re.search(r'Motor_type:1\\b',c['serial_diagnostics']['configuration'])")
 s=once(s,"assert c['autonomy']['fallback_used'] is False","assert c['autonomy']['direction_recovery_enabled'] is False")
 s=once(s,"not a.get('committing'),'Capture active'","not a.get('committing') and not a.get('closing'),'Capture active'")
 s=once(s,"def wait_healthy(process,expect_auto):",PROFILE+"\ndef wait_healthy(process,expect_auto):")
 s=once(s,"assert c.get('driving_mode')=='manual' and c.get('drive_commands_sent')==0", "assert c.get('driving_mode')=='manual' and c.get('drive_commands_sent')==0\n   assert c['command_mode']==wanted_mode\n   assert re.search(r'Motor_type:%d\\b'%wanted_type,c['serial_diagnostics']['configuration'])")
 s=once(s,"assert not pathlib.Path('/proc/%d'%pid).exists(),'Old process still owns production; no files installed'", "assert not pathlib.Path('/proc/%d'%pid).exists(),'Old process still owns production; no files installed'\n profile_attempted=True\n journal['motor_profile']=configure_motor_type(1,expected=4);write_journal()")
 s=once(s,"for name in reversed(installed):", "wanted_mode='pwm';wanted_type=4\n if profile_attempted:\n  journal['motor_profile_rollback']=configure_motor_type(4)\n for name in reversed(installed):")
 # Record the newly verified live command mode as part of the result.
 s=s.replace("('state','armed','outputs','fault','driving_mode','drive_commands_sent','autonomy')", "('state','armed','outputs','fault','driving_mode','drive_commands_sent','autonomy','command_mode','serial_diagnostics')")
 compile(s,'two_wire_pwm_deploy','exec');return s,p
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');a=parser.parse_args();s,p=program();digest=hashlib.sha256(s.encode()).hexdigest()
 if not a.apply:
  (QA/'deployment_dry_check.json').write_text(json.dumps({'program_sha256':digest,'files':{n:i['sha256'] for n,i in p['files'].items()},'nonzero_motion_sent':False,'motor_type_change':{'from':4,'to':1},'existing_service_only':True,'no_deadzone_or_PID_tuning':True},indent=2));print('Two-wire PWM deployment payload checked')
 else:
  assert json.loads((QA/'deployment_dry_check.json').read_text())['program_sha256']==digest
  run=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=s,text=True,capture_output=True,timeout=180)
  (QA/'robot_deployment.log').write_text(run.stdout+run.stderr+'\nexit='+str(run.returncode));print(run.stdout,run.stderr);assert run.returncode==0
  (QA/'robot_deployment_result.json').write_text(json.dumps(json.loads(run.stdout),indent=2))
