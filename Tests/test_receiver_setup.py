import json,os,pathlib,sys,tempfile,unittest
from unittest.mock import patch,Mock
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
import receiver_setup as setup
class ReceiverSetupTests(unittest.TestCase):
 def test_remote_recipe_compiles_without_running(self):compile(setup.REMOTE,'receiver-setup-fixture','exec')
 def test_add_is_explicit_preserves_legacy_and_never_selects_new_receiver(self):
  config=dict(ssh_host='fixture-old',remote_root='/fixture/old',remote_client_sha256='0'*64,unrelated_setting='keep')
  assets={'snapclient-macbee-application':dict(sha256='1'*64,data=''), 'macbee-tone-probe':dict(sha256='2'*64,data=''),'audio_guardian-configured-v1.py':dict(sha256='3'*64,data='')}
  with tempfile.TemporaryDirectory() as tmp,patch.object(setup,'local_assets',return_value=assets),patch.object(setup,'rpc',return_value=dict(root='/fixture/new',device_sha256='4'*64)) as rpc,patch.object(setup,'local_device_identity',return_value='5'*64):
   row=setup.add_receiver('fixture-new','Office Mac',config,pathlib.Path(tmp));saved=json.loads((pathlib.Path(tmp)/'local-config.json').read_text())
   self.assertEqual(len(saved['receivers']),2);self.assertEqual(saved['unrelated_setting'],'keep');self.assertFalse(row['selected'])
   self.assertEqual(saved['receivers'][0]['id'],'remote');self.assertEqual(saved['receivers'][0]['remote_root'],'/fixture/old/')
   self.assertEqual(rpc.call_args.args[0],'fixture-new');self.assertEqual(os.stat(pathlib.Path(tmp)/'local-config.json').st_mode&0o777,0o600)
 def test_unknown_duplicate_invalid_destinations_do_not_contact_anything(self):
  with patch.object(setup,'rpc',side_effect=AssertionError('No network')),patch.object(setup,'local_assets',side_effect=AssertionError('No file read')):
   with self.assertRaises(ValueError):setup.check_receiver('unknown',{})
   with self.assertRaises(ValueError):setup.add_receiver('-bad','Test',{})
   with self.assertRaises(ValueError):setup.add_receiver('fixture','Test',dict(ssh_host='fixture',remote_root='/fixture/root'))
 def test_sender_cannot_be_added_as_a_remote_receiver(self):
  assets={'snapclient-macbee-application':dict(sha256='1'*64,data=''),'macbee-tone-probe':dict(sha256='2'*64,data=''),'audio_guardian-configured-v1.py':dict(sha256='3'*64,data='')}
  with patch.object(setup,'local_assets',return_value=assets),patch.object(setup,'rpc',return_value=dict(root='/fixture/new',device_sha256='4'*64)),patch.object(setup,'local_device_identity',return_value='4'*64),patch.object(setup,'save_config',side_effect=AssertionError('No config change')):
   with self.assertRaisesRegex(ValueError,'already an output'):setup.add_receiver('fixture','Duplicate',{})
 def test_selection_is_local_only_and_preserves_legacy_connection(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(setup,'rpc',side_effect=AssertionError('No network')):
   config=dict(ssh_host='fixture',remote_root='/fixture/root')
   setup.save_selection(['remote'],config,pathlib.Path(tmp));saved=json.loads((pathlib.Path(tmp)/'local-config.json').read_text())
   self.assertFalse(saved['local_output_selected']);self.assertTrue(saved['receivers'][0]['selected']);self.assertEqual(saved['ssh_host'],'fixture')
   with self.assertRaises(ValueError):setup.save_selection(['unknown'],config,pathlib.Path(tmp))
 def test_host_key_refusal_is_never_bypassed(self):
  with patch('receiver_setup.subprocess.run',return_value=Mock(returncode=255,stderr='Host key verification failed',stdout='')) as call:
   with self.assertRaisesRegex(RuntimeError,'not verified'):setup.rpc('fixture',dict(action='check'))
   args=call.call_args.args[0];self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('BatchMode=yes',args)
   self.assertNotIn('accept-new',' '.join(args));self.assertEqual(call.call_count,1)
 def test_config_symlink_refused_without_target_change(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);target=root/'target';target.write_text('keep');(root/'local-config.json').symlink_to(target)
   with self.assertRaises(RuntimeError):setup.save_config({},root)
   self.assertEqual(target.read_text(),'keep')
if __name__=='__main__':unittest.main()
