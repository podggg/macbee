import contextlib,errno,io,json,os,pathlib,sys,unittest
from unittest.mock import Mock,patch

ROOT=pathlib.Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'tools'))

from application_transport import ApplicationTransport
from audio_guardian import GuardianLease,changed_setting,main as guardian_main
from configured_transport import ConfiguredTransport
from receiver_registry import AUTHORIZATION
from speaker_transport import final_guard


def run_guardian(args,run_side_effect,wait_fn,read_fn=lambda *a:b'STOP\n'):
    child=Mock(pid=123,returncode=0)
    child.poll.return_value=None
    clock=[0.0]
    def wait(*_args):
        res=wait_fn(clock)
        return res
    out=io.StringIO()
    exited=None
    with patch('sys.argv',args), \
         patch('audio_guardian.subprocess.Popen',return_value=child), \
         patch('audio_guardian.subprocess.run',side_effect=run_side_effect), \
         patch('audio_guardian.signal.signal'), \
         patch('audio_guardian.time.monotonic',side_effect=lambda:clock[0]), \
         patch('audio_guardian.select.select',side_effect=wait), \
         patch('audio_guardian.os.read',side_effect=read_fn), \
         patch('audio_guardian.os.killpg'), \
         contextlib.redirect_stdout(out):
        try:
            guardian_main()
        except SystemExit as e:
            exited=e.code
    final_line=out.getvalue().split('MACBEE_GUARD_FINAL ')[1]
    return exited,json.loads(final_line),child


class GuardianProbeTests(unittest.TestCase):
    def test_changed_setting_prefers_device_uid_and_falls_back_to_device_id(self):
        before=dict(device_id=84,device_uid='BuiltInSpeakerDevice',sample_rate=48000,input_streams=0,volume=.25,mute=0)
        # Same device_uid across HAL AudioObjectID shift must be ignored.
        self.assertIsNone(changed_setting(before,dict(before,device_id=91)))
        # Different device_uid even with same numeric device_id must stop.
        self.assertEqual(changed_setting(before,dict(before,device_uid='BuiltInHeadphoneOutputDevice')),'device_uid')
        # Missing or empty device_uid on one side must fail closed.
        self.assertEqual(changed_setting(before,{k:v for k,v in before.items() if k!='device_uid'}),'device_uid')
        self.assertEqual(changed_setting(before,dict(before,device_uid='')),'device_uid')
        self.assertEqual(changed_setting({k:v for k,v in before.items() if k!='device_uid'},before),'device_uid')
        # Compatibility fallback when device_uid is absent on both sides.
        legacy=dict(device_id=84,sample_rate=48000,input_streams=0,volume=.25,mute=0)
        self.assertIsNone(changed_setting(legacy,dict(legacy)))
        self.assertEqual(changed_setting(legacy,dict(legacy,device_id=85)),'device_id')
        # Genuine safety fields still fail when device_uid matches.
        for key,bad in [('sample_rate',44100),('input_streams',1),('volume',.5),('mute',1)]:
            self.assertEqual(changed_setting(before,dict(before,**{key:bad})),key)
        # final_guard accepts shifted device_id with identical device_uid, rejects changed device_uid.
        good=dict(before=before,after=dict(before,device_id=99,volume=.5),client_exit=0,reason='stop-command',error=None)
        self.assertEqual(final_guard('MACBEE_GUARD_FINAL '+json.dumps(good),allow_user_levels=True),good)
        bad=dict(good,after=dict(before,device_uid='BuiltInHeadphoneOutputDevice'))
        with self.assertRaisesRegex(ValueError,'device_uid'):
            final_guard('MACBEE_GUARD_FINAL '+json.dumps(bad),allow_user_levels=True)

    def test_guardian_survives_device_id_shift_when_device_uid_matches(self):
        base=dict(active_other_outputs=2,mute=0,volume=.25,input_streams=0,sample_rate=48000,device_id=84,device_uid='BuiltInSpeakerDevice')
        inspections=[
            Mock(returncode=0,stdout=json.dumps(base),stderr=''),
            Mock(returncode=0,stdout=json.dumps(dict(base,device_id=85)),stderr=''),
            Mock(returncode=0,stdout=json.dumps(dict(base,device_id=92)),stderr=''),
            Mock(returncode=0,stdout=json.dumps(base),stderr=''),
        ]
        steps=[0]
        def wait(clock):
            clock[0]+=.6;steps[0]+=1
            return ([Mock()],[],[]) if steps[0]>=3 else ([],[],[])
        args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture',
              '--manual-session','--allow-user-levels','--system-audio','--lease-seconds','3','--max-seconds','0']
        code,row,_=run_guardian(args,inspections,wait)
        self.assertIsNone(code)
        self.assertIsNone(row['error'])
        self.assertEqual(row['reason'],'stop-command')
        self.assertEqual(row['before']['device_uid'],'BuiltInSpeakerDevice')
        self.assertEqual(row['after']['device_id'],84)

    def test_guardian_stops_immediately_on_device_uid_change(self):
        before=dict(active_other_outputs=0,mute=0,volume=.25,input_streams=0,sample_rate=48000,device_id=84,device_uid='BuiltInSpeakerDevice')
        switched=dict(before,device_uid='BuiltInHeadphoneOutputDevice')
        inspections=[Mock(returncode=0,stdout=json.dumps(x),stderr='') for x in (before,switched,switched)]
        steps=[0]
        def wait(clock):
            clock[0]+=.6;steps[0]+=1
            return ([],[],[])
        args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture',
              '--manual-session','--allow-user-levels','--system-audio','--lease-seconds','3','--max-seconds','0']
        code,row,_=run_guardian(args,inspections,wait)
        self.assertEqual(code,1)
        self.assertEqual(row['error'],'speaker setting changed: device_uid')

    def test_guardian_stops_immediately_on_definite_errors_and_unsafe_preflight(self):
        base=dict(active_other_outputs=0,mute=0,volume=.25,input_streams=0,sample_rate=48000,device_id=84,device_uid='BuiltInSpeakerDevice')
        args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture',
              '--manual-session','--allow-user-levels','--system-audio','--lease-seconds','3','--max-seconds','0']
        for reason in ('default_not_builtin','output_device_has_input_stream','expected_one_output_stream','unsupported_format'):
            err=Mock(returncode=1,stdout=json.dumps(dict(event='error',reason=reason,status=0)),stderr='')
            calls=[0]
            def side_effect(*_a,**_k):
                calls[0]+=1
                return Mock(returncode=0,stdout=json.dumps(base),stderr='') if calls[0]==1 else err
            def wait(clock):
                clock[0]+=.6
                return ([],[],[])
            code,row,_=run_guardian(args,side_effect,wait)
            self.assertEqual(code,1)
            self.assertEqual(row['error'],'speaker inventory failed: '+reason)
            # 1 startup + 1 loop failure (no transient retry) + 1 finally after
            self.assertEqual(calls[0],3)

        for bad in (dict(base,sample_rate=44100),dict(base,input_streams=1),dict(base,device_uid=''),dict(base,mute=True)):
            calls=[0]
            def side_effect(*_a,**_k):
                calls[0]+=1
                payload=base if calls[0]==1 else bad
                return Mock(returncode=0,stdout=json.dumps(payload),stderr='')
            def wait(clock):
                clock[0]+=.6
                return ([],[],[])
            code,row,_=run_guardian(args,side_effect,wait)
            self.assertEqual(code,1)
            self.assertIn('speaker safety preflight failed',row['error'])
            self.assertEqual(calls[0],3)

    def test_guardian_tolerates_single_transient_glitch_and_fails_on_two(self):
        base=dict(active_other_outputs=1,mute=0,volume=.25,input_streams=0,sample_rate=48000,device_id=84,device_uid='BuiltInSpeakerDevice')
        args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture',
              '--manual-session','--allow-user-levels','--system-audio','--lease-seconds','3','--max-seconds','0']
        glitches=[
            __import__('subprocess').TimeoutExpired(['probe'],1.0),
            Mock(returncode=0,stdout='{"partial":',stderr=''),
            Mock(returncode=1,stdout=json.dumps(dict(event='error',reason='default_output',status=-1)),stderr=''),
            OSError('transient'),
        ]
        for glitch in glitches:
            ok=Mock(returncode=0,stdout=json.dumps(base),stderr='')
            seq=[ok,glitch,ok,ok,ok]
            steps=[0]
            def wait(clock):
                clock[0]+=.3;steps[0]+=1
                if steps[0]==1:return ([Mock()],[],[])  # PING
                if steps[0]>=5:return ([Mock()],[],[])  # STOP
                return ([],[],[])
            reads=iter([b'PING\n',b'STOP\n'])
            code,row,_=run_guardian(args,seq,wait,lambda *a:next(reads))
            self.assertIsNone(code)
            self.assertIsNone(row['error'])
            self.assertEqual(row['reason'],'stop-command')

        # Two consecutive transient glitches must fail closed.
        ok=Mock(returncode=0,stdout=json.dumps(base),stderr='')
        seq=[ok,glitches[0],glitches[2],ok]
        def wait2(clock):
            clock[0]+=.3
            return ([],[],[])
        code,row,_=run_guardian(args,seq,wait2)
        self.assertEqual(code,1)
        self.assertEqual(row['error'],'speaker inventory failed: default_output')

    def test_guardian_finally_retries_single_transient_glitch_on_clean_stop(self):
        base=dict(active_other_outputs=0,mute=0,volume=.25,input_streams=0,sample_rate=48000,device_id=84,device_uid='BuiltInSpeakerDevice')
        args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture',
              '--manual-session','--allow-user-levels','--system-audio','--lease-seconds','3','--max-seconds','0']
        ok=Mock(returncode=0,stdout=json.dumps(base),stderr='')
        seq=[ok,__import__('subprocess').TimeoutExpired(['probe'],1.0),ok]
        def wait(clock):
            return ([Mock()],[],[])
        code,row,_=run_guardian(args,seq,wait)
        self.assertIsNone(code)
        self.assertIsNone(row['error'])
        self.assertEqual(row['after'],base)

    def test_guardian_manual_vs_strict_active_other_outputs(self):
        base=dict(active_other_outputs=-1,mute=0,volume=.25,input_streams=0,sample_rate=48000,device_id=84,device_uid='BuiltInSpeakerDevice')
        strict_args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture']
        with patch('sys.argv',strict_args),patch('audio_guardian.subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps(base),stderr='')):
            with self.assertRaisesRegex(RuntimeError,'speaker safety preflight failed'):
                guardian_main()
        manual_args=strict_args+['--manual-session','--allow-user-levels','--system-audio','--lease-seconds','3','--max-seconds','0']
        ok=Mock(returncode=0,stdout=json.dumps(base),stderr='')
        code,row,_=run_guardian(manual_args,[ok,ok],lambda clock:([Mock()],[],[]))
        self.assertIsNone(code)
        self.assertIsNone(row['error'])

    def test_guardian_lease_partial_command_at_64_byte_boundary(self):
        lease=GuardianLease(0,3,0)
        self.assertIsNone(lease.feed(b'PI',1))
        chunk=b'NG\n'+b'PING\n'*12+b'P'  # 64 bytes -> pending becomes 66 bytes before split, 1 byte after
        self.assertEqual(len(chunk),64)
        self.assertIsNone(lease.feed(chunk,2))
        self.assertIsNone(lease.expired(4.9))
        self.assertEqual(GuardianLease(0,3,0).feed(b'x'*65,1),'invalid-command')

    def test_configured_and_application_transport_heartbeat_eagain_and_partial_writes(self):
        c=dict(receivers=[dict(id='r-0000000000000001',name='Test 1',ssh_host='fixture-1',remote_root='/fixture/receiver',client_sha256='0'*64,authorization=AUTHORIZATION)])
        s=ConfiguredTransport(['local','r-0000000000000001'],c)
        s.server=Mock(returncode=None);s.server.poll.return_value=None
        s.peers={}
        for i,key in enumerate(s.outputs):
            child=Mock(pid=100+i,returncode=None);child.poll.return_value=None;child.stdin.fileno.return_value=10+i
            s.peers[key]=child;s.output_state.ready(key)
        s.ready=True;s.processes=[s.server]+list(s.peers.values());s.next_capacity_check=float('inf')

        # Local succeeds (5); remote hits EAGAIN, then writes 2 bytes, then hits EAGAIN across a new 0.5s tick, then flushes remaining 3 bytes.
        remote_replies=iter([BlockingIOError(errno.EAGAIN,'busy'),2,BlockingIOError(errno.EAGAIN,'busy'),3,5])
        sent={10:[],11:[]}
        def fake_write(fd,data):
            if fd==10:
                sent[10].append(data)
                return len(data)
            r=next(remote_replies)
            if isinstance(r,BaseException):raise r
            sent[11].append(data[:r])
            return r

        now=[1.0]
        with patch('configured_transport.os.write',side_effect=fake_write), \
             patch('configured_transport.time.monotonic',side_effect=lambda:now[0]):
            s.check()  # t=1.0: local=5, remote=EAGAIN -> still online!
            self.assertEqual(s.failed_outputs,set())
            now[0]=1.02;s.check()  # remote writes 2 ('PI')
            now[0]=1.60;s.check()  # new 0.5s tick: remote EAGAIN, must NOT prepend another PING\n
            now[0]=1.62;s.check()  # remote flushes remaining 3 ('NG\n')
            now[0]=2.20;s.check()  # next clean heartbeat (5)
        self.assertEqual(s.failed_outputs,set())
        self.assertEqual(b''.join(sent[11]),b'PING\nPING\n')
        self.assertEqual(s.drain_events(),[])

        # Real non-blocking pipe round-trip with partial tail flushed cleanly on close().
        r_fd,w_fd=os.pipe()
        try:
            os.set_blocking(w_fd,False)
            os.set_blocking(r_fd,False)
            s._heartbeat_pending['r-0000000000000001']=[b'NG\n',1.0]
            w_file=os.fdopen(w_fd,'wb',buffering=0)
            s.peers['r-0000000000000001'].stdin=w_file
            os.write(w_fd,b'PI')
            # close() must send tail 'NG\n' + 'STOP\n', not 'STOP\n' directly.
            with patch.object(s,'verify_cleanup',return_value=True):
                s.peers['local'].poll.return_value=0
                s.peers['r-0000000000000001'].poll.side_effect=[None,0,0,0,0,0]
                s.server.poll.return_value=0
                s.close()
            received=os.read(r_fd,64)
            lease=GuardianLease(0,3,0)
            self.assertEqual(lease.feed(received,1.0),'stop-command')
        finally:
            try:os.close(r_fd)
            except OSError:pass

        # Persistent BlockingIOError beyond 2.0s marks remote offline while local stays alive; BrokenPipeError fails immediately.
        s2=ConfiguredTransport(['local','r-0000000000000001'],c)
        s2.server=Mock(returncode=None);s2.server.poll.return_value=None
        s2.peers={}
        for i,key in enumerate(s2.outputs):
            child=Mock(pid=200+i,returncode=None);child.poll.return_value=None;child.stdin.fileno.return_value=20+i
            s2.peers[key]=child;s2.output_state.ready(key)
        s2.ready=True;s2.processes=[s2.server]+list(s2.peers.values());s2.next_capacity_check=float('inf')
        now=[10.0]
        def persistent_block(fd,data):
            if fd==20:return len(data)
            raise BlockingIOError(errno.EAGAIN,'wedged')
        with patch('configured_transport.os.write',side_effect=persistent_block), \
             patch('configured_transport.time.monotonic',side_effect=lambda:now[0]):
            s2.check()
            self.assertEqual(s2.failed_outputs,set())
            now[0]=11.9;s2.check()
            self.assertEqual(s2.failed_outputs,set())
            now[0]=12.1;s2.check()
            self.assertEqual(s2.failed_outputs,{'r-0000000000000001'})
            self.assertEqual(s2.drain_events(),[dict(output='r-0000000000000001',state='offline')])

        # ApplicationTransport tolerates transient EAGAIN/partial write and fails on persistent timeout or BrokenPipeError.
        app=ApplicationTransport(manual_session=True)
        app.local=Mock(pid=1,returncode=None);app.local.poll.return_value=None;app.local.stdin.fileno.return_value=30
        app.remote=Mock(pid=2,returncode=None);app.remote.poll.return_value=None;app.remote.stdin.fileno.return_value=31
        app.server=Mock(pid=3,returncode=None);app.server.poll.return_value=None
        app.processes=[app.server,app.local,app.remote]
        app_remote=iter([BlockingIOError(errno.EAGAIN,'busy'),3,2,BrokenPipeError(errno.EPIPE,'gone')])
        app_now=[1.0]
        def app_write(fd,data):
            if fd==30:return len(data)
            r=next(app_remote)
            if isinstance(r,BaseException):raise r
            return r
        with patch('application_transport.guard'),patch('application_transport.time.monotonic',side_effect=lambda:app_now[0]), \
             patch('application_transport.os.write',side_effect=app_write):
            app.check()
            app_now[0]=1.05;app.check()
            app_now[0]=1.10;app.check()
            app_now[0]=2.00
            with self.assertRaisesRegex(RuntimeError,'Renderer heartbeat failed'):
                app.check()
            self.assertEqual(app.last_failure_peer,'remote')
            self.assertEqual(app.last_failure_errno,errno.EPIPE)

        # ApplicationTransport.stop_clients() prepends any partial PING tail before STOP\n,
        # and does not prepend unsent full PING\n.
        app2=ApplicationTransport(manual_session=True)
        app2.local=Mock(pid=1,returncode=0);app2.local.poll.return_value=None
        app2.remote=Mock(pid=2,returncode=0);app2.remote.poll.return_value=None
        app2._heartbeat_pending['remote']=[b'NG\n',1.0]
        app2._heartbeat_pending['local']=[b'PING\n',1.0]
        app2.stop_clients()
        app2.remote.stdin.write.assert_called_once_with(b'NG\nSTOP\n')
        app2.local.stdin.write.assert_called_once_with(b'STOP\n')


if __name__=='__main__':unittest.main()
