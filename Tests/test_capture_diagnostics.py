import pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
@unittest.skipUnless(shutil.which('xcrun'),'Apple compiler required')
class CaptureDiagnosticsTests(unittest.TestCase):
    def compile_run(self,name,sources):
        with tempfile.TemporaryDirectory(prefix='macbee-offline-diagnostics-') as folder:
            binary=pathlib.Path(folder)/'test'
            result=subprocess.run(['xcrun','clang','-fobjc-arc','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2','-framework','Cocoa','-framework','CoreAudio',str(ROOT/'Tests'/name)]+[str(ROOT/s) for s in sources]+['-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_callback_counters_without_hal(self):
        self.compile_run('capture_diagnostics_test.m',['Sources/ControlledCapture/ApplicationSource.m','Sources/AudioPipeline/pcm_ring.c'])
    def test_log_exists_before_diagnostics_window(self):
        self.compile_run('ui_log_backlog_test.m',[])
    def test_prepare_never_starts_input_and_cancel_releases(self):
        self.compile_run('capture_preparation_test.m',['Sources/ControlledCapture/ControlledCapture.m','Sources/ControlledCapture/ApplicationSource.m','Sources/AudioPipeline/pcm_ring.c'])
