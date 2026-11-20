import copy,pathlib,sys,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
from receiver_registry import configured_receivers,selected_outputs,OutputState,AUTHORIZATION

def row(number):return dict(id='r-%016x'%number,name='Test Mac '+str(number),ssh_host='fixture-'+str(number),remote_root='/fixture/receiver',client_sha256='0'*64,selected=True,authorization=AUTHORIZATION)
class ReceiverRegistryTests(unittest.TestCase):
 def test_existing_pair_migrates_without_writing_or_probing(self):
  config=dict(ssh_host='fixture',remote_root='/fixture/receiver',remote_client_sha256='0'*64);before=copy.deepcopy(config)
  with patch('subprocess.Popen',side_effect=AssertionError('No processes')),patch('socket.socket',side_effect=AssertionError('No network')):
   receivers=configured_receivers(config);self.assertEqual(receivers[0].id,'remote')
   self.assertEqual(list(selected_outputs(config,['local','remote'])),['local','remote'])
  self.assertEqual(config,before)
 def test_four_receivers_any_subset_and_optional_local(self):
  config=dict(receivers=[row(i) for i in range(1,5)])
  for ids in (['local'],[row(2)['id']],[row(i)['id'] for i in (1,3,4)],['local']+[row(i)['id'] for i in range(1,5)]):
   self.assertEqual(list(selected_outputs(config,ids)),ids)
  self.assertEqual([x.public()['availability'] for x in configured_receivers(config)],['not_checked']*4)
 def test_no_implicit_authorization_or_unknown_destination(self):
  for change in (dict(authorization=''),dict(ssh_host='-oProxyCommand=bad'),dict(remote_root='/fixture/../else'),dict(password='secret'),dict(id='local')):
   with self.assertRaises(ValueError):configured_receivers(dict(receivers=[dict(row(1),**change)]))
  for selection in ([],['unknown'],['local','local']):
   with self.assertRaises(ValueError):selected_outputs(dict(receivers=[row(1)]),selection)
  with self.assertRaises(ValueError):configured_receivers(dict(receivers=[row(1),row(1)]))
 def test_one_of_four_receivers_drops_others_remain_connected(self):
  ids=['local']+[row(i)['id'] for i in range(1,5)];state=OutputState(ids)
  for key in ids:state.ready(key)
  self.assertTrue(state.can_continue_after(ids[2]));self.assertEqual(state.remaining(),[ids[0],ids[1],ids[3],ids[4]])
  self.assertEqual(state.drain_events(),[dict(output=ids[2],state='offline')]);self.assertEqual(state.drain_events(),[])
  with self.assertRaises(ValueError):state.ready(ids[2]) # No silent reconnect.
  self.assertFalse(state.can_continue_after('local')) # Preserve renderer exclusion safety.
 def test_last_output_loss_stops_session(self):
  state=OutputState([row(1)['id']]);state.ready(row(1)['id']);self.assertFalse(state.can_continue_after(row(1)['id']))
if __name__=='__main__':unittest.main()
