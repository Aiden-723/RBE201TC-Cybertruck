from pathlib import Path
exec((Path(__file__).with_name('update_contract_tests.py')).read_text().split('# Physical-policy')[0])
f='backend/tests/test_basic_line_integration.py'
patch(f,'test_all_black_replaces_forward_and_continues_past_old_ambiguity_timeout',[('[50,50,-50,-50]','[50]*4'),('replaces_forward','preserves_forward')])
replace(f,'test_outer_gap_inner_opposite_and_center_replace_each_other_immediately','''    def test_outer_gap_inner_and_single_center_keep_pivot_then_two_centers_finish(self):
        token=self.start();self.heartbeat(token);self.follower.tick()
        before=len(self.wire.packets);self.line_step(token,[1,0,0,0])
        self.assertEqual(self.wire.packets[before:],[b'$spd:50,50,-50,-50#'])
        self.assertTrue(self.line_step(token,[1,1,1,1])['line_gap_active'])
        for values in ([1,1,0,1],[1,0,1,1],[1,0,0,1]):
            state=self.line_step(token,values)
            self.assertTrue(state['running']);self.assertFalse(state['line_gap_active'])
            self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])
        self.line_step(token,[1,0,0,1]);self.assertEqual(self.motor.snapshot()['outputs'],[50]*4)
        self.assertTrue(self.motor.snapshot()['armed'])
''')
patch(f,'test_visible_feedback_does_not_expire_at_a_turn_duration',[("state = self.line_step(token, [1,0,0,1])","state = self.line_step(token, [1,0,0,1])\n        self.assertEqual(self.motor.snapshot()['outputs'], [50,50,-50,-50])\n        state = self.line_step(token, [1,0,0,1])")])
patch(f,'test_first_center_overrides_white_and_next_all_black_uses_source_rule',[("self.assertEqual(self.motor.snapshot()['outputs'],[50]*4)","self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])\n        self.line_step(token,[1,0,0,1]);self.assertEqual(self.motor.snapshot()['outputs'],[50]*4)"),("self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])\n", "self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])\n")])
# After the second center, symmetric black continues forward.
p= P/f;s=p.read_text();start=s.index('    def test_first_center_overrides_white');end=s.index('\n\n',s.index("state=self.line_step(token,[0,0,0,0])",start));part=s[start:end];part=part.replace("self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])", "self.assertEqual(self.motor.snapshot()['outputs'],[50]*4)",1) if False else part
# Update only the last assertion of that test.
a=part.rfind("self.assertEqual(self.motor.snapshot()['outputs'],[50,50,-50,-50])");part=part[:a]+part[a:].replace("[50,50,-50,-50]","[50]*4",1);s=s[:start]+part+s[end:];p.write_text(s)
patch(f,'test_opposite_outer_is_current_feedback_not_a_corner_fault',[("'left'","'right'"),('[-50,-50,50,50]','[50,50,-50,-50]'),('opposite_outer_is_current_feedback','opposite_outer_preserves_current_turn')])
patch(f,'test_gap_holds_last_real_inner_pair_not_a_new_hard_turn',[("self.line_step(token, [1,0,0,0])","self.line_step(token, [1,0,0,0])\n        self.line_step(token, [1,0,0,1]);self.line_step(token, [1,0,0,1])")])
patch(f,'test_all_four_previously_unlocated_masks_reach_uart_without_timeout',[('[50,50,-50,-50]','[-50,-50,50,50]')])
patch(f,'test_recorded_full_black_in_right_correction_does_not_pick_new_direction',[('[30,30,50,50]','[50,50,-50,-50]')])
patch(f,'test_default_channel_order_reaches_signed_uart_with_startup_assist',[("([0, 0, 0, 0], [50, 50, -50, -50])","([0, 0, 0, 0], [50, 50, 50, 50])"),("([0, 1, 0, 0], [50, 50, -50, -50])","([0, 1, 0, 0], [-50, -50, 50, 50])"),("([0, 1, 1, 0], [50, 50, -50, -50])","([0, 1, 1, 0], [50, 50, 50, 50])"),("[0, 0, 50, 50]","[30, 30, 50, 50]"),("[50, 50, 0, 0]","[50, 50, 30, 30]"),("boost=[200 if value>0 else -200 if value<0 else 0 for value in expected]","boost=[value*4 for value in expected]")])
patch(f,'test_recorded_raw_correction_white_then_center_does_not_reuse_old_turn',[("([1,0,0,1],[50]*4),([1,1,1,1],[50]*4)","([1,0,0,1],[50,50,-50,-50]),([1,0,0,1],[50]*4),([1,1,1,1],[50]*4)"),("seq==6","seq==7"),("),7):","),8):")])
f='backend/tests/test_autonomous_motor_bridge.py'
patch(f,'test_recorded_opposing_patterns_have_one_speed_packet_each_no_full_car_stop',[("frames=[([1,0,1,1],[0,0,50,50]),([1,0,1,0],[50,50,-50,-50]),([1,0,1,1],[0,0,50,50]),([1,0,0,1],[50]*4)]*5","frames=[([1,0,1,1],[30,30,50,50]),([1,0,1,0],[50,50,-50,-50]),([1,0,1,1],[50,50,-50,-50]),([1,0,0,1],[50,50,-50,-50]),([1,0,0,1],[50]*4)]*5")])
# Update user-visible status to describe intent rather than claiming an outer
# probe is still black after the turn started. Detailed engineering goes in docs.
p=P/'frontend/control.js';s=p.read_text().replace("hard_left:'左外侧见线 · 强纠偏',hard_right:'右外侧见线 · 强纠偏'","hard_left:'正在左转',hard_right:'正在右转',broad_line:'宽黑区域 · 延续行驶'")
start=s.index("autoMessage.textContent='基础循线");end=s.index("'+",start)+2
s=s[:start]+"autoMessage.textContent='基础循线 · 直行50 · 连续差速纠偏。'"+s[end-1:]
p.write_text(s)
p=P/'frontend/tests/corner-control.test.cjs';s=p.read_text();a=s.index("test('v6 banner");b=s.index("\ntest(",a+6);s=s[:a]+'''test('automatic banner stays concise and retains start50',()=>{
  const source=require('node:fs').readFileSync(require.resolve('../control.js'),'utf8');
  assert.match(source,/autonomy_start',speed:50/);assert.match(source,/直行50/);
  assert.match(source,/连续差速纠偏/);
  assert.doesNotMatch(source,/居中即前进|每次动作先200|见线按官方组合规则/);
});
'''+s[b:];p.write_text(s)
