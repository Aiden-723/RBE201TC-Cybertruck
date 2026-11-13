from pathlib import Path
import ast,hashlib,importlib.util,itertools,json,re
O=Path(__file__).resolve().parent;R=O.parents[1];P=R/'work/device-console-v2'
source=R/'outputs/RBE201TC_SOURCE_ADAPTER_20261112/github_tracking.py.txt'
text=source.read_text();predicates=[]
for expression,action in re.findall(r'^\s*(?:if|elif) (.*TrackSensor.*):\s*\n\s*(spin_right|spin_left|left|right|run)\(',text,re.M):
 tree=ast.parse(expression,mode='eval')
 assert all(isinstance(n,(ast.Expression,ast.BoolOp,ast.Or,ast.And,ast.Compare,ast.Eq,ast.Name,ast.Load,ast.Constant)) for n in ast.walk(tree))
 predicates.append((compile(tree,'vendor_predicate','eval'),action,expression))
assert len(predicates)==7,len(predicates)
names=('TrackSensorLeftValue1','TrackSensorLeftValue2','TrackSensorRightValue1','TrackSensorRightValue2')
labels={'spin_right':'hard_right','spin_left':'hard_left','left':'inner_left','right':'inner_right','run':'center'}
spec=importlib.util.spec_from_file_location('candidate_rules',P/'backend/basic_line_follower.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
old={tuple(r['physical_left_to_right']):r['before'] for r in json.loads((O/'before_16_patterns.json').read_text())}
rows=[]
for values in itertools.product((0,1),repeat=4):
 env=dict(zip(names,values));action=next((action for expr,action,_ in predicates if eval(expr,{'__builtins__':{}},env)),'hold_previous')
 try:out=m.steering(values,50);assert out[2]==labels[action];candidate=list(out)
 except ValueError as exc:
  assert values==(1,1,1,1) and action=='hold_previous' and str(exc)=='line_lost';candidate='existing_bounded_WHITE_policy'
 rows.append(dict(pattern=''.join(map(str,values)),before=old[values],vendor_action=action,candidate=candidate))
assert sum(isinstance(r['candidate'],list) for r in rows)==15
assert sum(r['before']=='ambiguous_line' for r in rows)==4
result=dict(source_url='https://github.com/YahboomTechnology/Tracking-module/blob/e5a9e261c549c89c5421e48ebe6df1b5244d7229/4.Raspberry%20Pi%20tutorials/tracking.py',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),predicates=[dict(expression=e,action=a) for _,a,e in predicates],rows=rows,all_fifteen_visible_actions_match=True,deliberate_adaptations=['BOARD pins and UART transport','all targets capped at 50 with existing 200/.2 startup assist','existing continuous v6 reads; vendor .08 sleep not added','WHITE retains existing .2 weak/.4 pivot limits, forward or initial WHITE stops'])
(O/'sixteen_pattern_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'nonwhite_matched':15,'previous_ambiguity_holes':4,'source_predicates':len(predicates)}))
