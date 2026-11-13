import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('clock_sync',Path(__file__).resolve().parents[1]/'sync_clock_before_connect.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class ClockSyncTests(unittest.TestCase):
    target=1793889000
    def setUp(self):
        self.acq={'active_run_id':None,'busy':False,'sensor_only':True}
        self.control={'enabled':False,'armed':False,'state':'disabled'}
        self.execute=Mock()
    def read(self,endpoint):return self.acq if endpoint=='experiments' else self.control
    def run_sync(self):
        return module.synchronize(self.target,now=Mock(side_effect=[946684800,self.target]),read=self.read,execute=self.execute)
    def test_invalid_clock_is_corrected_only_when_idle_readonly(self):
        self.assertEqual(self.run_sync()['clock'],'synchronized_from_mac')
        self.assertEqual(self.execute.call_count,1)
        self.assertIn('@'+str(self.target),self.execute.call_args.args[0])
    def test_already_correct_has_no_requests_or_writes(self):
        read=Mock(side_effect=AssertionError('must not query'))
        self.assertEqual(module.synchronize(self.target,now=lambda:self.target,read=read,execute=self.execute)['clock'],'already_current')
        self.execute.assert_not_called()
    def test_active_recording_is_never_adjusted(self):
        self.acq['active_run_id']='REAL_RUN';self.assertTrue(self.run_sync()['attention_required']);self.execute.assert_not_called()
    def test_busy_worker_is_never_adjusted(self):
        self.acq['busy']=True;self.run_sync();self.execute.assert_not_called()
    def test_enabled_motor_is_never_adjusted(self):
        self.control.update(enabled=True,state='stopped');self.run_sync();self.execute.assert_not_called()
    def test_missing_state_is_not_assumed_idle(self):
        self.acq.clear();self.run_sync();self.execute.assert_not_called()
    def test_invalid_computer_time_rejected(self):
        for value in (0,True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):module.synchronize(value,execute=self.execute)
        self.execute.assert_not_called()
    def test_failed_correction_not_reported_as_success(self):
        with self.assertRaises(RuntimeError):
            module.synchronize(self.target,now=lambda:946684800,read=self.read,execute=self.execute)

    def test_idle_zero_unarmed_motor_can_sync_before_tunnel_opens(self):
        self.acq['sensor_only']=False
        self.control.update(enabled=True,state='stopped',fault=None,outputs=[0,0,0,0])
        self.assertEqual(self.run_sync()['clock'],'synchronized_from_mac')
    def test_nonzero_or_armed_motor_blocks_sync(self):
        self.control.update(enabled=True,state='stopped',fault=None,outputs=[200,0,0,0])
        self.run_sync();self.execute.assert_not_called()
        self.control.update(armed=True,outputs=[0,0,0,0])
        self.run_sync();self.execute.assert_not_called()
