import pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class ApplicationSessionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('xcrun'),'Apple compiler required')
    def test_deterministic_lifecycle_without_effects(self):
        with tempfile.TemporaryDirectory(prefix='macbee-app-lifecycle-') as temp:
            binary=pathlib.Path(temp)/'test';obj=pathlib.Path(temp)/'pcm.o'
            flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
            subprocess.run(flags+['-std=c11','-c',str(ROOT/'Sources/AudioPipeline/application_pcm.c'),'-o',str(obj)],check=True,capture_output=True,timeout=30)
            subprocess.run(flags+['-fobjc-arc','-framework','Cocoa','-framework','CoreAudio',
                str(ROOT/'Sources/ControlledCapture/ApplicationSession.m'),str(ROOT/'Sources/ControlledCapture/ApplicationSource.m'),
                str(ROOT/'Tests/application_session_test.m'),str(obj),'-o',str(binary)],check=True,capture_output=True,timeout=30)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('effects=none',result.stdout)

if __name__=='__main__':unittest.main()
