import pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class RingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('clang') or shutil.which('cc'),'existing C compiler required')
    def test_native_spsc_synthetic_only(self):
        with tempfile.TemporaryDirectory(prefix='macbee-ring-') as temporary:
            binary=pathlib.Path(temporary)/'ring-test'
            subprocess.run([shutil.which('clang') or shutil.which('cc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'Sources/AudioPipeline'),str(ROOT/'Sources/AudioPipeline/pcm_ring.c'),
                str(ROOT/'Tests/pcm_ring_test.c'),'-o',str(binary)],check=True,capture_output=True,timeout=30)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
            self.assertIn('100000 ordered concurrent frames',result.stdout)
