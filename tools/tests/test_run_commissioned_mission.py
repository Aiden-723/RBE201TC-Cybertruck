"""Uses only an ephemeral local HTTP server with an in-memory fake motor transport."""
from datetime import datetime,timezone
import copy,json
from pathlib import Path
import sys,tempfile,threading,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from run_commissioned_mission import LocalControllerClient,preflight,stationarity_feed,main
from rehearse_mission import fixture_plan
from motor_control import MotorControl
from server import create_server

class ClientTests(unittest.TestCase):
    def test_remote_urls_are_rejected_before_any_request(self):
        for url in ('http://192.168.86.5:8765','https://127.0.0.1:8765','http://127.0.0.1:8765/path','http://user@127.0.0.1:8765'):
            with self.assertRaises(ValueError):LocalControllerClient(url)
    def test_default_cli_cannot_construct_motor_client(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'missing.json'
            with patch('sys.argv',['runner','--config',str(p)]),patch('run_commissioned_mission.LocalControllerClient') as client:
                self.assertEqual(main(),1);client.assert_not_called()
    def test_synthetic_plan_refused_even_on_model_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'model';p.write_text('D-Robotics RDK X5 V1.0')
            client=Mock()
            with self.assertRaisesRegex(ValueError,'synthetic_plan'):
                preflight(client,fixture_plan('preflight'),lambda _:None,clock=lambda:100,stationary_max_age=.2,model_path=p)
            client.status.assert_not_called()
    def test_missing_stationarity_args_refuse_before_client(self):
        with patch('sys.argv',['runner','--config','missing','--execute']),patch('run_commissioned_mission.LocalControllerClient') as client:
            with self.assertRaises(SystemExit):main()
            client.assert_not_called()
    def test_feed_source_switch_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'feed.json';p.write_text(json.dumps(dict(source_id='OTHER',confirmed=True,sampled_at=100)))
            with self.assertRaises(ValueError):stationarity_feed(p,'EXPECTED')(100)
    def test_authenticated_http_adapter_uses_existing_fake_owner(self):
        class FakeTransport:
            def __init__(self):self.packets=[]
            def send(self,p):self.packets.append(p)
            def close(self):pass
        transport=FakeTransport();motor=MotorControl(transport,mode='speed')
        with patch.dict('os.environ',{'DEVICE_CONSOLE_TOKEN':'SOFTWARE_FIXTURE_TOKEN'}):
            server=create_server(host='127.0.0.1',port=0,motor=motor)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=LocalControllerClient('http://127.0.0.1:'+str(server.server_port),token='SOFTWARE_FIXTURE_TOKEN',timeout=1)
            session=client.command({'action':'arm'})['session']
            result=client.command(dict(action='drive',session=session,seq=1,direction='forward_right',speed=100))
            self.assertEqual(result['outputs'],[100,100,20,20])
            client.command(dict(action='release',session=session,seq=2))
            self.assertEqual(motor.snapshot()['outputs'],[0]*4)
            client.command({'action':'stop'});self.assertFalse(motor.snapshot()['armed'])
        finally:server.shutdown();server.server_close();thread.join();motor.close()

if __name__=='__main__':unittest.main()
