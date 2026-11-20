import os,pathlib,plistlib,shutil,subprocess,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
@unittest.skipUnless(shutil.which('xcrun'),'Apple compiler required')
class ApplicationPipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='macbee-app-pipe-');root=pathlib.Path(cls.temp.name)
        app=root/'Offline.app';binary=app/'Contents/MacOS';binary.mkdir(parents=True);scripts=root/'tools';scripts.mkdir()
        info=dict(CFBundleIdentifier='macbee.offline.pipe-test',CFBundleName='Offline Session Test',CFBundleExecutable='test',
                  MacbeeApplicationTestEnabled=True,MacbeeDevelopmentRoot=str(root),MacbeePythonExecutable=sys.executable)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        (scripts/'run_application_relay.py').write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT/'tools'))+')\n'+'''
import json,os,select,signal
from application_protocol import ApplicationDecoder
mode=os.getenv('MACBEE_PIPE_CASE','normal');stop=False
def end(sig,frame):
 global stop
 stop=True
signal.signal(signal.SIGTERM,end)
sink='wrong sink' if mode=='wrong_sink' else 'CoreAudioPlayer -> selected-app speakers'
text=json.dumps(dict(event='ready',sinks=[sink]*2))+'\\n'
sys.stdout.write(text[:7]);sys.stdout.flush();sys.stdout.write(text[7:]);sys.stdout.flush()
decoder=ApplicationDecoder()
while not stop:
 if select.select([sys.stdin],[],[],.01)[0]:
  data=os.read(sys.stdin.fileno(),4096)
  if not data:break
  decoder.feed(data)
  if mode in ('missing_ack','invalid_ack','backend_error','failed_unverified','stopping_error'):break
if mode!='wrong_sink':assert decoder.peak==16384
if mode=='stopping_error':
 print(json.dumps(dict(event='stopping',error='simulated device change')),flush=True);sys.exit(0)
if mode=='missing_ack':sys.exit(0)
cleanup={} if mode=='invalid_ack' else (False if mode=='failed_unverified' else True)
event='failed' if mode in ('backend_error','failed_unverified') else 'stopped'
print(json.dumps(dict(event=event,cleanup_verified=cleanup,error='simulated peer loss')),flush=True)
''')
        flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
        obj=root/'pcm.o';cls.binary=binary/'test'
        subprocess.run(flags+['-c',str(ROOT/'Sources/AudioPipeline/application_pcm.c'),'-o',str(obj)],check=True,capture_output=True,timeout=30)
        subprocess.run(flags+['-fobjc-arc','-framework','Cocoa','-framework','CoreAudio',
            str(ROOT/'Sources/ControlledCapture/ApplicationSession.m'),str(ROOT/'Sources/ControlledCapture/ApplicationSource.m'),
            str(ROOT/'Tests/application_session_pipe_test.m'),str(obj),'-o',str(cls.binary)],check=True,capture_output=True,timeout=30)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check_case(self,mode):
        env=os.environ.copy();env['MACBEE_PIPE_CASE']=mode
        r=subprocess.run([str(self.binary)],env=env,text=True,capture_output=True,timeout=6)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertIn('audio_opened=false; network_opened=false',r.stdout)
    def test_full_range_native_pipe_stop(self):self.check_case('normal')
    def test_wrong_sink_never_captures(self):self.check_case('wrong_sink')
    def test_missing_ack_not_reported_clean(self):self.check_case('missing_ack')
    def test_malformed_ack_not_reported_clean(self):self.check_case('invalid_ack')
    def test_backend_failure_reason_is_retained(self):self.check_case('backend_error')
    def test_failed_unverified_final_survives_termination_fallback(self):self.check_case('failed_unverified')
    def test_stopping_failure_survives_missing_final(self):self.check_case('stopping_error')
if __name__=='__main__':unittest.main()
