import pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class ApplicationPCMTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('clang'),'existing compiler required')
    def test_offline_normal_amplitude_converter(self):
        with tempfile.TemporaryDirectory(prefix='macbee-application-pcm-') as temp:
            binary=pathlib.Path(temp)/'test'
            subprocess.run(['clang','-std=c11','-fno-modules','-O2','-Wall','-Wextra','-Werror',
                '-I'+str(ROOT/'Sources/AudioPipeline'),str(ROOT/'Sources/AudioPipeline/application_pcm.c'),
                str(ROOT/'Tests/application_pcm_test.c'),'-lm','-o',str(binary)],check=True,capture_output=True,timeout=30)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=5)
            self.assertIn('no audio API or IO',result.stdout)
    def test_current_builds_do_not_link_ordinary_audio(self):
        for file in ('build_app.py','build_capture_bench.py','build_relay_candidate.py'):
            self.assertNotIn('application_pcm', (ROOT/'tools'/file).read_text())

if __name__=='__main__':unittest.main()
