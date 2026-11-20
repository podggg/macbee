import contextlib,io,json,os,pathlib,signal,stat,struct,sys,tempfile,types,unittest
from unittest.mock import Mock,patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
from session_diagnostics import StopTrace,RelaySignal
from run_system_relay import run
from audio_guardian import GuardianLease,main as guardian_main
from speaker_transport import final_guard
HEALTH=struct.pack('<4sI',b'MBH2',2);END=struct.pack('<4sI',b'MBP2',0)
class FakeTransport:
 outputs=['local','remote'];sinks=['fixture','fixture'];renderer_identity=None
 def __init__(self):self.cleanup_verified=False;self.closed=False
 def start(self):pass
 def check(self):pass
 def write(self,payload):pass
 def close(self):self.closed=True;self.cleanup_verified=True
class StopDiagnosticsTests(unittest.TestCase):
 def execute(self,kind):
  now=[0.0];step=[0];s=FakeTransport();events=[];trace=StopTrace(lambda:now[0],'00000000-0000-0000-0000-000000000001')
  def read():
   step[0]+=1;now[0]+=.02
   if kind=='eof':return b''
   if kind=='signal':raise RelaySignal(signal.SIGTERM)
   if kind=='heartbeat':return HEALTH if step[0]==1 else None
   if kind=='pacing':now[0]+=.251;return HEALTH
   if kind=='protocol':return b'BAD!'+bytes(4)
   return HEALTH if step[0]==1 else END
  if kind=='backpressure':s.write=lambda payload:(_ for _ in ()).throw(TimeoutError('PCM pipe backpressure'))
  if kind=='ssh':s.check=lambda:(_ for _ in ()).throw(RuntimeError('Transport peer exited'))
  if kind=='private_error':s.write=lambda payload:(_ for _ in ()).throw(OSError('private secret /Users/someone/host'))
  with patch('subprocess.Popen',side_effect=AssertionError('No real process')),patch('socket.socket',side_effect=AssertionError('No network')):
   result=run(s,read,lambda event,**data:events.append((event,data)),lambda:now[0],trace)
  self.assertTrue(s.closed);self.assertTrue(result['cleanup_verified']);return result,trace,events
 def test_distinct_first_causes_survive_cleanup(self):
  for kind,code in [('eof','source_eof'),('signal','signal_term'),('heartbeat','source_health_timeout'),
   ('pacing','output_pacing_stall'),('backpressure','pcm_backpressure'),('ssh','transport_peer_exit'),('protocol','protocol_failed')]:
   with self.subTest(kind=kind):
    result,trace,events=self.execute(kind);self.assertEqual(result['reason_code'],code)
    self.assertEqual(trace.first_snapshot['first_cause'],code);self.assertNotEqual(trace.first_snapshot['phase'],'cleanup')
    trace.cause('cleanup_failed');self.assertEqual(trace.first,code)
    self.assertEqual([x for x in events if x[0]=='stopping'][0][1]['reason_code'],code)
 def test_250ms_pacing_late_is_reproduced_not_called_original_cause(self):
  result,trace,_=self.execute('pacing');self.assertGreater(trace.first_snapshot['metrics']['pacing_late'],.25)
  self.assertIn('pacing stalled',result['error']);self.assertGreaterEqual(trace.first_snapshot['metrics']['max_loop_gap'],.251)
 def test_private_text_never_enters_saved_diagnostic(self):
  result,trace,_=self.execute('private_error');self.assertEqual(trace.first,'pcm_write_failed')
  with tempfile.TemporaryDirectory() as name:
   directory=pathlib.Path(name);self.assertTrue(trace.save(directory));self.assertTrue(trace.save(directory,final=True))
   for p in directory.iterdir():
    data=p.read_bytes();self.assertLessEqual(len(data),8192);self.assertNotIn(b'private secret',data);self.assertNotIn(b'/Users/',data)
    self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
   self.assertFalse(trace.save(directory)) # First record cannot be overwritten.
 def test_transport_exit_identifies_only_role_and_exit_number(self):
  from application_transport import ApplicationTransport
  session=ApplicationTransport(manual_session=True)
  session.server=Mock();session.server.poll.return_value=None
  session.remote=Mock();session.remote.poll.return_value=255
  session.processes=[session.server,session.remote]
  trace=StopTrace(lambda:0)
  with self.assertRaisesRegex(RuntimeError,'Transport peer exited') as failure:session.check()
  trace.note_transport(session,failure.exception);trace.cause('transport_peer_exit')
  self.assertEqual(trace.first_snapshot['transport'],dict(peer='remote',exit=255))
 def test_symlink_and_unknown_metric_refused(self):
  trace=StopTrace(lambda:1);trace.cause('not a valid cause')
  with self.assertRaises(ValueError):trace.mark('audio',1)
  with tempfile.TemporaryDirectory() as name:
   directory=pathlib.Path(name);target=directory/'untouched';target.write_text('keep')
   (directory/'backend-first-stop.json').symlink_to(target)
   self.assertFalse(trace.save(directory));self.assertEqual(target.read_text(),'keep')
 def test_nonterminal_loss_is_bounded_and_not_a_first_stop(self):
  trace=StopTrace(lambda:1)
  for key in ('private-host','/Users/someone','r-'+'0'*16):
   trace.note_output_loss(dict(output=key,state='offline'))
  for i in range(20):trace.note_output_loss(dict(output='r-%016x'%i,state='offline'))
  self.assertIsNone(trace.first);self.assertEqual(len(trace.output_losses),16)
  trace.cause('source_health_timeout');first=trace.first_snapshot
  trace.note_output_loss(dict(output='remote',state='offline'));trace.cause('cleanup_failed')
  self.assertEqual(trace.first,'source_health_timeout');self.assertEqual(first,trace.first_snapshot)
  data=json.dumps(first);self.assertNotIn('private-host',data);self.assertNotIn('/Users/',data)
 def test_guardian_stop_eof_and_natural_exit_are_distinct(self):
  self.assertEqual(GuardianLease(0,3,0).feed(b'STOP\n',1),'stop-command')
  self.assertEqual(GuardianLease(0,3,0).feed(b'',1),'control-eof')
  before=dict(active_other_outputs=0,mute=0,volume=.5,input_streams=0,sample_rate=48000,device_id=1)
  child=Mock(pid=123,returncode=0);child.poll.return_value=0
  args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture',
        '--manual-session','--allow-user-levels','--lease-seconds','3','--max-seconds','0']
  with patch('sys.argv',args),patch('audio_guardian.subprocess.Popen',return_value=child), \
   patch('audio_guardian.subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps(before),stderr='')), \
   patch('audio_guardian.signal.signal'),patch('audio_guardian.os.killpg',side_effect=AssertionError('No signal')),contextlib.redirect_stdout(io.StringIO()) as out:
   guardian_main()
  row=json.loads(out.getvalue().split('MACBEE_GUARD_FINAL ')[1]);self.assertEqual(row['reason'],'renderer-exit')
  with self.assertRaises(ValueError):final_guard('MACBEE_GUARD_FINAL '+json.dumps(row),allow_user_levels=True)
if __name__=='__main__':unittest.main()
