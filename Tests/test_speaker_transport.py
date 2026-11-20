import json,pathlib,sys,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
from speaker_transport import SpeakerTransport,final_guard
from null_transport import NullTransport

class SpeakerTransportTests(unittest.TestCase):
    def test_constructing_does_not_play_or_connect(self):
        with patch('subprocess.Popen',side_effect=AssertionError('No processes')),patch('socket.socket',side_effect=AssertionError('No sockets')):
            self.assertEqual(NullTransport().sinks,['FilePlayer -> /dev/null']*2)
            self.assertEqual(SpeakerTransport().sinks,['CoreAudioPlayer -> guarded built-in speakers']*2)
    def test_guard_checks_errors_settings_and_exit(self):
        before=dict(device_id=84,sample_rate=48000,input_streams=0,volume=.0625,mute=0)
        good=dict(before=before,after=dict(before),client_exit=0,reason='stop_or_eof',error=None)
        self.assertEqual(final_guard('MACBEE_GUARD_FINAL '+json.dumps(good)),good)
        for bad in [dict(good,error='changed'),dict(good,reason='20-second-watchdog'),dict(good,reason='lease-expired'),dict(good,reason='session-watchdog'),dict(good,client_exit=-15),
                    dict(good,after=dict(before,volume=.5)),dict(good,before={})]:
            with self.assertRaises(ValueError):final_guard('MACBEE_GUARD_FINAL '+json.dumps(bad))
    def test_ordinary_volume_change_is_not_cleanup_failure(self):
        from application_transport import ApplicationTransport
        before=dict(device_id=84,sample_rate=48000,input_streams=0,volume=.25,mute=0)
        for volume in (.3125,0,1):
            good=dict(before=before,after=dict(before,volume=volume),client_exit=0,reason='stop_or_eof',error=None)
            text='MACBEE_GUARD_FINAL '+json.dumps(good)
            self.assertEqual(final_guard(text,allow_user_levels=True),good)
            with self.assertRaises(ValueError):final_guard(text)
            session=ApplicationTransport(manual_session=True)
            with patch.object(session,'logs',return_value=[text]*2):
                self.assertTrue(session.verify_cleanup([0,0,0]));self.assertFalse(session.verify_cleanup([0,1,0]))
        for key,value in [('device_id',99),('sample_rate',44100),('input_streams',1),('mute',-1),('volume',-1)]:
            bad=dict(good,after=dict(before,**{key:value}))
            with self.assertRaises(ValueError):final_guard('MACBEE_GUARD_FINAL '+json.dumps(bad),allow_user_levels=True)
        for old,new in ((0,1),(1,0)):
            row=dict(good,before=dict(before,mute=old),after=dict(before,mute=new,volume=0))
            self.assertEqual(final_guard('MACBEE_GUARD_FINAL '+json.dumps(row),allow_user_levels=True),row)
            with self.assertRaises(ValueError):final_guard('MACBEE_GUARD_FINAL '+json.dumps(row))
        bad=dict(good,error='speaker setting changed: device_id')
        with self.assertRaisesRegex(ValueError,'device_id'):final_guard('MACBEE_GUARD_FINAL '+json.dumps(bad),allow_user_levels=True)
    def test_guardian_level_policy_without_audio_or_os_changes(self):
        import contextlib,io
        from unittest.mock import Mock
        from audio_guardian import main
        before=dict(active_other_outputs=0,mute=0,volume=.25,input_streams=0,sample_rate=48000,device_id=1)
        after=dict(before,volume=.3125)
        cases=[(False,before,after),(True,before,after),
               (True,dict(before,volume=0,mute=0),dict(after,volume=0,mute=1)),
               (True,dict(before,volume=0,mute=1),dict(after,volume=.5,mute=0))]
        for manual,initial,changed in cases:
            inspection=[Mock(returncode=0,stdout=json.dumps(x),stderr='') for x in (initial,changed,changed)]
            child=Mock(pid=123,returncode=0);child.poll.return_value=None
            clock=[0.0];steps=[0]
            def wait(*args):
                clock[0]+=.5;steps[0]+=1
                return ([],[],[]) if steps[0]==1 else ([Mock()],[],[])
            args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture']
            if manual:args+=['--manual-session','--allow-user-levels','--lease-seconds','3','--max-seconds','0']
            with patch('sys.argv',args),patch('audio_guardian.subprocess.Popen',return_value=child), \
                 patch('audio_guardian.subprocess.run',side_effect=inspection),patch('audio_guardian.signal.signal'), \
                 patch('audio_guardian.time.monotonic',side_effect=lambda:clock[0]),patch('audio_guardian.select.select',side_effect=wait), \
                 patch('audio_guardian.os.read',return_value=b'STOP\n'),patch('audio_guardian.os.killpg'),contextlib.redirect_stdout(io.StringIO()) as out:
                if manual:main()
                else:
                    with self.assertRaises(SystemExit):main()
            row=json.loads(out.getvalue().split('MACBEE_GUARD_FINAL ')[1])
            self.assertEqual(row['error'],None if manual else 'speaker setting changed: volume')
            child.wait.assert_called_once()

    def test_manual_policy_cannot_disable_the_lease(self):
        import contextlib,io
        from audio_guardian import main
        base=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture']
        for options in (['--manual-session'],['--manual-session','--max-seconds','0'],
                        ['--allow-user-levels'],['--max-seconds','0']):
            with patch('sys.argv',base+options),patch('subprocess.Popen',side_effect=AssertionError('No child')), \
                 contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:main()
            self.assertEqual(error.exception.code,2)

    def test_other_app_start_stop_does_not_cancel_either_ordinary_mode(self):
        import contextlib,io,json
        from unittest.mock import Mock
        from audio_guardian import main
        before=dict(active_other_outputs=1,mute=0,volume=.5,input_streams=0,sample_rate=48000,device_id=1)
        for mode in ('strict','selected-app','system'):
            child=Mock(pid=123,returncode=0);child.poll.return_value=None
            inspections=[0];clock=[0.0];steps=[0]
            def inspect(*args,**kwargs):
                inspections[0]+=1;pids='Active output PID: 200\n'
                if inspections[0]==2:pids+='Active output PID: 300\n'
                return Mock(returncode=0,stdout=json.dumps(before),stderr=pids)
            def wait(*args):
                clock[0]+=.6;steps[0]+=1
                return ([Mock()],[],[]) if steps[0]>=3 else ([],[],[])
            args=['guardian','--binary','fixture','--probe','fixture','--port','1','--host-id','fixture']
            if mode!='strict':args+=['--manual-session','--allow-user-levels','--lease-seconds','3','--max-seconds','0']
            if mode=='system':args+=['--system-audio']
            with patch('sys.argv',args),patch('audio_guardian.subprocess.Popen',return_value=child),patch('audio_guardian.subprocess.run',side_effect=inspect), \
                 patch('audio_guardian.signal.signal'),patch('audio_guardian.time.monotonic',side_effect=lambda:clock[0]), \
                 patch('audio_guardian.select.select',side_effect=wait),patch('audio_guardian.os.read',return_value=b'STOP\n'), \
                 patch('audio_guardian.os.killpg'),contextlib.redirect_stdout(io.StringIO()) as output:
                if mode=='strict':
                    with self.assertRaises(SystemExit):main()
                else:main()
            final=json.loads(output.getvalue().split('MACBEE_GUARD_FINAL ')[1])
            self.assertEqual(final['error'],'new unrelated audio output started' if mode=='strict' else None)
            child.wait.assert_called_once()

    def test_no_speaker_readiness_from_null_log(self):
        session=SpeakerTransport()
        with patch.object(session,'logs',return_value=['Player name: file']*2),self.assertRaises(RuntimeError):session.verify_sinks()
    def test_stop_broadcasts_before_waiting_for_either_peer(self):
        from unittest.mock import Mock
        events=[];session=SpeakerTransport();session.local=Mock();session.remote=Mock()
        for name,child in [('local',session.local),('remote',session.remote)]:
            child.poll.return_value=None
            child.stdin.write.side_effect=lambda data,n=name:events.append(n+'_stop')
            child.wait.side_effect=lambda timeout,n=name:events.append(n+'_wait')
        session.stop_clients()
        self.assertEqual(events,['remote_stop','local_stop','remote_wait','local_wait'])
    def test_c2_never_selects_speaker_transport(self):
        source=(ROOT/'tools/run_capture_relay.py').read_text()
        self.assertNotIn('SpeakerTransport',source)
        self.assertNotIn('run_speaker_relay',source)

if __name__=='__main__':unittest.main()
