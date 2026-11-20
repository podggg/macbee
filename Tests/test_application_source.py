import os,plistlib,pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class ApplicationSourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('xcrun'),'Apple framework compiler required')
    def test_metadata_fixtures_without_live_lookup(self):
        self.compile_fixture("application_source_test.m")
    @unittest.skipUnless(shutil.which("xcrun"),"Apple framework compiler required")
    def test_browser_fixtures_without_live_lookup(self):
        self.compile_fixture("browser_source_test.m")
    @unittest.skipUnless(shutil.which("xcrun"),"Apple framework compiler required")
    def test_dia_uses_same_identity_resolver_without_live_lookup(self):
        self.compile_fixture("browser_source_test.m",dia=True)
    @unittest.skipUnless(shutil.which("xcrun"),"Apple framework compiler required")
    def test_unlisted_app_uses_same_resolver_without_brand_table(self):
        self.compile_fixture("browser_source_test.m",generic=True)
    def compile_fixture(self,fixture,dia=False,generic=False):
        with tempfile.TemporaryDirectory(prefix='macbee-source-identity-') as temp:
            binary=pathlib.Path(temp)/'test'
            subprocess.run(['xcrun','clang','-fobjc-arc','-fno-modules','-O2','-Wall','-Wextra','-Werror',
                '-mmacosx-version-min=14.2','-framework','Cocoa','-framework','CoreAudio',
                str(ROOT/'Sources/ControlledCapture/ApplicationSource.m'),str(ROOT/'Tests'/fixture),
                '-o',str(binary)],check=True,timeout=30)
            env=os.environ.copy()
            if dia:env["MACBEE_DIA_FIXTURE"]="1"
            if generic:env["MACBEE_GENERIC_FIXTURE"]="1"
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5,env=env)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('no live metadata or capture',result.stdout)
    @unittest.skipUnless(shutil.which('xcrun'),'Apple framework compiler required')
    def test_browser_tap_descriptor_without_hal_mutation(self):
        with tempfile.TemporaryDirectory(prefix='macbee-tap-descriptor-') as temp:
            root=pathlib.Path(temp);app=root/'Fixture.app';binary=app/'Contents/MacOS/test';binary.parent.mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleExecutable='test',CFBundleIdentifier='macbee.fixture.tap',NSAudioCaptureUsageDescription='Fixture only. HAL tap creation is intercepted and fails.')))
            flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
            obj=root/'ring.o'
            subprocess.run(flags+['-c',str(ROOT/'Sources/AudioPipeline/pcm_ring.c'),'-o',str(obj)],check=True,timeout=30)
            subprocess.run(flags+['-fobjc-arc','-framework','Cocoa','-framework','CoreAudio',
                str(ROOT/'Sources/ControlledCapture/ApplicationSource.m'),str(ROOT/'Sources/ControlledCapture/ControlledCapture.m'),
                str(ROOT/'Tests/browser_tap_test.m'),str(obj),'-o',str(binary)],check=True,timeout=30)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('exact include-only descriptor verified',result.stdout)
    def test_identity_resolver_has_no_browser_allowlist(self):
        source=(ROOT/'Sources/ControlledCapture/ApplicationSource.m').read_text()
        self.assertNotIn('com.google.Chrome',source)
        self.assertNotIn('company.thebrowser',source)
        self.assertNotIn('browserRule',source)
    def test_selected_capture_never_terminates_a_user_app(self):
        source=(ROOT/'Sources/ControlledCapture/ControlledCapture.m').read_text()
        self.assertNotIn('[source terminate]',source)
        self.assertIn('_deferTeardown=YES',source)
        self.assertIn('suspendReadingKeepingTap',source)
        self.assertNotIn('initStereoGlobalTap',source)

if __name__=='__main__':unittest.main()
