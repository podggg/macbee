import pathlib,sys,unittest,types,struct,tempfile,json,hashlib,signal
from unittest.mock import Mock,patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
from configured_transport import ConfiguredTransport
from run_system_relay import run
from run_application_relay import run as run_application
from session_diagnostics import StopTrace,RelaySignal
from receiver_registry import AUTHORIZATION

def config():return dict(receivers=[dict(id='r-%016x'%i,name='Test '+str(i),ssh_host='fixture-'+str(i),remote_root='/fixture/receiver',client_sha256='0'*64,authorization=AUTHORIZATION) for i in range(1,5)])
def receipt(**changes):
 before=dict(device_id=1,sample_rate=48000,input_streams=0,volume=.25,mute=0)
 row=dict(before=before,after=dict(before),client_exit=0,reason='stop-command',error=None)
 row.update(changes);return 'MACBEE_GUARD_FINAL '+json.dumps(row)
class ConfiguredTransportTests(unittest.TestCase):
 def session(self,local=True):
  c=config();ids=(['local'] if local else [])+[x['id'] for x in c['receivers']]
  s=ConfiguredTransport(ids,c);s.server=Mock(returncode=None);s.server.poll.return_value=None;s.peers={}
  for i,key in enumerate(ids):
   child=Mock(pid=100+i,returncode=None);child.poll.return_value=None;child.stdin.fileno.return_value=10+i;s.peers[key]=child;s.output_state.ready(key)
  s.ready=True;s.processes=[s.server]+list(s.peers.values());s.next_capacity_check=float('inf');return s
 def test_no_processes_or_network_from_construction(self):
  with patch('subprocess.Popen',side_effect=AssertionError('No processes')),patch('socket.socket',side_effect=AssertionError('No network')):
   s=ConfiguredTransport([x['id'] for x in config()['receivers']],config());self.assertEqual(len(s.outputs),4)
 def test_one_remote_loss_keeps_other_three_and_local_heartbeats(self):
  s=self.session();lost=s.outputs[2];s.peers[lost].poll.return_value=255;s.peers[lost].returncode=255
  writes=[]
  with patch('configured_transport.os.write',side_effect=lambda fd,data:(writes.append(fd) or len(data))):s.check()
  self.assertEqual(len(writes),4);self.assertNotIn(s.peers[lost].stdin.fileno(),writes)
  self.assertEqual(s.drain_events(),[dict(output=lost,state='offline')]);self.assertEqual(len(s.output_state.remaining()),4)
 def test_local_exclusion_identity_or_shared_server_loss_stops_all(self):
  for key in ('local','server'):
   s=self.session();child=s.server if key=='server' else s.peers[key];child.poll.return_value=0
   with self.assertRaisesRegex(RuntimeError,'peer exited'):s.check()
 def test_prior_output_loss_is_not_a_substitute_for_cleanup_evidence(self):
  s=self.session();lost=s.outputs[1];s.peers[lost].poll.return_value=0
  with patch('configured_transport.os.write',side_effect=lambda fd,data:len(data)):s.check()
  self.assertIn(lost,s.failed_outputs);self.assertEqual(s.output_state.states[lost],'offline')
  good=[receipt(reason='control-eof') if key==lost else receipt() for key in s.outputs]
  with patch.object(s,'logs',return_value=good):
   self.assertTrue(s.verify_cleanup([0]*(len(s.outputs)+1)))
   for codes in ([0]*len(s.outputs),[0]*len(s.outputs)+[None],[0]*len(s.outputs)+[255]):
    self.assertFalse(s.verify_cleanup(codes))
  for bad in ('',receipt(reason='lease-expired'),receipt(reason='renderer-exit'),receipt(reason='unknown'),receipt(reason='signal'),
              receipt(client_exit=None),receipt(client_exit=1),receipt(error='fixture failure'),receipt()+'\n'+receipt()):
   with self.subTest(receipt=bad),patch.object(s,'logs',return_value=[bad]+good[1:]):
    self.assertFalse(s.verify_cleanup([0]*(len(s.outputs)+1)))
  with patch.object(s,'logs',return_value=good[:-1]):self.assertFalse(s.verify_cleanup([0]*(len(s.outputs)+1)))
  self.assertIn(lost,s.failed_outputs) # Never erase independent loss history.
 def test_both_relays_preserve_loss_history_then_confirm_evidenced_stop(self):
  for application in (False,True):
   with self.subTest(application=application),tempfile.TemporaryDirectory() as tmp:
    s=self.session(local=False);s.run_dir=pathlib.Path(tmp);s.next_heartbeat=float('inf');clock=[0.0];steps=[0];writes=[];events=[]
    s.start=lambda:None;s.write=lambda pcm:writes.append(pcm);lost=s.outputs[0]
    def close():
     # Independent final receipts and exit codes, not a lease assumption.
     s.cleanup_verified=s.verify_cleanup([0]*(len(s.outputs)+1))
    s.close=close
    def read():
     steps[0]+=1;clock[0]+=.02
     if steps[0]==3:s.peers[lost].poll.return_value=0
     if steps[0]>8:raise RelaySignal(signal.SIGTERM) # Native explicit Stop requests backend termination.
     pcm=struct.pack('<4sI',b'MBP2',960)+struct.pack('<hh',100,100)*960
     return pcm if application else struct.pack('<4sI',b'MBH2',2)+pcm
    trace=StopTrace(lambda:clock[0]);emit=lambda kind,**row:events.append((kind,row))
    with patch.object(s,'logs',return_value=[receipt(reason='control-eof')]+[receipt()]*3),patch('configured_transport.time.monotonic',side_effect=lambda:clock[0]),patch('subprocess.Popen',side_effect=AssertionError('No processes')),patch('socket.socket',side_effect=AssertionError('No network')):
     result=run_application(s,read,emit,lambda:clock[0],manual_session=True,trace=trace) if application else run(s,read,emit,lambda:clock[0],trace=trace)
    self.assertGreaterEqual(len(writes),8);self.assertEqual(len(s.output_state.remaining()),3)
    self.assertTrue(result['cleanup_verified']);self.assertNotIn('error',result);self.assertEqual(events[-1][0],'stopped')
    self.assertEqual(trace.first,'signal_term');self.assertEqual(s.failed_outputs,{lost})
    self.assertEqual([row for kind,row in events if kind=='output'],[dict(output=lost,state='offline')])
    for name in ('backend-first-stop.json','backend-final.json'):
     evidence=json.loads((s.run_dir/name).read_text());self.assertEqual(evidence['first_cause'],'signal_term')
     self.assertEqual([row['output'] for row in evidence['output_losses']],[lost])
 def test_duplicate_mac_or_sender_alias_rejected_before_server_or_capture(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);binary=root/'client';binary.write_bytes(b'fixture-not-executable');guardian=root/'guardian';guardian.write_bytes(b'fixture')
   (root/'application-client.json').write_text(json.dumps(dict(sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),format='48000:16:2',stop_mode='wait-for-process-exit')))
   for identity in ('a'*64,'b'*64):
    s=ConfiguredTransport([x['id'] for x in config()['receivers']],config());s.local_binary=binary;s.local_guardian=guardian
    with patch('configured_transport.ASSET_ROOT',root),patch('configured_transport.guard'),patch('receiver_setup.local_device_identity',return_value='a'*64),patch('receiver_setup.rpc',return_value=dict(device_sha256=identity)),patch('configured_transport.NullTransport.start',side_effect=AssertionError('No server/audio')):
     with self.assertRaisesRegex(RuntimeError,'duplicated'):s.start()
 def test_complete_relay_continues_after_one_of_four_synthetic_receivers_drops(self):
  s=self.session(local=False);clock=[0.0];n=[0];writes=[];events=[]
  s.start=lambda:None;s.write=lambda pcm:writes.append(pcm)
  s.logs=lambda:[receipt()]*len(s.outputs)
  def close():s.cleanup_verified=s.verify_cleanup([0,255]+[0]*(len(s.outputs)-1))
  s.close=close
  def read():
   n[0]+=1;clock[0]+=.02
   if n[0]==3:s.peers[s.outputs[0]].poll.return_value=255
   if n[0]>8:return struct.pack('<4sI',b'MBP2',0)
   return struct.pack('<4sI',b'MBH2',2)+struct.pack('<4sI',b'MBP2',960)+struct.pack('<hh',100,100)*960
  with patch('configured_transport.os.write',side_effect=lambda fd,data:len(data)),patch('configured_transport.time.monotonic',side_effect=lambda:clock[0]),patch('subprocess.Popen',side_effect=AssertionError('No processes')):
   result=run(s,read,lambda event,**row:events.append((event,row)),lambda:clock[0])
  self.assertGreaterEqual(len(writes),8);self.assertEqual(len(s.output_state.remaining()),3)
  self.assertEqual(result['source']['frames'],8*960);self.assertIn('unconfirmed',result['error'])
  self.assertEqual(sum(e=='ready' for e,row in events),1)
  self.assertEqual([row for e,row in events if e=='output'],[dict(output=s.outputs[0],state='offline')])
if __name__=='__main__':unittest.main()
