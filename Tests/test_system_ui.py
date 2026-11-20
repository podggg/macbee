import pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class SystemUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('xcrun'),'Apple compiler required')
    def test_real_native_controls_without_capture_or_visible_window(self):
        with tempfile.TemporaryDirectory(prefix='macbee-system-ui-') as folder:
            binary=pathlib.Path(folder)/'test'
            command=['xcrun','clang','-fobjc-arc','-fno-modules','-O2','-Wall','-Wextra','-Werror',
                '-mmacosx-version-min=14.2','-framework','Cocoa','-framework','CoreAudio',
                str(ROOT/'Sources/MacbeeApp/SSHBrowse.m'),
                str(ROOT/'Sources/MacbeeApp/AddMacSheet.m'),
                str(ROOT/'Sources/SSHLogin/SSHSignIn.m'),
                str(ROOT/'Sources/SSHLogin/SSHControlMaster.m'),
                str(ROOT/'Tests/system_ui_test.m'),'-o',str(binary)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('window never shown',result.stdout)

    def test_system_ui_removes_one_app_and_fixes_add_mac_contrast(self):
        system_src=(ROOT/'Sources/MacbeeApp/SystemMain.m').read_text()
        for forbidden in ('One App','sourceMode','appPicker','changeSource:','startSource:','ApplicationSource','Refresh'):
            self.assertNotIn(forbidden,system_src)
        self.assertIn('Shares all Mac audio, including notifications and calls. No microphone or recordings.',system_src)
        self.assertIn('NSAppearanceNameAqua',system_src)

        sheet_src=(ROOT/'Sources/MacbeeApp/AddMacSheet.m').read_text()
        self.assertIn('MBAddMacRowCellView',sheet_src)
        self.assertIn('setBackgroundStyle:',sheet_src)
        self.assertIn('NSBackgroundStyleEmphasized',sheet_src)
        self.assertIn('alternateSelectedControlTextColor',sheet_src)
        self.assertIn('This Mac',sheet_src)
        self.assertIn('@"alex"',sheet_src)
        self.assertIn('username@192.168.1.20',sheet_src)
if __name__=='__main__':unittest.main()
