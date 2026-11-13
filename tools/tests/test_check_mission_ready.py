import copy,json,tempfile,unittest,sys
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from check_mission_ready import TEMPLATE,check,load_config
from rehearse_mission import fixture_plan

class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_CONFIG_');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.path=self.root/'plan.json'
    def write_fixture(self):
        plan=fixture_plan('config');d=copy.deepcopy(TEMPLATE)
        for key in ('mission_id','group_id','room_id','route_evidence_id','marker_evidence_id','stationary_evidence_id','timing_evidence_id'):d[key]=getattr(plan,key)
        d['stations']=[asdict(s) for s in plan.stations];d['timing']=asdict(plan.timing)
        d['calibration']=asdict(plan.calibration);d['calibration']['physical_left_to_right_order']=list(plan.calibration.physical_left_to_right_order)
        for key in ('white_reference','black_reference'):
            (self.root/(key+'.json')).write_text(json.dumps(getattr(plan.calibration,key)));d['calibration'][key]=key+'.json'
        d['guidance']=dict(positions=[-3,-1,1,3],deadband=.5,speed=100)
        d['crossings']=dict(confirm_seconds=.2,confirm_frames=3,clear_frames=2,max_gap=.5)
        d['loop']=dict(period=.1,stationary_max_age=.2)
        self.path.write_text(json.dumps(d));return d
    def test_missing_and_empty_template_block_motion(self):
        self.assertFalse(check(self.path)['configuration_valid'])
        self.path.write_text(json.dumps(TEMPLATE));r=check(self.path)
        self.assertFalse(r['configuration_valid']);self.assertFalse(r['autonomous_enabled'])
    def test_valid_synthetic_config_still_does_not_enable_motion(self):
        self.write_fixture();r=check(self.path)
        self.assertTrue(r['configuration_valid'],r);self.assertFalse(r['autonomous_enabled']);self.assertFalse(r['sends_motor_commands'])
        self.assertEqual(len(r['reference_sha256']),2)
    def test_unknown_fields_and_invalid_order_refused(self):
        d=self.write_fixture();d['unrecognized']=True;self.path.write_text(json.dumps(d));self.assertFalse(check(self.path)['configuration_valid'])
        d=self.write_fixture();d['calibration']['physical_left_to_right_order']=None;self.path.write_text(json.dumps(d));self.assertFalse(check(self.path)['configuration_valid'])
    def test_missing_real_black_reference_refused(self):
        self.write_fixture();(self.root/'black_reference.json').unlink();self.assertFalse(check(self.path)['configuration_valid'])
    def test_timing_and_source_contrast_rechecked(self):
        d=self.write_fixture();d['loop']['period']=1.;self.path.write_text(json.dumps(d));self.assertFalse(check(self.path)['configuration_valid'])
        self.write_fixture();p=self.root/'black_reference.json';d=json.loads(p.read_text())
        for row in d['samples']:row['value']=[0,0,0,0]
        p.write_text(json.dumps(d));self.assertFalse(check(self.path)['configuration_valid'])

if __name__=='__main__':unittest.main()
