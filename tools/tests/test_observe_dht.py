from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from observe_dht import summarize


def row(s,f,t,status='live',value=None,kind='sample'):
    return dict(kind=kind,snapshot=dict(successes=s,failures=f,monotonic=t,status=status,value=value if value is not None else {'temperature_c':0,'humidity_pct':0},error='checksum'))


class ObserverTests(unittest.TestCase):
    def test_baseline_excluded_and_failure_value_not_reused(self):
        r=summarize([row(100,10,1,kind='baseline'),row(101,10,3),row(101,11,5,'error',{'temperature_c':99,'humidity_pct':99})])
        self.assertEqual(r['counter_attempts_in_observed_segments'],2)
        self.assertEqual(r['success_rate_pct'],50)
        self.assertEqual(r['temperature_c'],{'min':0,'max':0})
        self.assertEqual(r['observed_error_reasons'],{'checksum':1})
    def test_missed_publishes_counted_without_inventing_values(self):
        r=summarize([row(0,0,0,kind='baseline'),row(2,1,9)])
        self.assertEqual(r['unobserved_outcomes_within_segments'],2)
        self.assertEqual(r['counter_successes'],2)
        self.assertEqual(r['observed_outcomes'],{'live':1})
    def test_reset_starts_new_segment_and_no_outcomes_is_not_success(self):
        r=summarize([row(10,2,10,kind='baseline'),row(0,0,11),row(1,0,13)])
        self.assertEqual(r['counter_reset_segments'],1);self.assertEqual(r['counter_successes'],1)
        self.assertIsNone(summarize([row(100,20,1,kind='baseline')])['success_rate_pct'])
    def test_invalid_values_stale_and_reader_error_are_visible(self):
        r=summarize([row(0,0,0,kind='baseline'),row(1,0,3,value={'temperature_c':float('nan'),'humidity_pct':50}),dict(kind='read_error'),dict(kind='stale_start')])
        self.assertEqual(r['invalid_live_values'],1);self.assertIsNone(r['temperature_c'])
        self.assertEqual(r['stale_episodes'],1);self.assertEqual(r['read_error_events'],1)


if __name__=='__main__':unittest.main()
