from pathlib import Path
import ast,shutil,json
O=Path(__file__).resolve().parent;R=O.parents[1];P=R/'work/device-console-v2'
files=['backend/basic_line_follower.py','backend/tests/test_corner_line_follower.py','backend/tests/test_basic_line_integration.py','frontend/control.js','frontend/tests/corner-control.test.cjs','BASIC_LINE_FOLLOWING.md']
for name in files:
 d=O/'before'/name;d.parent.mkdir(parents=True,exist_ok=True);assert not d.exists();shutil.copy2(P/name,d)
def edit(name,old,new,count=1):
 f=P/name;s=f.read_text();assert s.count(old)==count,(name,old,s.count(old));f.write_text(s.replace(old,new))
def replace_method(name,method,body):
 f=P/name;s=f.read_text();ns=[n for n in ast.walk(ast.parse(s)) if isinstance(n,ast.FunctionDef) and n.name==method];assert len(ns)==1;node=ns[0];ls=s.splitlines(True);ls[node.lineno-1:node.end_lineno]=[body.rstrip()+'\n'];f.write_text(''.join(ls))
B=files[0];C=files[1];I=files[2]
edit(B,"        if hold_pattern == 'white' and self._left == self._right:\n            raise ValueError('line_lost')  # Never continue straight or reuse an old turn.\n",'')
edit(C,'class WhiteImmediateStopTests','class WhiteGapTests')
# After center, WHITE must retain the actual forward pair, never resurrect an earlier turn.
for old in ["        self.assert_stopped(self.step(UNKNOWN))  # Preserve the existing WHITE-after-straight stop.","        self.assert_stopped(self.step(UNKNOWN))  # WHITE after actual forward still stops.","        self.assert_stopped(self.step(WHITE))  # New center erases earlier turn authority.","        self.assert_stopped(self.step(WHITE))"]:
 replacement="        held=self.step(WHITE)\n        self.assertTrue(held['running']);self.assertEqual(self.pair(held),(50,50))\n        self.assertEqual(held['line_gap_limit_s'],.2)"
 edit(C,old,replacement)
replace_method(C,'test_white_after_straight_stops_and_each_correction_uses_its_fixed_limit','''    def test_white_after_every_visible_pair_uses_existing_point2_or_point4_limit(self):
        for visible in itertools.product((0,1),repeat=4):
            try:expected=steering(visible,50)[:2]
            except ValueError:continue
            with self.subTest(visible=visible):
                self.reset(first=visible);state=self.step(WHITE,dt=.03)
                self.assertEqual(state['last_values'],WHITE);self.assertEqual(state['last_raw_values'],WHITE)
                self.assertTrue(state['running']);self.assertEqual(self.pair(state),expected)
                limit=.4 if expected[0]*expected[1]<0 else .2
                self.assertEqual(state['line_gap_limit_s'],limit)
                state=self.step(WHITE,dt=limit+.001)
                self.assertEqual(state['reason'],'line_gap_timeout');self.assert_stopped(state)''')
# Add evidence-driven gap/reacquisition tests without changing any sensor-visible rule.
f=P/C;s=f.read_text();idx=s.index('    def test_latest_corner_inner_right_')
addition='''    def test_observed_center_to_white_survives_first_frame_and_reacquires_every_black_mask(self):
        for visible in itertools.product((0,1),repeat=4):
            if visible==tuple(WHITE):continue
            with self.subTest(reacquired=visible):
                self.reset(first=CENTER)
                state=self.step(WHITE,dt=.680)  # Observed previous-center to first-white interval.
                self.assertTrue(state['running']);self.assertEqual(self.pair(state),(50,50))
                anchor=self.worker._line_gap_started
                state=self.step(WHITE,dt=.10)
                self.assertEqual(self.worker._line_gap_started,anchor)
                self.assertEqual(self.pair(state),(50,50))
                state=self.step(visible,dt=.05)
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertEqual(self.pair(state),steering(visible,50)[:2])

    def test_forward_white_cache_keeps_original_deadline_and_explicit_stop_interrupts(self):
        self.reset(first=CENTER);self.step(WHITE,dt=.03)
        anchor=self.worker._line_gap_started
        state=self.step(dt=.199,fresh=False)
        self.assertTrue(state['running']);self.assertEqual(self.worker._line_gap_started,anchor)
        self.assert_stopped(self.step(dt=.002,fresh=False))
        self.reset(first=CENTER);self.step(WHITE);self.assert_stopped(self.worker.stop())

'''
s=s[:idx]+addition+s[idx:];f.write_text(s)
replace_method(I,'test_white_after_straight_stops_or_briefly_retains_exact_corrective_wire_pair','''    def test_white_after_forward_or_correction_retains_actual_wire_pair_then_times_out(self):
        for values in ([1,0,0,1],[1,0,1,1],[1,1,0,1],[0,1,1,1],[1,1,1,0]):
            with self.subTest(values=values):
                self.sample.update(value=values,monotonic=self.now,successes=self.sample['successes']+1)
                token=self.start();self.heartbeat(token);self.follower.tick()
                expected=self.motor.snapshot()['outputs'];before=len(self.wire.packets)
                state=self.line_step(token,[1,1,1,1],dt=.036)
                self.assertTrue(state['running']);self.assertEqual(self.motor.snapshot()['outputs'],expected)
                self.assertEqual(self.wire.packets[before:],[('$spd:'+','.join(map(str,expected))+'#').encode()])
                before=len(self.wire.packets);limit=.4 if expected[0]*expected[2]<0 else .2
                state=self.line_step(token,[1,1,1,1],dt=limit+.001)
                self.assertEqual(state['reason'],'line_gap_timeout');self.assertFalse(state['running'])
                self.assertFalse(self.motor.snapshot()['armed']);self.assertEqual(self.motor.snapshot()['outputs'],[0]*4)
                self.assertEqual(self.wire.packets[before:],[b'$pwm:0,0,0,0#',b'$spd:0,0,0,0#',b'$stop#'])
                before=len(self.wire.packets);self.sample.update(value=[1,0,0,1],successes=self.sample['successes']+1)
                self.follower.tick();self.assertEqual(len(self.wire.packets),before)
                with self.assertRaises(ValueError):self.heartbeat(token,999999)''')
# Keep the original regression's old-turn cancellation, now with forward gap before timeout.
edit(I,"([1,1,1,1],[-50,-50,50,50]),([1,0,0,1],[50]*4),([1,1,1,1],[0]*4))","([1,1,1,1],[-50,-50,50,50]),([1,0,0,1],[50]*4),([1,1,1,1],[50]*4),\n                ([1,1,1,1],[50]*4),([1,1,1,1],[50]*4),([1,1,1,1],[50]*4),([1,1,1,1],[50]*4),([1,1,1,1],[0]*4))")
edit(I,"self.assertFalse(state['running']);self.assertEqual(state['reason'],'line_lost')","self.assertFalse(state['running']);self.assertEqual(state['reason'],'line_gap_timeout')")
edit(I,"[1,0,0,1]),6)","[1,0,0,1]),11)")
# Honest UI during a forward gap, without adding controls or new mechanisms.
edit(files[3],'短暂丢线 · 延续刚才纠偏','短暂丢线 · 延续上一动作')
edit(files[3],'短丢线轻纠偏0.2秒／强纠偏0.4秒','短丢线直行及轻纠偏0.2秒／强纠偏0.4秒')
edit(files[4],'短暂丢线.*延续刚才纠偏','短暂丢线.*延续上一动作')
edit(files[4],'短丢线轻纠偏0\\.2秒／强纠偏0\\.4秒','短丢线直行及轻纠偏0\\.2秒／强纠偏0\\.4秒')
edit(files[5],'启动即白或上一动作直行则停车；上一动作轻纠偏最多保持原pair0.2秒','启动即白则停车；上一动作直行或轻纠偏最多保持原pair0.2秒')
# Reuse the reviewed transaction; new baseline and protected source hashes will be read fresh.
prepare=(R/'outputs/RBE201TC_ALL16_SOURCE_RULES_20261113/prepare_deploy.py').read_text()
prepare=prepare.replace("'backend/server.py','config/hardware_observations.json'","'backend/server.py','backend/tests/test_basic_line_follower.py','config/hardware_observations.json'")
(O/'prepare_deploy.py').write_text(prepare)
(O/'deploy_forward_gap.py').write_text((R/'outputs/RBE201TC_ALL16_SOURCE_RULES_20261113/deploy_all16.py').read_text())
verify=(R/'outputs/RBE201TC_ALL16_SOURCE_RULES_20261113/verify_live.py').read_text()
verify=verify.replace("assert '见线按官方组合规则纠偏' in js.decode()","assert '见线按官方组合规则纠偏' in js.decode()\nassert '短暂丢线 · 延续上一动作' in js.decode()\nassert '短丢线直行及轻纠偏0.2秒／强纠偏0.4秒' in js.decode()")
(O/'verify_live.py').write_text(verify)
print('Removed only first-WHITE-after-forward stop; kept startup WHITE stop, .2/.4 limits, all15 source actions, identity mapping and motor changes.')
