import pathlib,sys,struct,unittest,json,types
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
from system_audio_protocol import SystemAudioDecoder
from system_audio_transport import SystemAudioTransport
from run_system_relay import run
def health(state=2):return struct.pack('<4sI',b'MBH2',state)
def pcm(value,frames=960):return struct.pack('<4sI',b'MBP2',frames)+struct.pack('<hh',value,value)*frames
END=struct.pack('<4sI',b'MBP2',0)
class Fake:
    outputs=['local','remote'];sinks=['fixture']*2;renderer_identity={'guardian_pid':1,'renderer_pid':2}
    def __init__(self):self.writes=[];self.cleanup_verified=False;self.closed=False
    def start(self):pass
    def check(self):pass
    def write(self,p):self.writes.append(p)
    def close(self):self.closed=True;self.cleanup_verified=True
class SystemProtocolTests(unittest.TestCase):
    def test_fragmented_stamped_frames_expire_without_losing_control_or_alignment(self):
        now=1_000_000_000;d=SystemAudioDecoder(stamp_clock=lambda:now)
        def packet(stamp,x):
            return struct.pack('<4sIQhh',b'MBP3',1,stamp,x,-x)
        d.require_stamps=True
        with self.assertRaisesRegex(ValueError,'Missing system packet timestamp'):d.feed(health()+pcm(1))
        events=[]
        for b in health()+packet(now-250_000_000,17)+packet(now-250_000_000,19):
            events+=d.feed(bytes([b]))
        now+=300_000_000
        for b in packet(now-250_000_001,2)+packet(now-1,5)+END:events+=d.feed(bytes([b]))
        self.assertTrue(d.reanchor);self.assertEqual(d.dropped_frames,2);self.assertTrue(d.ended)
        self.assertEqual(events,[('health',2),('pcm',struct.pack('<hh',17,-17)),('pcm',struct.pack('<hh',19,-19))])
        for stamp in (0,now+1):
            with self.assertRaises(ValueError):SystemAudioDecoder(stamp_clock=lambda:now).feed(health()+packet(stamp,4))
        with self.assertRaises(ValueError):SystemAudioDecoder(stamp_clock=lambda:now).feed(health()+packet(now,4)+packet(now-1,4))

    def test_hardware_capture_age_uses_uptime_not_server_or_consumer_time(self):
        # The same machine slept for 18 hours before this session. In this test
        # a 150 ms late consumer still delivers the captured frame; 251 ms does not.
        host=[5_000_000_000];continuous=lambda:host[0]+18*3600*10**9
        def pkt(kind,ns):return struct.pack('<4sIQhh',kind,1,ns,31,-31)
        d=SystemAudioDecoder(stamp_clock=continuous,host_clock=lambda:host[0])
        d.require_stamps=True;d.feed(health());captured=host[0]
        host[0]+=150_000_000
        self.assertEqual(d.feed(pkt(b'MBP4',captured)),[('pcm',struct.pack('<hh',31,-31))])
        host[0]+=101_000_000
        self.assertEqual(d.feed(pkt(b'MBP4',captured)),[]);self.assertEqual(d.dropped_frames,1)
        with self.assertRaisesRegex(ValueError,'domain changed'):d.feed(pkt(b'MBP3',continuous()))
        # Recovery may occur before the first PCM announces its clock domain.
        d=SystemAudioDecoder(stamp_clock=continuous,host_clock=lambda:host[0]);d.feed(health());d.discard_through_now()
        self.assertEqual(d.feed(pkt(b'MBP4',host[0])),[])
        host[0]+=20_000_000;self.assertEqual(d.feed(pkt(b'MBP4',host[0])),[('pcm',struct.pack('<hh',31,-31))])

    def test_accumulated_source_drops_oldest_whole_frames_without_ending(self):
        clock=[0.0];i=[0];session=Fake();session.bounded_pacing=True
        def read():
            i[0]+=1;clock[0]+=.005
            if i[0]<=8:
                from reader_clock import shared_clock_ns
                return health()+struct.pack('<4sIQ',b'MBP3',4096,shared_clock_ns())+struct.pack('<hh',i[0],i[0])*4096
            if i[0]<=40:return health()
            return END
        result=run(session,read,lambda *a,**k:None,lambda:clock[0])
        self.assertNotIn('error',result);self.assertTrue(result['cleanup_verified'])
        self.assertGreater(result['discarded_source_frames'],16384)
        self.assertTrue(all(len(p)==3840 for p in session.writes))
        self.assertTrue(any(struct.unpack_from('<h',p)[0]==8 for p in session.writes))
        self.assertEqual(result['reason_code'],'source_end')

    def test_health_is_not_captured_pcm(self):
        d=SystemAudioDecoder();events=[]
        for b in health(1)+health(2)+pcm(100)+END:events+=d.feed(bytes([b]))
        self.assertEqual([k for k,p in events],['health','health','pcm']);self.assertEqual(d.pcm.frames,960)
        self.assertTrue(d.ended)
    def test_no_pcm_before_activation_or_phase_rollback(self):
        for data in (pcm(1),health(2)+health(1),health(7),END+b'x'):
            with self.assertRaises(ValueError):SystemAudioDecoder().feed(data)
    def test_silent_start_past_old_timeout_then_two_sources_without_restart(self):
        clock=[0.0];step=[0];s=Fake();events=[]
        def read():
            step[0]+=1;clock[0]+=.02
            if step[0]<800:return health(2) # Sixteen seconds of healthy silence.
            if step[0]<805:return health(2)+pcm(100) # Synthetic source A.
            if step[0]<810:return health(2)+pcm(100+300) # A+B, no second Start.
            if step[0]<815:return health(2)+pcm(100) # B stopped, A continues.
            return END
        with patch('subprocess.Popen',side_effect=AssertionError('No processes')),patch('socket.socket',side_effect=AssertionError('No network')):
            result=run(s,read,lambda k,**d:events.append((k,d)),lambda:clock[0])
        self.assertNotIn('error',result);self.assertTrue(s.closed);self.assertGreater(result['padding_frames'],48000*12)
        self.assertEqual(result['source']['frames'],15*960)
        values={struct.unpack_from('<h',p)[0] for p in s.writes};self.assertTrue({0,100,400}<=values)
        self.assertEqual(sum(k=='ready' for k,d in events),1)
        self.assertIn(('state',{'state':'waiting'}),events);self.assertIn(('state',{'state':'playing'}),events)
    def test_lost_health_stops_even_with_pcm_or_partial_bytes(self):
        for value in (pcm(100),b'M'):
            clock=[0.0];s=Fake();n=[0]
            def read():
                n[0]+=1;clock[0]+=.02
                return health(2) if n[0]==1 else value
            result=run(s,read,lambda *a,**k:None,lambda:clock[0])
            self.assertIn('error',result);self.assertTrue(s.closed)
    def test_preparation_remains_bounded(self):
        clock=[0.0];s=Fake()
        def read():clock[0]+=.02;return health(1)
        result=run(s,read,lambda *a,**k:None,lambda:clock[0])
        self.assertIn('preparation',result['error']);self.assertTrue(s.closed)
    def test_stop_eof_and_peer_loss_close_selected_renderers(self):
        for fault in (KeyboardInterrupt(),RuntimeError('peer failed'),None):
            s=Fake()
            def read():
                if fault:raise fault
                return b''
            result=run(s,read,lambda *a,**k:None)
            self.assertTrue(s.closed);self.assertTrue(result['cleanup_verified'])
    def test_outputs_are_fixed_ids_and_no_processes_on_construction(self):
        with patch('subprocess.Popen',side_effect=AssertionError('No process')):
            for outputs in (['local'],['remote'],['local','remote']):
                t=SystemAudioTransport(outputs);self.assertEqual(t.outputs,outputs)
                self.assertIn('audio_guardian-system-v1.py',t.remote_guardian)
            for outputs in ([],['unknown'],['local','local'],['host.example']):
                with self.assertRaises(ValueError):SystemAudioTransport(outputs)
    def test_local_only_does_not_require_or_connect_a_receiver(self):
        with patch('system_audio_transport.require_receiver',side_effect=RuntimeError('not paired')) as check:
            SystemAudioTransport(['local']).validate_configuration();check.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'not paired'):SystemAudioTransport(['remote']).validate_configuration()
    def test_exact_system_policy_and_local_owned_identity_required(self):
        policy=dict(version=4,lease_seconds=3,max_seconds=0,level_policy='user-controlled',source_policy='system',concurrency_policy='other-apps-allowed')
        def text(p,guardian=100):
            return 'Player name: coreaudio\nMACBEE_GUARD_READY {}\nMACBEE_GUARD_LEASE '+json.dumps(p)+'\nMACBEE_RENDERER_IDENTITY '+json.dumps(dict(guardian_pid=guardian,renderer_pid=101))+'\n'
        t=SystemAudioTransport(['local']);t.local=types.SimpleNamespace(pid=100)
        with patch.object(t,'logs',return_value=[text(policy)]):t.verify_sinks()
        self.assertEqual(t.renderer_identity['renderer_pid'],101)
        for bad in (text(dict(policy,source_policy='selected-app')),text(policy,guardian=999),text(policy)+text(policy)):
            with patch.object(t,'logs',return_value=[bad]):
                with self.assertRaises(RuntimeError):t.verify_sinks()
if __name__=='__main__':unittest.main()
