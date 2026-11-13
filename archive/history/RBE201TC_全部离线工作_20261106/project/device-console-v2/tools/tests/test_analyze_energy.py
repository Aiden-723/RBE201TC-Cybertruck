"""Only analytic SOFTWARE_FIXTURE values, never real battery measurements."""
import csv
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_energy import analyze

class EnergyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='SOFTWARE_FIXTURE_ENERGY_');self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'samples.csv'
    def write(self,powers,**changes):
        rows=[]
        for i,p in enumerate(powers):
            row=dict(EnergyRunID='SOFTWARE_FIXTURE_RUN',InstrumentID='SOFTWARE_FIXTURE_INSTRUMENT',ChannelAndMeasurementBoundary='SOFTWARE_FIXTURE_BOUNDARY',RawInstrumentFileLink='SOFTWARE_FIXTURE_ONLY',SampleNumber=i+1,Elapsed_s=i*10,TimestampWithTZ=(datetime(2026,11,6,tzinfo=timezone.utc)+timedelta(seconds=i*10)).isoformat(),DataClassMeasuredOrCalculated='calculated',Power_W=p,Voltage_V=12,Current_A=1,MissingOrFailureReason='')
            if i==1:row.update(changes)
            rows.append(row)
        with self.path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    def test_constant_power_wh_and_original_untouched(self):
        self.write([12,12,12]);before=self.path.read_bytes();r=analyze(self.path,10)
        self.assertAlmostEqual(r['calculated_energy_Wh'],12*20/3600);self.assertEqual(r['mean_power_W'],12);self.assertEqual(before,self.path.read_bytes())
    def test_linear_ramp_and_valid_zero(self):
        self.write([0,10,20]);self.assertAlmostEqual(analyze(self.path,10)['calculated_energy_Wh'],200/3600)
    def test_voltage_current_used_only_when_power_absent(self):
        self.write(['','','']);r=analyze(self.path,10);self.assertEqual(r['power_sources'],['voltage_times_current']);self.assertEqual(r['mean_power_W'],12)
    def test_invalid_missing_negative_or_nonfinite_cannot_estimate(self):
        for changes in ({'Power_W':'NaN'},{'Power_W':'-1'},{'Power_W':'','Current_A':''},{'RawInstrumentFileLink':''},{'MissingOrFailureReason':'disconnect'}):
            with self.subTest(changes=changes):
                self.write([12,12,12],**changes)
                with self.assertRaises(ValueError):analyze(self.path,10)
    def test_gaps_duplicate_time_and_mixed_boundary_rejected(self):
        for changes in ({'Elapsed_s':0},{'SampleNumber':1},{'InstrumentID':'OTHER'},{'TimestampWithTZ':'2026-11-06T00:00:10'}):
            self.write([12,12,12],**changes)
            with self.assertRaises(ValueError):analyze(self.path,10)
        self.write([12,12]);
        with self.assertRaises(ValueError):analyze(self.path,9)
    def test_one_sample_cannot_produce_energy(self):
        self.write([12]);
        with self.assertRaises(ValueError):analyze(self.path,10)

if __name__=='__main__':unittest.main()
