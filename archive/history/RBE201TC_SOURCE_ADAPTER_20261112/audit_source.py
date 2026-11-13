"""READ-ONLY policy audit. Parses only source IF expressions, never executes vendor code."""
from pathlib import Path
import ast,hashlib,itertools,json,re,sys
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
raw=(OUT/'github_tracking.py.txt').read_bytes();source=raw.decode()
variables=['TrackSensorLeftValue1','TrackSensorLeftValue2','TrackSensorRightValue1','TrackSensorRightValue2']
branches=[]
for match in re.finditer(r'^\s*(?:if|elif) (.+):\s*\n\s*(spin_right|spin_left|left|right|run)\((\d+),\s*(\d+)\)',source,re.M):
 expr,action,l,r=match.groups()
 if not any(v in expr for v in variables):continue
 tail=source[match.end():].lstrip();delay=.08 if tail.startswith('time.sleep(0.08)') else 0
 branches.append((ast.parse(expr,mode='eval').body,action,int(l),int(r),delay))
assert len(branches)==7
# An allowlisted evaluator for comparisons/booleans; no imports, function calls,
# vendor statements, GPIO access or eval/exec.
def condition(n,env):
 if isinstance(n,ast.Name):return env[n.id]
 if isinstance(n,ast.Constant):return n.value
 if isinstance(n,ast.BoolOp):
  vals=[condition(v,env) for v in n.values]
  if isinstance(n.op,ast.And):return all(vals)
  if isinstance(n.op,ast.Or):return any(vals)
 if isinstance(n,ast.Compare) and len(n.ops)==1 and isinstance(n.ops[0],ast.Eq):return condition(n.left,env)==condition(n.comparators[0],env)
 raise ValueError('Unsupported expression node '+type(n).__name__)
sys.path.insert(0,str(ROOT/'work/device-console-v2/backend'))
from basic_line_follower import steering
rows=[]
for bits in itertools.product((0,1),repeat=4):
 selected=None
 for expr,action,l,r,delay in branches:
  if condition(expr,dict(zip(variables,bits))):selected={'action':action,'original_PWM':[l,r],'hold_s':delay};break
 selected=selected or {'action':'retain_previous','original_PWM':None,'hold_s':0}
 try:l,r,band,error=steering(bits,84);current={'pair':[l,r],'band':band}
 except ValueError as e:current={'error':str(e)}
 action=selected['action'];equivalent={'spin_left':'hard_left','spin_right':'hard_right','left':'inner_left','right':'inner_right','run':'center'}.get(action)
 rows.append({'physical_input':list(bits),'github':selected,'current':current,'same_action':current.get('band')==equivalent if equivalent else False})
report={'commit':'e5a9e261c549c89c5421e48ebe6df1b5244d7229','source_sha256':hashlib.sha256(raw).hexdigest(),'source_conditions_checked':7,'input_patterns_checked':16,'unit_conversion':'No PWM percentage to spd conversion assumed','rows':rows,'retain':['physical X4 X3 X2 X1 mapping once','black0 floor1','continuous visible feedback and outgoing following','no unknown forward motion','manual hold/release/default200','owner heartbeat freshness lease and stop','CYBERTRUCK UI camera and CSV','atomic DTR/RTS repair'],'do_not_assume':['v7.1 physical success','PWM 35 equals speed35 or speed200','all changes after v6 should be reverted','short trace proves unique physical cause']}
(OUT/'sixteen_pattern_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
for row in rows:
 if not row['same_action'] or row['github']['hold_s']:
  print(''.join(map(str,row['physical_input'])),row['github'],'current',row['current'])
print('16 patterns checked. No project source changed, no robot commands sent.')
