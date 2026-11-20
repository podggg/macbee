import json,pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class ControlledSourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('xcrun'),'Apple toolchain required')
    def test_generator_without_opening_audio(self):
        with tempfile.TemporaryDirectory(prefix='macbee-source-') as directory:
            binary=pathlib.Path(directory)/'controlled-source'
            subprocess.run(['xcrun','clang','-std=c11','-D_DARWIN_C_SOURCE','-fno-modules','-O2','-Wall','-Wextra','-Werror',
                '-framework','AudioToolbox','-framework','CoreAudio',str(ROOT/'Sources/ControlledSource/main.c'),'-o',str(binary)],check=True,capture_output=True,timeout=30)
            result=subprocess.run([str(binary),'--self-test'],check=True,capture_output=True,text=True,timeout=5)
            self.assertEqual(json.loads(result.stdout),dict(pulses=6,peak=163,nonzero_frames=27132,audio_opened=False))
    def test_adapter_has_no_global_or_microphone_mode(self):
        code=(ROOT/'Sources/ControlledCapture/ControlledCapture.m').read_text()
        self.assertIn('initStereoMixdownOfProcesses:processes]',code)
        self.assertIn('startProcesses:@[@(process)]',code)
        self.assertIn('prepareProcesses:source.audioProcesses',code)
        self.assertIn('description.exclusive=NO',code)
        self.assertIn('description.processRestoreEnabled=NO',code)
        self.assertNotIn('initStereoGlobalTap',code)
        self.assertNotIn('AVCapture',code)
        self.assertIn('helper.isRunning',code)
    def test_capture_bench_does_not_start_on_launch(self):
        code=(ROOT/'Sources/CaptureBench/main.m').read_text()
        launch=code.split('- (void)applicationDidFinishLaunching:')[1].split('- (void)append:')[0]
        self.assertNotIn('startForOwnedHelper:',launch)
        self.assertNotIn('[helper launch',launch)
        self.assertIn('containsObject:@"--allow-controlled-test"',launch)
