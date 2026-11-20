import pathlib,struct,sys,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
from application_protocol import ApplicationDecoder,MAX_TOTAL_FRAMES,SESSION_SECONDS,FIRST_PACKET_SECONDS,RENDERER_MAX_SECONDS,SOURCE_PACKET_SECONDS
from application_transport import ApplicationTransport
from run_application_relay import run
from audio_guardian import GuardianLease

def packet(samples):return struct.pack('<4sI',b'MBP2',len(samples))+b''.join(struct.pack('<hh',*v) for v in samples)
END=struct.pack('<4sI',b'MBP2',0)
class FakeTransport:
    sinks=['CoreAudioPlayer -> selected-app speakers']*2
    def __init__(self,fail=None):self.events=[];self.cleanup_verified=False;self.fail=fail
    def start(self):
        self.events.append('start')
        if self.fail=='start':raise RuntimeError('startup failed')
    def check(self):
        self.events.append('check')
        if self.fail=='peer':raise RuntimeError('peer lost while source silent')
    def write(self,data):
        self.events.append('write')
        if self.fail=='write':raise RuntimeError('peer lost')
    def close(self):self.events.append('renderer_exit');self.cleanup_verified=self.fail!='cleanup'
class ApplicationProtocolTests(unittest.TestCase):
    def test_fragmented_full_range_stereo(self):
        d=ApplicationDecoder();data=packet([(-32768,32767),(164,165),(0,0)])+END;out=[]
        for byte in data:out+=d.feed(bytes([byte]))
        self.assertEqual(out,[data[8:-8]]);self.assertTrue(d.ended)
        self.assertEqual(d.statistics(),dict(frames=3,nonzero_frames=2,peak_int16=32768))
    def test_rejects_wrong_protocol_oversize_and_trailing(self):
        for data in [struct.pack('<4sI',b'MBP1',1),struct.pack('<4sI',b'MBP2',4097),END+b'x']:
            d=ApplicationDecoder()
            with self.assertRaises(ValueError):d.feed(data)
            self.assertEqual(d.buffer,b'')
        with self.assertRaises(ValueError):ApplicationDecoder().feed(b'x'*65537)
    def test_duration_and_abort_clear(self):
        d=ApplicationDecoder();d.frames=MAX_TOTAL_FRAMES
        with self.assertRaises(ValueError):d.feed(packet([(1,1)]))
        d=ApplicationDecoder();d.feed(packet([(1,2)])[:-1]);self.assertTrue(d.buffer);d.clear();self.assertEqual(d.buffer,b'')
    def check_run(self,items,fail=None):
        s=FakeTransport(fail);events=[];iterator=iter(items)
        def read():
            item=next(iterator,b'')
            if isinstance(item,BaseException):raise item
            return item
        def emit(kind,**data):events.append((kind,list(s.events),data))
        with patch('subprocess.Popen',side_effect=AssertionError('No processes')),patch('socket.socket',side_effect=AssertionError('No networking')):
            result=run(s,read,emit)
        self.assertEqual(events[-1][1][-1],'renderer_exit');self.assertNotIn('drain',s.events)
        return result,events
    def test_stop_after_renderer_exit_and_no_drain(self):
        result,events=self.check_run([packet([(20000,-20000)]),KeyboardInterrupt()])
        self.assertEqual(events[-1][0],'stopped');self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['source']['nonzero_frames'],1)
    def test_eof_and_end(self):
        for end in (b'',END):self.assertTrue(self.check_run([packet([(0,0)]),end])[0]['cleanup_verified'])
    def test_start_loss_cleanup_failure_reported(self):
        for fail in ('start','write','peer','cleanup'):
            result,events=self.check_run([packet([(1,1)]),END],fail)
            self.assertIn('error',result);self.assertEqual(events[-1][0],'failed')
            self.assertEqual(result['cleanup_verified'],fail!='cleanup')
    def test_permission_wait_does_not_steal_playback_budget(self):
        clock=[0.0];step=[0];s=FakeTransport();events=[]
        def read():
            step[0]+=1
            if step[0]==1:clock[0]=2.6;return packet([(1024,1024)])
            if step[0]==2:clock[0]=5.6;return None
            raise KeyboardInterrupt()
        with patch('run_application_relay.SESSION_SECONDS',3):
            result=run(s,read,lambda kind,**data:events.append(kind),lambda:clock[0])
        self.assertNotIn('error',result);self.assertEqual(result['reason'],'stop_requested')
        self.assertEqual(events[-1],'stopped');self.assertTrue(result['cleanup_verified'])
    def test_first_packet_cannot_extend_absolute_renderer_lease(self):
        clock=[0.0];step=[0];s=FakeTransport()
        def read():
            step[0]+=1
            if step[0]==1:clock[0]=11.0;return packet([(1024,1024)])
            clock[0]=FIRST_PACKET_SECONDS+SESSION_SECONDS+1;return None
        result=run(s,read,lambda *args,**kwargs:None,lambda:clock[0])
        self.assertIn('deadline',result['error']);self.assertTrue(result['cleanup_verified'])
    def test_silent_source_peer_loss_stops_before_cleanup(self):
        result,events=self.check_run([None],fail='peer')
        self.assertIn('source silent',result['error'])
        stopping=next(row for row in events if row[0]=='stopping')
        self.assertNotIn('renderer_exit',stopping[1])
        self.assertEqual(events[-1][1][-1],'renderer_exit')
    def test_truncated_eof_is_not_a_clean_source_end(self):
        result,_=self.check_run([packet([(1,1)])[:-1],b''])
        self.assertIn('Truncated',result['error']);self.assertTrue(result['cleanup_verified'])
    def test_pcm_remains_bounded_beyond_first_trial(self):
        d=ApplicationDecoder();d.frames=30*48000
        self.assertEqual(len(d.feed(packet([(100,-100)]))),1)
        self.assertEqual(d.frames,30*48000+1)
        self.assertEqual(MAX_TOTAL_FRAMES,1800*48000)
    def test_guardian_short_lease_survives_long_session_and_bounds_disconnect(self):
        lease=GuardianLease(0,3,RENDERER_MAX_SECONDS)
        for tick in range(1,3601):
            now=tick*.5
            self.assertIsNone(lease.expired(now))
            self.assertIsNone(lease.feed(b'PING\n',now))
        self.assertIsNone(lease.expired(1802.99))
        self.assertEqual(lease.expired(1803),'lease-expired')
        self.assertEqual(lease.feed(b'PING\n',1803),'lease-expired')
    def test_guardian_partial_input_eof_stop_and_legacy_cap(self):
        lease=GuardianLease(0,3,RENDERER_MAX_SECONDS)
        self.assertIsNone(lease.feed(b'PI',1));self.assertIsNone(lease.feed(b'NG\n',2))
        self.assertIsNone(lease.expired(4.9));self.assertEqual(lease.feed(b'',4.9),'control-eof')
        self.assertEqual(lease.feed(b'STOP\n',4.9),'stop-command')
        lease=GuardianLease(0,3,RENDERER_MAX_SECONDS)
        self.assertIsNone(lease.feed(b'PI',1));self.assertEqual(lease.expired(3),'lease-expired')
        self.assertEqual(GuardianLease(0).expired(20),'20-second-watchdog')
        self.assertEqual(GuardianLease(0,3,20).expired(20),'session-watchdog')
    def test_guardian_disconnect_runs_owned_child_cleanup_without_audio(self):
        import contextlib,io,json
        from unittest.mock import Mock
        from audio_guardian import main
        before=dict(active_other_outputs=0,mute=0,volume=.5,input_streams=0,sample_rate=48000,device_id=1)
        inspection=Mock(returncode=0,stdout=json.dumps(before),stderr='')
        child=Mock(pid=123,returncode=0);child.poll.return_value=None
        clock=[0.0];reads=[0]
        def wait(*args):
            clock[0]+=.5;reads[0]+=1
            return ([Mock()],[],[]) if reads[0]==1 else ([],[],[])
        with patch('sys.argv',['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture','--lease-seconds','3','--max-seconds',str(RENDERER_MAX_SECONDS)]), \
             patch('audio_guardian.subprocess.Popen',return_value=child),patch('audio_guardian.subprocess.run',return_value=inspection), \
             patch('audio_guardian.signal.signal'),patch('audio_guardian.time.monotonic',side_effect=lambda:clock[0]), \
             patch('audio_guardian.select.select',side_effect=wait),patch('audio_guardian.os.read',return_value=b'PING\n'), \
             patch('audio_guardian.os.killpg') as kill,contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        kill.assert_called_once();child.wait.assert_called_once_with(timeout=2)
        self.assertIn('lease-expired',output.getvalue());self.assertLessEqual(clock[0],4)
    def test_transport_pings_both_peers_without_audio_and_detects_loss(self):
        from unittest.mock import Mock
        session=ApplicationTransport();session.local=Mock();session.remote=Mock()
        self.assertEqual(session.ssh_arguments(),['-o','ServerAliveInterval=1','-o','ServerAliveCountMax=3'])
        session.processes=[session.local,session.remote]
        for child in session.processes:child.poll.return_value=None
        with patch('application_transport.guard'),patch('application_transport.time.monotonic',return_value=10),patch('application_transport.os.write',return_value=5) as write:
            session.check();session.check();self.assertEqual(write.call_count,2)
            session.remote.poll.return_value=255
            with self.assertRaisesRegex(RuntimeError,'peer exited'):session.check()
    def test_renderer_readiness_requires_short_lease(self):
        import json
        s=ApplicationTransport();base='Player name: coreaudio\nMACBEE_GUARD_READY {}\n'
        with patch.object(s,'logs',return_value=[base]*2),self.assertRaisesRegex(RuntimeError,'lease'):s.verify_sinks()
        lease=dict(version=3,lease_seconds=3,max_seconds=RENDERER_MAX_SECONDS,level_policy='fixed')
        with patch.object(s,'logs',return_value=[base+'MACBEE_GUARD_LEASE '+json.dumps(lease)]*2):s.verify_sinks()
    def test_versioned_guardian_identity_preflight(self):
        import hashlib,json,tempfile
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp);binary=root/'client';binary.write_bytes(b'fixture client')
            guardian=root/'guardian.py';guardian.write_bytes(b'fixture guardian')
            client_hash=hashlib.sha256(binary.read_bytes()).hexdigest()
            guardian_hash=hashlib.sha256(guardian.read_bytes()).hexdigest()
            (root/'application-client.json').write_text(json.dumps(dict(sha256=client_hash,stop_mode='wait-for-process-exit',format='48000:16:2')))
            session=ApplicationTransport();session.local_binary=binary;session.local_guardian=guardian
            self.assertTrue(session.remote_guardian.endswith('/audio_guardian-application-v3.py'))
            with patch('application_transport.SSH_HOST','fixture'),patch('application_transport.ROOT',root),patch('application_transport.require_receiver'),patch('application_transport.guard'), \
                 patch('speaker_transport.SpeakerTransport.start') as start,patch('subprocess.run') as run:
                run.side_effect=[Mock(returncode=0,stdout=client_hash),Mock(returncode=0,stdout='wrong')]
                with self.assertRaisesRegex(RuntimeError,'guardian is not staged'):session.start()
                start.assert_not_called()
                run.side_effect=[Mock(returncode=0,stdout=client_hash),Mock(returncode=0,stdout=guardian_hash)]
                session.start();start.assert_called_once()
    def test_manual_pcm_and_fake_time_exceed_thirty_minutes_then_stop(self):
        d=ApplicationDecoder(max_frames=None);d.frames=MAX_TOTAL_FRAMES*4
        self.assertEqual(len(d.feed(packet([(1,-1)]))),1)
        clock=[0.0];s=FakeTransport()
        def read():
            clock[0]+=.5
            if clock[0]>3700:raise KeyboardInterrupt()
            return packet([(0,0)])
        result=run(s,read,lambda *args,**kwargs:None,lambda:clock[0],manual_session=True)
        self.assertNotIn('error',result);self.assertEqual(result['reason'],'stop_requested')
        self.assertGreater(result['source']['frames'],3600);self.assertTrue(result['cleanup_verified'])
    def test_manual_packet_stall_and_partial_bytes_do_not_renew_source_liveness(self):
        for partial in (None,b'M',packet([(0,0)])):
            clock=[0.0];step=[0];s=FakeTransport()
            def read():
                step[0]+=1
                if step[0]==1:return packet([(0,0)])
                clock[0]=SOURCE_PACKET_SECONDS
                return partial
            result=run(s,read,lambda *args,**kwargs:None,lambda:clock[0],manual_session=True)
            self.assertIn('source deadline',result['error']);self.assertTrue(result['cleanup_verified'])
            self.assertEqual(result['source']['frames'],1)
    def test_manual_no_first_packet_still_has_deadline(self):
        clock=[0.0];s=FakeTransport()
        def read():clock[0]=FIRST_PACKET_SECONDS;return None
        result=run(s,read,lambda *args,**kwargs:None,lambda:clock[0],manual_session=True)
        self.assertIn('source deadline',result['error']);self.assertTrue(result['cleanup_verified'])
    def test_manual_guardian_unlimited_heartbeats_still_stop_on_eof_or_lost_lease(self):
        lease=GuardianLease(0,3,0)
        for now in range(1,7201):self.assertIsNone(lease.feed(b'PING\n',now))
        self.assertIsNone(lease.expired(7202.9));self.assertEqual(lease.expired(7203),'lease-expired')
        self.assertEqual(lease.feed(b'PING\n',7203),'lease-expired')
        self.assertEqual(GuardianLease(0,3,0).feed(b'',1),'control-eof')
        self.assertEqual(GuardianLease(0,3,0).feed(b'STOP\n',1),'stop-command')
        with self.assertRaises(ValueError):GuardianLease(0,0,0)
    def test_manual_transport_requires_exact_level_policy_and_v4_lease(self):
        import json
        s=ApplicationTransport(manual_session=True);base='Player name: coreaudio\nMACBEE_GUARD_READY {}\n'
        expected=dict(version=4,lease_seconds=3,max_seconds=0,level_policy='user-controlled',source_policy='selected-app',concurrency_policy='other-apps-allowed')
        self.assertEqual(s.guardian_arguments(),['--lease-seconds','3','--max-seconds','0','--manual-session','--allow-user-levels'])
        for row in (dict(expected,version=1),dict(expected,max_seconds=1830),dict(expected,level_policy='fixed')):
            with patch.object(s,'logs',return_value=[base+'MACBEE_GUARD_LEASE '+json.dumps(row)]*2),self.assertRaisesRegex(RuntimeError,'lease'):s.verify_sinks()
        with patch.object(s,'logs',return_value=[base+'MACBEE_GUARD_LEASE '+json.dumps(expected)]*2):s.verify_sinks()
    def test_manual_eof_and_peer_loss_keep_cleanup_and_failure_detail(self):
        for item in (b'',END):
            s=FakeTransport();items=iter([packet([(0,0)]),item])
            result=run(s,lambda:next(items),lambda *args,**kwargs:None,manual_session=True)
            self.assertTrue(result['cleanup_verified']);self.assertNotIn('error',result)
        s=FakeTransport('peer');s.cleanup_error='Speaker guardian failed: speaker setting changed: device_id'
        result=run(s,lambda:None,lambda *args,**kwargs:None,manual_session=True)
        self.assertIn('peer lost',result['error']);self.assertIn('device_id',result['error'])

    def test_missing_full_range_client_fails_before_process(self):
        with patch('application_transport.require_receiver'),patch('application_transport.guard'),patch('pathlib.Path.is_file',return_value=False),patch('subprocess.Popen',side_effect=AssertionError('No process')):
            with self.assertRaisesRegex(RuntimeError,'not staged'):ApplicationTransport().start()
if __name__=='__main__':unittest.main()
