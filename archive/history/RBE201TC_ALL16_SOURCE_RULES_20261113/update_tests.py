from pathlib import Path
import ast
P=Path(__file__).resolve().parents[2]/'work/device-console-v2'
def replace_method(name, method, body):
 f=P/name;s=f.read_text();tree=ast.parse(s);nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==method];assert len(nodes)==1,method;n=nodes[0];lines=s.splitlines(True);lines[n.lineno-1:n.end_lineno]=[body.rstrip()+'\n'];f.write_text(''.join(lines))
def edit(name,old,new,count=1):
 f=P/name;s=f.read_text();assert s.count(old)==count,(name,old,s.count(old));f.write_text(s.replace(old,new))
B='backend/tests/test_basic_line_follower.py';C='backend/tests/test_corner_line_follower.py';I='backend/tests/test_basic_line_integration.py'
edit(B,"        expected = {\n            (0, 0, 0, 1)","        expected = {\n            (0, 0, 0, 0): (50, -50, 'hard_right'),\n            (0, 0, 1, 0): (50, -50, 'hard_right'),\n            (0, 1, 0, 0): (50, -50, 'hard_right'),\n            (0, 1, 1, 0): (50, -50, 'hard_right'),\n            (0, 0, 0, 1)")
edit(B,'test_all_sixteen_patterns_have_explicit_outputs_or_conservative_stop','test_all_sixteen_patterns_have_fifteen_source_actions_and_white_policy')
replace_method(B,'test_all_black_and_both_outers_fail_shut_not_station_trigger','''    def test_all_black_and_both_outers_use_vendor_right_priority(self):
        for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
            self.assertEqual(steering(values,220),(50,-50,'hard_right',3.0))''')
replace_method(B,'test_all_black_stops_and_never_implies_station_or_sample_request','''    def test_all_black_runs_source_corner_rule_without_station_or_sample_request(self):
        self.active();self.feed.item['value']=[0,0,0,0]
        state=self.worker.tick()
        self.assertTrue(state['running']);self.assertEqual((state['left'],state['right']),(50,-50))
        self.assertFalse(state['station_detection']);self.assertFalse(state['line_gap_active'])''')
replace_method(B,'test_all_both_outer_patterns_stop_conservatively','''    def test_all_both_outer_patterns_start_without_prior_motion(self):
        for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
            with self.subTest(values=values):
                self.worker.stop();self.motor.drives.clear();self.active();self.feed.item['value']=values
                state=self.worker.tick()
                self.assertTrue(state['running']);self.assertEqual(state['reason'],'hard_right')
                self.assertEqual(self.motor.drives[-1][1:],(50,-50))
                self.assertFalse(state['line_gap_active'])''')
edit(C,'UNKNOWN = [0, 0, 0, 0]','UNKNOWN = [1, 1, 1, 1]  # Only all-WHITE needs the existing gap policy.')
edit(C,"        self.assertEqual(self.pair(self.step(UNKNOWN)), (50, 50))","        self.assert_stopped(self.step(UNKNOWN))  # WHITE after actual forward still stops.")
edit(C,"        held = self.step(UNKNOWN)\n        self.assertTrue(held['line_gap_active'])\n        self.assertEqual(self.pair(held), (50, 50))  # Preserve actual fresh forward, not an old spin.\n        self.assertFalse(any(left == right == 0 for _, left, right in self.motor.drives))","        self.assert_stopped(self.step(UNKNOWN))  # Preserve the existing WHITE-after-straight stop.")
edit(C,'state = self.step(UNKNOWN, dt=.1, fresh=fresh)','state = self.step(UNKNOWN, dt=.05, fresh=fresh)')
edit(C,"for _ in range(3): self.assertTrue(self.step(UNKNOWN, dt=.35)['running'])","for _ in range(3): self.assertTrue(self.step(UNKNOWN, dt=.1)['running'])")
edit(C,'state = self.step(UNKNOWN, dt=.351)','state = self.step(UNKNOWN, dt=.101)')
edit(C,"self.assertGreaterEqual(state['line_gap_elapsed_s'], 1.4)","self.assertGreaterEqual(state['line_gap_elapsed_s'], .4)",2)
edit(C,'for _ in range(3): self.step(UNKNOWN, dt=.35)','for _ in range(3): self.step(UNKNOWN, dt=.08)',2)
edit(C,'state = self.step([1, 0, 1, 1], dt=.2)','state = self.step([1, 0, 1, 1], dt=.04)')
replace_method(C,'test_both_outer_and_all_black_retain_actual_pair_without_resetting_gap','''    def test_both_outer_and_all_black_override_gap_with_source_priority(self):
        for previous in (LEFT,[1,0,1,1],RIGHT):
            for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
                self.reset(first=previous);self.step(WHITE)
                state=self.step(values)
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertIsNone(state['hold_pattern']);self.assertIsNone(self.worker._line_gap_started)
                self.assertEqual(state['last_visible_values'],values)
                self.assertEqual(self.pair(state),(50,-50));self.assertEqual(state['reason'],'hard_right')''')
replace_method(C,'test_slow_gap_write_cannot_move_first_ambiguous_timeout_anchor','''    def test_slow_white_write_cannot_move_first_gap_timeout_anchor(self):
        self.stage('gap');self.step(WHITE,dt=.25)
        started=self.worker._line_gap_started;original=self.motor.drive_sides
        def delayed(*args):
            self.clock.now+=.15;return original(*args)
        self.motor.drive_sides=delayed
        state=self.step(WHITE,dt=.01)
        self.assertEqual(state['reason'],'line_gap_timeout')
        self.assertEqual(self.worker._line_gap_started,started);self.assert_stopped(state)''')
replace_method(C,'test_new_start_discards_motion_authority_but_preserves_fault_diagnostics','''    def test_new_start_discards_white_hold_authority_but_preserves_fault_diagnostics(self):
        self.reset();self.step(RIGHT);self.step(WHITE);self.clock.now+=.401
        failure=self.worker.tick()['last_failure'];self.assertEqual(failure['reason'],'stale_line_sample')
        self.worker.stop();self.owner=self.worker.start(speed=84);self.seq=0;self.worker.heartbeat(self.owner,0)
        state=self.worker.snapshot();self.assertEqual(state['last_failure'],failure)
        self.assertEqual(state['last_values'],WHITE);self.assertEqual(state['last_visible_values'],RIGHT)
        self.assertFalse(state['hold_authorized']);self.assert_stopped(self.step(WHITE))''')
replace_method(C,'test_first_valid_ambiguous_or_ambiguous_raw_saved_before_policy_failure','''    def test_white_start_saves_raw_before_failure_and_all_other_masks_start(self):
        state=self.reset(first=WHITE)
        self.assertEqual(state['last_values'],WHITE);self.assertEqual(state['last_failure']['values'],WHITE)
        self.assert_stopped(state)
        for values in itertools.product((0,1),repeat=4):
            if values==tuple(WHITE):continue
            state=self.reset(first=values)
            self.assertTrue(state['running']);self.assertEqual(self.pair(state),steering(values,50)[:2])
            self.assertFalse(state['line_gap_active'])''')
replace_method(C,'test_real_stop_shapes_now_hold_actual_pair_with_sample_timing_diagnostics','''    def test_previously_rejected_masks_have_current_actions_and_sample_diagnostics(self):
        for previous in (CENTER,LEFT,RIGHT,[1,1,0,1]):
            for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
                self.reset(first=previous);state=self.step(values,dt=.013)
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertEqual(self.pair(state),(50,-50));self.assertEqual(state['last_visible_values'],values)
                for key,source in [('sample_monotonic','monotonic'),('sample_count','successes')]:
                    self.assertEqual(state[key],self.feed.item[source])
                    self.assertEqual(state['transition_trace'][-1][key],state[key])
                cache=self.step(dt=.01,fresh=False)
                self.assertEqual(cache['sample_count'],state['sample_count']);self.assertEqual(self.pair(cache),(50,-50))''')
replace_method(C,'test_ambiguous_both_outer_switches_and_caches_share_one_unknown_budget','''    def test_all_both_outer_masks_remain_visible_beyond_old_ambiguity_budget(self):
        self.reset(first=LEFT)
        for index in range(40):
            values=([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0])[index%4]
            state=self.step(values,dt=.05)
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.pair(state),(50,-50));self.assertEqual(state['last_visible_values'],values)
            self.assertTrue(self.step(dt=.005,fresh=False)['running'])''')
# Names now describe the WHITE-only gap fixture rather than the removed ambiguity behavior.
f=P/C;s=f.read_text().replace('continuous_fresh_ambiguous_hits_1point4_limit','continuous_fresh_white_hits_point4_limit').replace('first_ambiguous','first_white').replace('fresh_ambiguous','fresh_white').replace('latest_ambiguous','latest_white').replace('white_and_both_outer_switching','repeated_white_and_cache').replace('not_extended_by_ambiguous_or_cache','not_extended_by_white_or_cache');f.write_text(s)
replace_method(I,'test_real_forward_pair_survives_ambiguous_then_times_out_without_auto_resume','''    def test_all_black_replaces_forward_and_continues_past_old_ambiguity_timeout(self):
        token=self.start();self.heartbeat(token);self.follower.tick()
        for _ in range(80):
            state=self.line_step(token,[0,0,0,0])
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])
        self.mode.command(dict(action='stop'));before=self.motor.snapshot()['drive_commands_sent']
        self.sample.update(value=[1,0,0,1],successes=self.sample['successes']+1);self.follower.tick()
        self.assertEqual(before,self.motor.snapshot()['drive_commands_sent'])
        with self.assertRaises(ValueError):self.heartbeat(token,1000)''')
# Migrate only the actual gap tests from full-black to WHITE; full-black is now visible.
for method in ['test_outer_gap_inner_opposite_and_center_replace_each_other_immediately','test_gap_holds_last_real_inner_pair_not_a_new_hard_turn','test_actual_weak_left_ambiguous_keeps_real_pair_without_upgrading_to_pivot']:
 f=P/I;s=f.read_text();tree=ast.parse(s);n=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==method);ls=s.splitlines(True);part=''.join(ls[n.lineno-1:n.end_lineno]).replace('[0,0,0,0]','[1,1,1,1]').replace('_ambiguous_','_white_');ls[n.lineno-1:n.end_lineno]=[part];f.write_text(''.join(ls))
replace_method(I,'test_first_reacquired_center_drives_forward_without_waiting_for_confirmation','''    def test_first_center_overrides_white_and_next_all_black_uses_source_rule(self):
        token=self.start();self.heartbeat(token);self.follower.tick()
        self.line_step(token,[1,0,0,0]);self.line_step(token,[1,1,1,1])
        state=self.line_step(token,[1,0,0,1]);self.assertTrue(state['running'])
        self.assertFalse(state['line_gap_active']);self.assertEqual(self.motor.snapshot()['outputs'],[50]*4)
        state=self.line_step(token,[0,0,0,0]);self.assertTrue(state['running'])
        self.assertFalse(state['line_gap_active']);self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])''')
replace_method(I,'test_ambiguous_and_full_black_share_one_unlocated_budget','''    def test_all_four_previously_unlocated_masks_reach_uart_without_timeout(self):
        token=self.start();self.heartbeat(token);self.follower.tick();self.line_step(token,[0,1,1,1])
        for index in range(80):
            values=([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0])[index%4]
            state=self.line_step(token,values)
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])''')
edit(I,"state = self.line_step(token,[0,0,0,0])\n        self.assertTrue(state['running']); self.assertTrue(state['line_gap_active'])","state = self.line_step(token,[0,0,0,0])\n        self.assertTrue(state['running']); self.assertFalse(state['line_gap_active'])")
edit(I,"([1,0,0,0], [50,50,-50,-50]))","([1,0,0,0], [50,50,-50,-50]),\n                 ([0,0,0,0], [50,50,-50,-50]),\n                 ([0,0,1,0], [50,50,-50,-50]),\n                 ([0,1,0,0], [50,50,-50,-50]),\n                 ([0,1,1,0], [50,50,-50,-50]),\n                 ([0,0,1,1], [-50,-50,50,50]),\n                 ([0,1,0,1], [-50,-50,50,50]),\n                 ([1,0,0,1], [50,50,50,50]),\n                 ([1,0,1,0], [50,50,-50,-50]),\n                 ([1,1,0,0], [50,50,-50,-50]))")
print('Updated policy expectations; retained lifecycle, WHITE limits, and all-channel UART/startup checks.')
