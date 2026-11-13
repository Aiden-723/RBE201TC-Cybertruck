import csv
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
sys.path.insert(0,str(TOOLS.parent/'backend'))
import check_data as audit
from experiment_log import RunLog, Station


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_NOT_COURSE_DATA_')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.now=datetime(2026,11,6,tzinfo=timezone.utc)
        self.log=RunLog(self.root,purpose='room_stationary',group_id='FIXTURE_GROUP',room_id='FIXTURE_ROOM',run_id='FIXTURE_RUN',now=lambda:self.now,
            metadata={'stations':[dict(station_id='S1',x_m=0.,y_m=0.,height_m=.2),dict(station_id='S2',x_m=1.,y_m=0.,height_m=.2)]})
    def record(self,station='S1',status='success',value=None):
        p=self.log.begin(Station(station,0 if station=='S1' else 1,0,.2))
        self.now+=timedelta(seconds=2)
        self.log.finish(p,status,value=value or {'temperature_c':0,'humidity_pct':0},explanation='Software fixture')
    def check(self):return audit.audit([self.log.directory])
    def codes(self,r):return {i['code'] for i in r['issues']}
    def edit(self,fn):
        _,fields,rows=audit.read_csv(self.log.path);fn(rows)
        with self.log.path.open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fields);w.writeheader();w.writerows(rows)
    def test_valid_real_zero_and_missing_station(self):
        self.record();self.log.close();r=self.check()
        self.assertEqual(r['summary']['errors'],0)
        self.assertEqual(r['summary']['candidate_room_rows'],1)
        self.assertEqual(r['runs'][0]['missing_stations'],['S2'])
        self.assertEqual(r['files'][0]['manifest_integrity'],'verified')
    def test_failed_station_is_visited_but_requires_retry(self):
        self.record();self.record('S2','sensor_failure');self.log.close();r=self.check()
        self.assertEqual(r['runs'][0]['missing_stations'],[])
        self.assertEqual(r['runs'][0]['stations_without_valid_reading'],['S2'])
        self.assertEqual(r['summary']['candidate_room_rows'],1)
    def test_repeated_station_events_are_not_duplicates(self):
        self.record();self.record();self.log.close();r=self.check()
        self.assertNotIn('duplicate_event',self.codes(r));self.assertEqual(r['summary']['candidate_room_rows'],2)
    def test_equivalent_event_numbers_and_copied_files_flag_both(self):
        self.record();self.record();self.log.close()
        self.edit(lambda rows:rows[1].update({'Detection point':'01.0'}));r=self.check()
        self.assertIn('duplicate_event',self.codes(r));self.assertEqual(r['summary']['candidate_room_rows'],0)
        other=self.root/'copy';other.mkdir();(other/self.log.path.name).write_bytes(self.log.path.read_bytes())
        self.assertIn('duplicate_event',self.codes(audit.audit([self.root])))
    def test_bad_failure_values_and_hash_are_never_hidden(self):
        self.record(status='sensor_failure');self.log.close()
        self.edit(lambda rows:rows[0].update({'Temperature_°C':'0'}));r=self.check()
        self.assertTrue({'failure_has_values','manifest_mismatch'}<=self.codes(r))
        self.assertEqual(r['files'][0]['manifest_integrity'],'failed')
    def test_pending_and_unclosed_run_remain_unmodified(self):
        self.log.begin(Station('S1',0,0,.2));before=self.log.path.read_bytes();r=self.check()
        self.assertTrue({'pending','unclosed_run'}<=self.codes(r))
        self.assertEqual(before,self.log.path.read_bytes());self.assertEqual(r['summary']['candidate_room_rows'],0)
    def test_missing_coordinates_nan_bad_time_and_missing_identity(self):
        self.record();self.log.close()
        self.edit(lambda rows:rows[0].update({'X_m':'NaN','Trigger time':'2026-11-06T00:00:00','GroupID':''}));r=self.check()
        self.assertTrue({'missing_coordinates','invalid_time','missing_location_identity'}<=self.codes(r))
    def test_unknown_plan_does_not_claim_coverage(self):
        self.record();self.log.close();self.log.manifest_path.unlink();r=self.check()
        self.assertIn('coverage_unknown',self.codes(r));self.assertIsNone(r['runs'][0]['missing_stations'])
    def test_plan_coordinates_and_unexpected_station(self):
        self.record();self.log.close();r=audit.audit([self.log.directory],{'S1':[5,0,.2]})
        self.assertIn('plan_coordinate_mismatch',self.codes(r))
        r=audit.audit([self.log.directory],{'S9':[0,0,.2]})
        self.assertIn('unexpected_station',self.codes(r))
    def test_station_conflicts_and_sample_reuse(self):
        self.record();self.record();self.log.close()
        def mutate(rows):
            rows[1]['X_m']='2'
            for r in rows:r.update(Sampled_at='2026-11-06T00:00:00+00:00',Sampler_monotonic='10')
        self.edit(mutate);r=self.check()
        self.assertTrue({'station_conflict','reused_sample'}<=self.codes(r))
    def test_event_gap_and_duplicate_headers(self):
        self.record();self.log.close();self.edit(lambda rows:rows[0].update({'Detection point':'3'}))
        self.assertIn('event_gaps',self.codes(self.check()))
        self.log.path.write_text('RunID,RunID\nx,x\n')
        self.assertIn('invalid_columns',self.codes(self.check()))
    def test_bench_not_formal_missing_location_is_expected(self):
        log=RunLog(self.root,purpose='bench_diagnostic',run_id='FIXTURE_BENCH',now=lambda:self.now)
        p=log.begin(Station('UNLOCATED'));log.finish(p,'success',value={'temperature_c':20,'humidity_pct':50});log.close()
        r=audit.audit([log.directory]);self.assertEqual(r['summary']['candidate_room_rows'],0)
        self.assertEqual(r['summary']['errors'],0);self.assertEqual(r['summary']['bench_rows'],1)
    def test_report_escapes_text_and_refuses_overwrite(self):
        self.record(status='sensor_failure');self.log.close()
        self.edit(lambda rows:rows[0].update({'Explanation':'<script>alert(1)</script>'}));r=self.check()
        page=audit.save_report(r,self.root/'report');text=page.read_text()
        self.assertNotIn('<script>',text);self.assertIn('&lt;script&gt;',text)
        with self.assertRaises(FileExistsError):audit.save_report(r,self.root/'report')
    def test_bad_manifest_and_mixed_run_cannot_be_candidates(self):
        self.record();self.record();self.log.close()
        self.log.manifest_path.write_text('[]')
        r=self.check();self.assertIn('bad_manifest',self.codes(r))
        self.assertEqual(r['summary']['candidate_room_rows'],0)
        self.log.manifest_path.unlink()
        self.edit(lambda rows:rows[1].update(RoomID='OTHER_ROOM'))
        r=self.check();self.assertIn('mixed_run_identity',self.codes(r))
        self.assertEqual(r['summary']['candidate_room_rows'],0)
    def test_empty_input_and_strict_station_plan(self):
        empty=self.root/'empty';empty.mkdir()
        self.assertIn('no_files',self.codes(audit.audit([empty])))
        p=empty/'stations.csv';p.write_text('StationID,X_m,Y_m,SensorHeight_m\n')
        with self.assertRaises(ValueError):audit.load_plan(p)
    def test_numeric_identity_rejects_nonfinite_large_and_negative(self):
        for n in ('NaN','Infinity','0','-1','1.1',str(2**53)):
            self.assertIsNone(audit.event_number(n))
        self.assertEqual(audit.event_number('01.0'),1)

    def test_malformed_manifest_station_plan_is_reported_without_crashing(self):
        self.record(); self.log.close()
        for specs in ([None], {'S1': 'invalid'}, ['invalid']):
            manifest = json.loads(self.log.manifest_path.read_text())
            manifest['metadata']['stations'] = specs
            self.log.manifest_path.write_text(json.dumps(manifest))
            before = self.log.path.read_bytes()
            report = self.check()
            self.assertIn('invalid_station_plan', self.codes(report))
            self.assertEqual(report['summary']['candidate_room_rows'], 0)
            self.assertEqual(self.log.path.read_bytes(), before)

    def test_timestamp_contract_accepts_utc_z_and_rejects_normalized_invalid_offsets(self):
        self.assertIsNotNone(audit.moment('2026-11-13T00:00:00.000Z'))
        self.assertIsNone(audit.moment('2026-11-13T00:00:00.000+08:99'))


if __name__=='__main__':unittest.main()
