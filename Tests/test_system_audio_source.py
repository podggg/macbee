import pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class SystemAudioSourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('xcrun'),'Apple compiler required')
    def test_both_source_modes_with_unrelated_and_renderer_markers(self):
        with tempfile.TemporaryDirectory(prefix='macbee-system-mix-') as folder:
            binary=pathlib.Path(folder)/'test'
            command=['xcrun','clang','-fobjc-arc','-fno-modules','-O2','-Wall','-Wextra','-Werror',
                '-mmacosx-version-min=14.2','-framework','Cocoa','-framework','CoreAudio']
            files=['Sources/ControlledCapture/SystemAudioSource.m','Sources/ControlledCapture/ControlledCapture.m',
                'Sources/ControlledCapture/ApplicationSource.m','Sources/AudioPipeline/pcm_ring.c','Tests/system_capture_test.m']
            result=subprocess.run(command+[str(ROOT/f) for f in files]+['-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('HAL is fake',result.stdout)
    @unittest.skipUnless(shutil.which('xcrun'),'Apple compiler required')
    def test_owned_renderer_exclusion_without_live_lookup_or_capture(self):
        with tempfile.TemporaryDirectory(prefix='macbee-system-source-') as folder:
            binary=pathlib.Path(folder)/'test'
            command=['xcrun','clang','-fobjc-arc','-fno-modules','-O2','-Wall','-Wextra','-Werror',
                '-mmacosx-version-min=14.2','-framework','Foundation','-framework','CoreAudio',
                str(ROOT/'Sources/ControlledCapture/SystemAudioSource.m'),str(ROOT/'Tests/system_audio_source_test.m'),'-o',str(binary)]
            subprocess.run(command,check=True,capture_output=True,text=True,timeout=30)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('no live metadata, HAL mutation or audio',result.stdout)
    def test_only_explicit_future_build_enables_system_mode(self):
        self.assertNotIn('MBSystemAudioSource',(ROOT/'Sources/MacbeeApp/main.m').read_text())
        capture=(ROOT/'Sources/ControlledCapture/ControlledCapture.m').read_text()
        self.assertIn('objectForInfoDictionaryKey:@"MacbeeSystemAudioEnabled"',capture)
        build=(ROOT/'tools/build_application_candidate.py').read_text()
        self.assertIn('if args.system_candidate:',build)
        self.assertIn("CFBundleIdentifier='io.github.podggg.macbee.systemcandidate'",build)
if __name__=='__main__':unittest.main()
