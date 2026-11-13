"""Execute archived STM32 code, not an invented PID model; no device access."""
from pathlib import Path
import json,struct,hashlib
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_LR
ROOT=Path(__file__).resolve().parents[2]
blob=(ROOT/'outputs/RBE201TC_MOTOR_DIAG_20261110/original_flash_read1.bin').read_bytes()
assert hashlib.sha256(blob).hexdigest()=='edfb23cb0ff28450248d4355ae12cda3cf34248495c418dff646d949bd77e22b'
u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
u.mem_map(0x08000000,0x40000);u.mem_write(0x08000000,blob)
u.mem_map(0x20000000,0x10000);u.mem_map(0x40000000,0x30000)
regs=[UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3]
def put(addr,v):u.mem_write(addr,struct.pack('<f',v))
def get(addr):return struct.unpack('<f',u.mem_read(addr,4))[0]
def call(addr,*args):
 u.reg_write(UC_ARM_REG_SP,0x2000fff0);u.reg_write(UC_ARM_REG_LR,0x0803ff01)
 for r,v in zip(regs,args):u.reg_write(r,v&0xffffffff)
 u.emu_start(addr|1,0x0803ff00,count=100000)
 return u.reg_read(UC_ARM_REG_R0)
PID=0x200000e0
for i in range(4):
 p=PID+40*i
 for offset,v in [(12,.8),(16,.06),(20,.5)]:put(p+offset,v)
call(0x08003110,200,200,40,40)
print('targets',[get(PID+40*i) for i in range(4)])
rows=[]
for n in range(1,1201):
 for i in range(4):call(0x0800357c,PID+40*i,0)
 if n in [1,10,50,100,200,400,600,800,1000,1200]:rows.append({'pid_iterations':n,'stored_outputs':[get(PID+40*i+8) for i in range(4)]})
print('outputs',rows)
# A later positive speed change does not clear the stored saturated outputs.
call(0x08003110,40,40,200,200)
pre=[get(PID+40*i+8) for i in range(4)]
for i in range(4):call(0x0800357c,PID+40*i,0)
post=[get(PID+40*i+8) for i in range(4)]
# PWM clears the old loop and writes actual timer compare registers.
u.mem_write(0x2000007a,struct.pack('<H',1600));writes=[]
def hook(_u,access,addr,size,value,data):
 if 0x40000000<=addr<0x40030000:writes.append({'address':hex(addr),'size':size,'value':value})
u.hook_add(UC_HOOK_MEM_WRITE,hook)
call(0x080057f0);call(0x08003050,200,200,40,40)
result={'archive_sha256':hashlib.sha256(blob).hexdigest(),'execution':'real archived Thumb instructions under Unicorn; SRAM initialized from saved PID/deadzone settings; zero encoder feedback injected',
 'not_proven':'Current device flash equality, real timing, GPIO pin mux, clocks, voltage and wheel response are NOT emulated',
 'zero_feedback':rows,'after_switch_before_pid':pre,'after_switch_after_pid':post,'pwm_register_writes':list(writes),
 'pid_enabled_after_pwm':int.from_bytes(u.mem_read(0x20000058,1),'little')}
timer_pairs=[(0x40012c38,0x40012c3c),(0x40013434,0x40013438),(0x40012c40,0x40012c34),(0x4001343c,0x40013440)]
def pwm_registers():
 return [[int.from_bytes(u.mem_read(addr,4),'little') for addr in pair] for pair in timer_pairs]
# Repeat the exact PWM clear+apply path; the timer values must not accumulate.
for _ in range(1000):
 call(0x080057f0);call(0x08003050,200,200,40,40)
result['after_1000_pwm_commands']=pwm_registers()
assert [max(pair) for pair in result['after_1000_pwm_commands']]==[1800,1800,1640,1640]
call(0x080057f0);call(0x08003050,40,40,200,200)
result['opposite_arc_pwm_registers']=pwm_registers()
assert [max(pair) for pair in result['opposite_arc_pwm_registers']]==[1640,1640,1800,1800]
call(0x080057f0);call(0x08003050,0,0,0,0)
result['pwm_zero_stop_registers']=pwm_registers()
assert result['pwm_zero_stop_registers']==[[0,0]]*4
assert [get(PID+40*i+8) for i in range(4)]==[0]*4
assert int.from_bytes(u.mem_read(0x20000058,1),'little')==0
# Execute the archived command-body parser as well, not only its output helper.
# The UART ISR's $/# framing, UART electrical delivery and peripheral timing are
# outside this test: the already de-framed body is supplied in the RX buffer.
parser_cases=[]
for motor_type,values in [(1,(200,200,40,40)),(4,(200,200,40,40)),(4,(40,40,200,200)),(4,(0,0,0,0))]:
 payload=('pwm:'+','.join(map(str,values))).encode()+b'\0'
 u.mem_write(0x200002b0,bytes(100));u.mem_write(0x200002b0,payload)
 u.mem_write(0x20000078,bytes([motor_type]));u.reg_write(UC_ARM_REG_SP,0x2000f000)
 u.emu_start(0x08001c89,0x08001d64,count=200000)
 parsed=[struct.unpack('<i',u.mem_read(0x2000f004+4*i,4))[0] for i in range(4)]
 actual=[max(pair) for pair in pwm_registers()]
 expected=[1600+abs(v) if v else 0 for v in values]
 assert parsed==list(values) and actual==expected
 assert int.from_bytes(u.mem_read(0x20000058,1),'little')==0
 parser_cases.append({'motor_type':motor_type,'command_body':payload[:-1].decode(),'parsed':parsed,'timer_compare_magnitudes':actual})
result['uart_command_body_parse']=parser_cases
Path(__file__).with_name('firmware_emulation.json').write_text(json.dumps(result,indent=2))
assert all(v==2000 for v in pre)
assert post[0]>1700 and post[2]==2000
assert result['pid_enabled_after_pwm']==0
print('PASS: archived PID saturation, repeated PWM, opposite arc, PWM stop and four command-body parse cases; no hardware accessed')
