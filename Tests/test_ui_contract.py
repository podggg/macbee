import pathlib,re,unittest
ROOT=pathlib.Path(__file__).parents[1]
class UIContractTests(unittest.TestCase):
    def test_owned_visible_strings_are_english(self):
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        literals=re.findall(r'@"(?:[^"\\]|\\.)*"',source)
        self.assertTrue(literals)
        self.assertFalse(any(re.search(r'[\u4e00-\u9fff]',s) for s in literals))
    def test_preview_routes_only_to_mock(self):
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        self.assertIn('self.preview?@"tools/mock_ui_test.py":@"tools/run_coreaudio_probe.py"',source)
        self.assertIn('start.enabled=NO',source)
        self.assertIn('add.enabled=NO',source)
    def test_capture_not_linked_or_enabled_in_ui(self):
        build=(ROOT/'tools/build_app.py').read_text()
        self.assertNotIn('NSAudioCaptureUsageDescription',build)
        self.assertNotIn('ControlledCapture.m',build)
    def test_relay_candidate_is_separate_and_not_audible(self):
        build=(ROOT/'tools/build_relay_candidate.py').read_text()
        self.assertIn("'Macbee Relay Test'",build)
        self.assertIn('if APP.exists():raise RuntimeError',build)
        self.assertNotIn('NSMicrophoneUsageDescription',build)
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        self.assertIn('@"--allow-controlled-speakers":@"--allow-controlled-relay"',source)
        self.assertIn("C2 cannot select C3's speaker path.",source)
        self.assertIn('self.toneButton.hidden=YES;self.toneButton.enabled=NO',source)

class SelectedAppUIContractTests(unittest.TestCase):
    def test_preview_cannot_link_the_real_tap(self):
        build=(ROOT/'tools/build_application_preview.py').read_text()
        self.assertNotIn('NSAudioCaptureUsageDescription',build)
        self.assertNotIn('ControlledCapture.m',build)
        self.assertIn('MacbeeApplicationTestEnabled=False',build)
    def test_real_ui_ignores_legacy_mock_toggle_and_requires_gate(self):
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        self.assertIn('self.preview=YES;\n#else\n    self.preview=NO;',source)
        self.assertIn('containsObject:@"--allow-selected-app"',source)
        self.assertIn('if(![self selectedAppStartAllowed])return;',source)
        self.assertIn('sourcePicker.enabled=!running',source)
        self.assertIn('if(self.applicationSession.running){self.closingWindow=window;',source)

    def test_user_candidate_is_explicit_and_keeps_test_identity(self):
        build=(ROOT/'tools/build_application_candidate.py').read_text()
        self.assertIn("mode.add_argument('--user-candidate'",build)
        self.assertIn(".local-app/Macbee Candidate.app",build)
        self.assertIn('MacbeeUserLaunchableCandidate=True,MacbeeManualSessionEnabled=True',build)
        self.assertIn("MacbeeReleaseVersion='1.0.1',CFBundleShortVersionString='1.0.1',CFBundleVersion='2'",build)
        self.assertIn('if APP.exists():raise RuntimeError',build)
        self.assertIn('MacbeeMaximumSharingSeconds=10',build)
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        self.assertIn('return self.preview || candidate ||',source)
        self.assertIn('if(self.userCandidate)unsetenv("MACBEE_OWNED_MARKER_TIMING")',source)
        self.assertIn('All audio from this app. No scheduled session limit.',source)
        self.assertNotIn('30-MINUTE SESSIONS',source)
        self.assertNotIn('30-minute session limit.',source)

    def test_refresh_does_not_capture_or_change_a_running_selection(self):
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        refresh=source.split('- (void)refreshSources:',1)[1].split('- (void)refreshApplicationControls',1)[0]
        self.assertIn('if(self.applicationSession.running)return;',refresh)
        self.assertIn('selectItemAtIndex:0',refresh)
        self.assertNotIn('startSource:',refresh)
        self.assertIn('self.refreshSourcesButton.enabled=!running',source)

    def test_registration_delay_preview_is_inert_and_bounded(self):
        source=(ROOT/'Sources/MacbeeApp/ApplicationPreview.m').read_text()
        self.assertIn('prepareSelectedCapture:',source)
        self.assertIn('MAX(0,MIN(120,delay.doubleValue))',source)
        self.assertIn('NSAssert(!NSThread.isMainThread',source)
        build=(ROOT/'tools/build_application_preview.py').read_text()
        self.assertNotIn('ControlledCapture.m',build)
        self.assertNotIn('NSAudioCaptureUsageDescription',build)

    def test_closing_diagnostics_does_not_stop_selected_app(self):
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        handler=source.split('- (BOOL)windowShouldClose:',1)[1]
        self.assertLess(handler.index('if(window==self.diagnostics)return YES'),handler.index('if(self.applicationSession.running)'))

class SystemCandidateUIContractTests(unittest.TestCase):
    def test_system_main_removes_one_app_and_keeps_entire_mac_privacy_note(self):
        source=(ROOT/'Sources/MacbeeApp/SystemMain.m').read_text()
        for forbidden in ('One App','sourceMode','appPicker','changeSource:','startSource:','MBApplicationSource','@"SOURCE"','Refresh'):
            self.assertNotIn(forbidden,source)
        self.assertIn('startSystemAudioWithOutputs:',source)
        self.assertIn('Shares all Mac audio, including notifications and calls. No microphone or recordings.',source)
        self.assertIn('NSAppearanceNameAqua',source)
        self.assertIn('NSMakeRect(0,0,440,520)',source)
        self.assertIn('NSMakeSize(380,460)',source)

    def test_add_mac_sheet_adapts_selection_contrast_and_marks_this_mac(self):
        source=(ROOT/'Sources/MacbeeApp/AddMacSheet.m').read_text()
        for required in ('MBAddMacRowCellView','setBackgroundStyle:','NSBackgroundStyleEmphasized',
                         'alternateSelectedControlTextColor','NSColor.windowBackgroundColor',
                         'NSColor.labelColor','NSColor.secondaryLabelColor','@"This Mac"','@"alex"'):
            self.assertIn(required,source)
        for forbidden in ('MBAddInk','MBAddMuted'):
            self.assertNotIn(forbidden,source)

    def test_system_candidate_version_and_source_gates(self):
        build=(ROOT/'tools/build_application_candidate.py').read_text()
        pkg=(ROOT/'tools/package_release.py').read_text()
        guide=(ROOT/'docs/NEXT-USER-GUIDE.md').read_text()
        self.assertIn("MacbeeApplicationTestEnabled=False",build)
        self.assertIn("MacbeeSystemAudioEnabled=True",build)
        self.assertIn("MacbeeBoundedSystemPacing=True",build)
        self.assertIn("NSBonjourServices=['_ssh._tcp']",build)
        self.assertIn("MacbeeReleaseVersion='1.2.6-dev',CFBundleShortVersionString='1.2.6',CFBundleVersion='10'",build)
        self.assertIn("version='1.2.6-dev' if system_candidate else VERSION",pkg)
        self.assertIn("CFBundleShortVersionString')!=('1.2.6'",pkg)
        self.assertIn("CFBundleVersion')!='10'",pkg)
        self.assertIn("MacbeeApplicationTestEnabled') is not False",pkg)
        self.assertIn('1.2.6-dev',guide)
        self.assertNotIn('One App',guide)
