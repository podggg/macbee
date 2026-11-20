import os,pathlib,plistlib,shutil,subprocess,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]

@unittest.skipUnless(shutil.which('xcrun'),'Apple Foundation/toolchain required')
class ControlledRelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='macbee-relay-unit-');cls.root=pathlib.Path(cls.temp.name)
        app=cls.root/'Mock Relay.app';binary=app/'Contents/MacOS';binary.mkdir(parents=True)
        scripts=cls.root/'tools';scripts.mkdir()
        info=dict(CFBundleIdentifier='macbee.test.offline-relay',CFBundleName='Offline Relay Unit Test',
                  CFBundleExecutable='relay-test',CFBundlePackageType='APPL',
                  MacbeeDevelopmentRoot=str(cls.root),MacbeePythonExecutable=sys.executable,
                  NSAudioCaptureUsageDescription='Test fixture only. No capture implementation is linked.')
        cls.info_path=app/'Contents/Info.plist';cls.info_path.write_bytes(plistlib.dumps(info))
        helper=binary/'macbee-controlled-source'
        helper.write_text('#!'+sys.executable+'''\nimport json,sys,time,pathlib
pathlib.Path(__file__).parents[3].joinpath('source-ready').write_text('owned zero-only mock helper ready')
print(json.dumps(dict(event='source_ready')),flush=True)
if sys.stdin.readline()=='ARM\\n':time.sleep(1.1)
''');helper.chmod(0o700)
        (scripts/'run_capture_relay.py').write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT/'tools'))+')\n'+'''
import json,os,time
from capture_protocol import PacketDecoder
mode=os.getenv('MACBEE_MOCK_CASE','normal').removeprefix('speaker_')
speakers=__file__.endswith('run_speaker_relay.py')
if speakers:
 import pathlib
 assert pathlib.Path(__file__).parents[1].joinpath('source-ready').is_file()
sink='CoreAudioPlayer -> guarded built-in speakers' if speakers else 'FilePlayer -> /dev/null'
if mode=='wrong_sink':sink='unexpected output'
ready=json.dumps(dict(event='ready',sinks=[sink]*2))+'\\n'
# Deliberately fragment the record: native reader must assemble complete lines.
sys.stdout.write(ready[:9]);sys.stdout.flush();time.sleep(.01)
sys.stdout.write(ready[9:]);sys.stdout.flush()
if mode=='backend_exit':sys.exit(1)
if mode=='backpressure':time.sleep(3);sys.exit(1)
decoder=PacketDecoder()
while not decoder.ended:
 data=os.read(sys.stdin.fileno(),4096)
 if not data:sys.exit(2)
 decoder.feed(data)
decoder.finish()
print(json.dumps(dict(event='complete',cleanup_verified=True)),flush=True)
''')
        shutil.copyfile(scripts/'run_capture_relay.py',scripts/'run_speaker_relay.py')
        cls.binary=binary/'relay-test'
        flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
        obj=cls.root/'encoder.o'
        subprocess.run(flags+['-std=c11','-c',str(ROOT/'Sources/AudioPipeline/controlled_pcm.c'),'-o',str(obj)],check=True,capture_output=True,timeout=30)
        subprocess.run(flags+['-fobjc-arc','-framework','Foundation',str(ROOT/'Sources/ControlledCapture/ControlledRelay.m'),
            str(ROOT/'Tests/controlled_relay_mock.m'),str(obj),'-o',str(cls.binary)],check=True,capture_output=True,timeout=30)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_mode(self,mode):
        info=plistlib.loads(self.info_path.read_bytes());info['MacbeeControlledSpeakerTest']=mode.startswith('speaker_')
        self.info_path.write_bytes(plistlib.dumps(info))
        marker=self.root/'source-ready'
        if marker.exists():marker.unlink()
        env=os.environ.copy();env['MACBEE_MOCK_CASE']=mode
        if mode=='bad_amplitude':env['MACBEE_MOCK_BAD_AMPLITUDE']='1'
        result=subprocess.run([str(self.binary)],env=env,capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('audio_opened=false; network_opened=false',result.stdout)
    def test_happy_and_repeated_start(self):self.run_mode('normal')
    def test_cancel_idempotent(self):self.run_mode('cancel')
    def test_restart_with_stale_callback_guard(self):self.run_mode('restart')
    def test_backend_exit(self):self.run_mode('backend_exit')
    def test_backpressure_stops(self):self.run_mode('backpressure')
    def test_unexpected_amplitude_stops(self):self.run_mode('bad_amplitude')
    def test_speaker_helper_precedes_backend(self):self.run_mode('speaker_normal')
    def test_speaker_cancel_before_ready(self):self.run_mode('speaker_cancel')
    def test_speaker_backend_exit(self):self.run_mode('speaker_backend_exit')
    def test_speaker_wrong_sink_does_not_arm(self):self.run_mode('speaker_wrong_sink')

if __name__=='__main__':unittest.main()
