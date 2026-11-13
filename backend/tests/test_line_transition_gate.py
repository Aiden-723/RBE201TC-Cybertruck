"""Pure gate tests, including time-preserving manual pattern RUN SUMMARY.

The manual run summary is not an automatic/pivot/90-degree success fixture.
Source capture: outputs/RBE201TC_RIGHT_ANGLE_20261111/integration/manual_right_turn_014917.json
SHA256: 9eff76a33bbedb80ac77df4369c245d899e17c9f7a08fc06bf72fe317623d9a1
Timestamps below are original sensor monotonic offsets, rounded to 9 decimals.
Run compression removes equal bits only; no elapsed time is compressed.
"""
import itertools
import unittest

import line_transition_gate
from line_transition_gate import LineTransitionGate


# (X1..X4 bits, unique published sample count, first offset, last offset).
# Hand positioning with no measured angle/pose; drive count remained 37 -> 37.
MANUAL_RIGHT_RUNS = (
    ('1001', 14, 0.0, 1.991452012),
    ('1101', 1, 2.144586404, 2.144586404),
    ('1001', 3, 2.297729338, 2.603273252),
    ('1101', 1, 2.756203604, 2.756203604),
    ('1001', 7, 2.909182748, 3.828110517),
    ('1101', 1, 3.980523872, 3.980523872),
    ('1001', 4, 4.134672841, 4.595884214),
    ('1101', 1, 4.749449979, 4.749449979),
    ('1001', 2, 4.901840751, 5.054420689),
    ('1101', 1, 5.207834454, 5.207834454),
    ('1001', 1, 5.361059929, 5.361059929),
    ('1101', 1, 5.514464569, 5.514464569),
    ('1001', 3, 5.666637218, 5.972303340),
    ('1101', 1, 6.125467482, 6.125467482),
    ('1001', 2, 6.278139086, 6.430088736),
    ('1101', 1, 6.583096796, 6.583096796),
    ('1001', 2, 6.737428388, 6.892921848),
    ('0001', 1, 7.045983366, 7.045983366),
    ('1001', 1, 7.198593678, 7.198593678),
    ('1111', 68, 7.352743230, 17.632178882),
    ('0111', 3, 17.784695862, 18.092058681),
    ('1111', 1, 18.244037789, 18.244037789),
    ('0111', 4, 18.396017731, 18.858748385),
    ('1111', 1, 19.011439031, 19.011439031),
    ('0111', 1, 19.163837178, 19.163837178),
    ('1111', 2, 19.316351908, 19.469335676),
    ('0111', 1, 19.621818947, 19.621818947),
    ('1111', 12, 19.774258969, 21.452347577),
    ('0111', 2, 21.605354804, 21.759480064),
    ('1111', 5, 21.913423326, 22.526876972),
    ('1011', 2, 22.680148239, 22.832599844),
    ('1001', 2, 22.985181823, 23.138235133),
    ('1011', 2, 23.291302275, 23.444285335),
    ('1001', 6, 23.597271603, 24.364790094),
    ('1101', 1, 24.517292324, 24.517292324),
    ('1111', 2, 24.671462209, 24.824999932),
    ('0111', 1, 24.976868791, 24.976868791),
    ('1111', 1, 25.130461722, 25.130461722),
    ('1001', 3, 25.284047861, 25.590646769),
    ('1101', 11, 25.743969201, 27.273864384),
    ('1001', 116, 27.426643028, 45.041146862),
)


class LineTransitionGateTests(unittest.TestCase):
    def test_initial_state_is_actual_entry_not_a_phantom_transition(self):
        for initial in (False, True):
            gate = LineTransitionGate(initial)
            self.assertEqual(gate.count, 0)
            self.assertIs(gate.last_status, initial)
            self.assertFalse(gate.complete)
            self.assertFalse(gate.observe(initial))
            self.assertEqual(gate.count, 0)

    def test_black_departure_reacquisition_requires_two_changes(self):
        gate = LineTransitionGate(True)
        self.assertFalse(gate.observe(True))
        self.assertFalse(gate.observe(False))
        self.assertEqual(gate.count, 1)
        self.assertFalse(gate.observe(False))
        self.assertTrue(gate.observe(True))
        self.assertEqual(gate.count, 2)

    def test_white_initial_state_is_supported_without_direction_assumptions(self):
        gate = LineTransitionGate(False)
        self.assertFalse(gate.observe(True))
        self.assertTrue(gate.observe(False))

    def test_repeated_constant_status_never_completes(self):
        for initial in (False, True):
            gate = LineTransitionGate(initial)
            for _ in range(1000):
                self.assertFalse(gate.observe(initial))
            self.assertEqual(gate.count, 0)

    def test_count_saturates_at_two_after_arbitrary_later_changes(self):
        gate = LineTransitionGate(True)
        for status in (False, True) * 100:
            gate.observe(status)
            self.assertLessEqual(gate.count, 2)
        self.assertEqual(gate.count, 2)
        self.assertTrue(gate.complete)

    def test_bool_is_strict_and_invalid_observation_cannot_mutate_gate(self):
        invalid = (0, 1, 0.0, 1.0, None, '', 'True', [], {}, object())
        for status in invalid:
            with self.assertRaisesRegex(ValueError, 'invalid_line_status'):
                LineTransitionGate(status)
        gate = LineTransitionGate(True)
        gate.observe(False)
        for status in invalid:
            with self.assertRaises(ValueError): gate.observe(status)
            self.assertEqual((gate.count, gate.last_status), (1, False))

    def test_all_boolean_sequences_match_saturated_xor_core(self):
        for initial in (False, True):
            for size in range(9):
                for observations in itertools.product((False, True), repeat=size):
                    gate = LineTransitionGate(initial)
                    count, previous = 0, initial
                    for status in observations:
                        count = min(2, count + int(status ^ previous))
                        previous = status
                        self.assertEqual(gate.observe(status), count == 2)
                        self.assertEqual((gate.count, gate.last_status), (count, previous))

    def test_instances_do_not_share_status_or_transition_count(self):
        first, second = LineTransitionGate(True), LineTransitionGate(True)
        first.observe(False); first.observe(True)
        self.assertTrue(first.complete)
        self.assertFalse(second.complete)
        self.assertEqual(second.count, 0)

    def test_full_mit_notice_and_fixed_source_are_retained(self):
        notice = line_transition_gate.__doc__
        self.assertIn('48c47e4ffaa5cb3d9d0fe35ef3e77ac8786e9de7', notice)
        self.assertIn('Copyright (c) 2012-2020 Pololu Corporation (www.pololu.com)', notice)
        self.assertIn('Permission is hereby granted, free of charge', notice)
        self.assertIn('The above copyright notice and this permission notice shall be included in all', notice)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND', notice)
        self.assertIn('CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.', notice)

    def test_manual_pattern_summary_replays_gate_without_compressing_real_time(self):
        self.assertEqual(sum(run[1] for run in MANUAL_RIGHT_RUNS), 295)
        self.assertTrue(all(a[3] < b[2] for a, b in zip(MANUAL_RIGHT_RUNS, MANUAL_RIGHT_RUNS[1:])))
        entry_index = next(i for i, run in enumerate(MANUAL_RIGHT_RUNS) if run[0] == '0001')
        entry = MANUAL_RIGHT_RUNS[entry_index]
        # No use of requested/right label to remap raw X1/X4 or infer angle.
        gate = LineTransitionGate(any(bit == '0' for bit in entry[0][1:3]))
        transitions = []
        for bits, count, start, end in MANUAL_RIGHT_RUNS[entry_index:]:
            before = gate.count
            # Same raw bool repeated count times cannot create extra edges.
            for _ in range(count): gate.observe(any(bit == '0' for bit in bits[1:3]))
            if gate.count != before: transitions.append((gate.count, start))
        self.assertEqual(transitions, [(1, 7.352743230), (2, 22.680148239)])
        white_run = MANUAL_RIGHT_RUNS[19]
        self.assertGreater(white_run[3] - white_run[2], 10.0)
        self.assertGreater(transitions[1][1] - entry[2], 1.4,
                           'This real hand sequence exceeds the worker timeout; NOT auto-corner success')
        self.assertTrue(gate.complete)  # Only the pure XOR helper, no motor or clock.


if __name__ == '__main__':
    unittest.main()
