from pathlib import Path
import ast
P=Path(__file__).resolve().parents[2]/'work/device-console-v2'
def patch(file, method, changes):
 p=P/file;s=p.read_text();t=ast.parse(s);f=next(x for x in ast.walk(t) if isinstance(x,ast.FunctionDef) and x.name==method);lines=s.splitlines(True);part=''.join(lines[f.lineno-1:f.end_lineno])
 for old,new in changes:
  assert old in part,(file,method,old)
  part=part.replace(old,new)
 lines[f.lineno-1:f.end_lineno]=[part];p.write_text(''.join(lines))
def replace(file,method,body):
 p=P/file;s=p.read_text();t=ast.parse(s);f=next(x for x in ast.walk(t) if isinstance(x,ast.FunctionDef) and x.name==method);lines=s.splitlines(True);lines[f.lineno-1:f.end_lineno]=[body.rstrip()+'\n'];p.write_text(''.join(lines))
# Physical-policy expectations change from stop-one-side to continuous 30/50.
for n in ['test_basic_line_follower.py','test_corner_line_follower.py','test_basic_line_integration.py']:
 p=P/'backend/tests'/n;s=p.read_text()
 for a,b in [('(0, 50)','(30, 50)'),('(50, 0)','(50, 30)'),('(0,50)','(30,50)'),('(50,0)','(50,30)'),('[0,0,50,50]','[30,30,50,50]'),('[50,50,0,0]','[50,50,30,30]')]:s=s.replace(a,b)
 p.write_text(s)
f='backend/tests/test_basic_line_follower.py'
patch(f,'test_all_sixteen_patterns_have_fifteen_source_actions_and_white_policy',[("(0, 0, 0, 0): (50, -50, 'hard_right')","(0, 0, 0, 0): (50, 50, 'broad_line')"),("(0, 0, 1, 0): (50, -50, 'hard_right')","(0, 0, 1, 0): (-50, 50, 'hard_left')"),("(0, 1, 1, 0): (50, -50, 'hard_right')","(0, 1, 1, 0): (50, 50, 'broad_line')"),("(0, 50, 'inner_left')","(30, 50, 'inner_left')"),("(50, 0, 'inner_right')","(50, 30, 'inner_right')"),('test_all_sixteen_patterns_have_fifteen_source_actions_and_white_policy','test_all_sixteen_patterns_have_explicit_adapted_actions_and_white_policy')])
replace(f,'test_all_black_and_both_outers_use_vendor_right_priority','''    def test_both_outers_use_inner_evidence_and_symmetric_black_has_no_side(self):
        cases=(([0,0,0,0],(50,50,'broad_line',0.0)),
               ([0,1,1,0],(50,50,'broad_line',0.0)),
               ([0,0,1,0],(-50,50,'hard_left',-3.0)),
               ([0,1,0,0],(50,-50,'hard_right',3.0)))
        for values,expected in cases:self.assertEqual(steering(values,220),expected)
''')
patch(f,'test_base_speed_boundaries_allow_inner_zero_and_fixed_outer_strength', [('(0, min(speed,50))','(30, min(speed,50))'),('allow_inner_zero','use_continuous_inner_differential')])
patch(f,'test_all_black_runs_source_corner_rule_without_station_or_sample_request',[('(50,-50)','(50,50)'),('runs_source_corner_rule','continues_forward')])
patch(f,'test_all_both_outer_patterns_start_without_prior_motion',[("self.assertEqual(state['reason'],'hard_right')","self.assertEqual(state['reason'],steering(values,50)[2])"),('(50,-50)','steering(values,50)[:2]')])
f='backend/tests/test_corner_line_follower.py'
patch(f,'test_policy_snapshot_has_continuous_contract_and_no_legacy_angle_state',[("'continuous_four_digital_v6'","'four_digital_continuous_turn_v1'")])
replace(f,'test_every_visible_sample_immediately_overrides_previous_side','''    def test_inner_samples_correct_immediately_but_active_pivot_keeps_direction(self):
        for values,expected in (([1,0,1,1],(30,50)),([1,1,0,1],(50,30)),(CENTER,(50,50))):
            self.assertEqual(self.pair(self.step(values)),expected)
        self.step(RIGHT)
        for values in (LEFT,[1,0,1,1],CENTER,RIGHT,[0,0,0,0]):
            state=self.step(values);self.assertEqual(self.pair(state),(50,-50))
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
        self.assertEqual(self.motor.calls,[{'action':'arm'}])
        self.assertEqual([seq for seq,_,_ in self.motor.drives],list(range(len(self.motor.drives))))
''')
patch(f,'test_real_v4_pattern_order_now_corrects_left_and_first_center_moves',[("([1, 0, 1, 1], (30, 50))","([1, 0, 1, 1], (50, -50))"),("(CENTER, (50, 50)), (CENTER, (50, 50))","(CENTER, (50, -50)), (CENTER, (50, 50))"),('and_first_center_moves','and_two_center_samples_finish_turn')])
patch(f,'test_fresh_visible_correction_can_run_far_longer_than_old_1point4_seconds',[("(50, -50) if values == RIGHT else (-50, 50)","(-50, 50)")])
replace(f,'test_outer_priority_including_split_is_recomputed_without_direction_latch','''    def test_outer_and_split_start_either_side_then_ignore_opposing_transient(self):
        for values,expected in (([0,0,0,1],(-50,50)),([0,1,0,1],(-50,50)),
                                ([1,0,1,0],(50,-50)),([1,0,0,0],(50,-50))):
            self.reset();self.assertEqual(self.pair(self.step(values)),expected)
            self.assertEqual(self.pair(self.step(CENTER,dt=.001)),expected)
            self.assertEqual(self.pair(self.step(CENTER,dt=.02)),(50,50))
''')
# These tests check the actual weak pair, now after a completed pivot rather than
# insisting that an arbitrary opposite inner reading cancels the turn.
patch(f,'test_gap_holds_last_actually_sent_inner_pair_not_earlier_outer_spin', [ ('self.reset(); self.step(outer); self.step(inner)','self.reset(); self.step(outer); self.step(CENTER); self.step(CENTER); self.step(inner)')])
patch(f,'test_first_center_clears_gap_and_remains_actual_forward_hold_authority',[("self.assertEqual(self.pair(state), (50, 50))","self.assertEqual(self.pair(state), (50, -50))\n        state = self.step(CENTER)\n        self.assertEqual(self.pair(state), (50, 50))"),('test_first_center_clears_gap','test_two_center_samples_clear_turn_after_gap')])
patch(f,'test_reacquired_opposite_inner_immediately_overrides_gap_without_zero_hold', [('(30, 50)','(50, -50)'),('reacquired_opposite_inner_immediately_overrides_gap_without_zero_hold','reacquired_opposite_inner_clears_gap_without_reversing_active_turn')])
# A new black reading still clears gap time; it does not necessarily end a turn.
patch(f,'test_any_fresh_visible_line_resets_only_continuous_gap_not_total_follow_time',[('(30, 50)','(50, -50)')])
replace(f,'test_both_outer_and_all_black_override_gap_with_source_priority','''    def test_broad_readings_clear_gap_and_respect_active_direction(self):
        for previous,pair in ((LEFT,(-50,50)),(RIGHT,(50,-50))):
            for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
                self.reset(first=previous);self.step(WHITE);state=self.step(values)
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertIsNone(state['hold_pattern']);self.assertIsNone(self.worker._line_gap_started)
                self.assertEqual(state['last_visible_values'],values);self.assertEqual(self.pair(state),pair)
''')
replace(f,'test_previously_rejected_masks_have_current_actions_and_sample_diagnostics','''    def test_broad_masks_have_contextual_actions_and_fresh_sample_diagnostics(self):
        for previous in (CENTER,LEFT,RIGHT,[1,1,0,1]):
            for values in ([0,0,0,0],[0,0,1,0],[0,1,0,0],[0,1,1,0]):
                initial=self.reset(first=previous);state=self.step(values,dt=.013)
                expected=(self.pair(initial) if previous in (LEFT,RIGHT) or values in ([0,0,0,0],[0,1,1,0]) else steering(values,50)[:2])
                self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
                self.assertEqual(self.pair(state),expected);self.assertEqual(state['last_visible_values'],values)
                for key,source in [('sample_monotonic','monotonic'),('sample_count','successes')]:
                    self.assertEqual(state[key],self.feed.item[source]);self.assertEqual(state['transition_trace'][-1][key],state[key])
                cache=self.step(dt=.01,fresh=False)
                self.assertEqual(cache['sample_count'],state['sample_count']);self.assertEqual(self.pair(cache),expected)
''')
patch(f,'test_all_both_outer_masks_remain_visible_beyond_old_ambiguity_budget',[('(50,-50)','(-50,50)')])
patch(f,'test_first_visible_center_before_point4_overrides_pivot_immediately',[("self.assertEqual(self.pair(state),(50,50))","self.assertEqual(self.pair(state),(50,-50))\n        state=self.step(CENTER,dt=.02)\n        self.assertEqual(self.pair(state),(50,50))"),('first_visible_center_before_point4_overrides_pivot_immediately','two_fresh_centers_before_point4_finish_the_pivot')])
# Preserve the real clock/expiry coverage while asserting running differential outputs.
f='backend/tests/test_motor_control.py'
patch(f,'test_real_follower_boost_stays_bounded_during_short_correction_gap', [
 ("self.assertEqual(self.motor.snapshot()['outputs'],[200,200,0,0])","self.assertEqual(self.motor.snapshot()['outputs'],[50,50,30,30])"),
 ("[50,50,0,0]","[50,50,30,30]"),
 ("self.now=until+.001;self.motor.check_deadline()\n        self.assertEqual(self.motor.snapshot()['outputs'],[50,50,30,30])","self.now+=.17;self.motor.check_deadline()\n        self.assertEqual(self.motor.snapshot()['outputs'],[50,50,30,30])")])
# A stopped channel does not re-start channels which are still commanded forward.
patch(f,'test_forward_to_inner_pause_clears_old_output_without_restarting_same_action',[("b'$spd:200,200,0,0#'","b'$spd:200,200,0,0#'")])
# Initial boost was already running at 50ms, so it keeps its original deadline.
