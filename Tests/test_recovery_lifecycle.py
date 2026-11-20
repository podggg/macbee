"""Bare Apple CLI: no NSApplication, window, app bundle, renderer or HAL input."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('xcrun'), 'Apple compiler required')
class RecoveryLifecycleTests(unittest.TestCase):
    def test_stop_drain_main_timer_and_recovery_ui_without_gui(self):
        with tempfile.TemporaryDirectory(prefix='macbee-recovery-lifecycle-') as temp:
            binary = pathlib.Path(temp) / 'test'
            objects = []
            flags = ['xcrun', 'clang', '-fno-modules', '-O2', '-Wall', '-Wextra',
                     '-Werror', '-mmacosx-version-min=14.2']
            for source in ('application_pcm', 'pcm_ring'):
                obj = pathlib.Path(temp) / (source + '.o')
                subprocess.run(flags + ['-std=c11', '-c',
                    str(ROOT / 'Sources/AudioPipeline' / (source + '.c')), '-o', str(obj)],
                    check=True, capture_output=True, timeout=30)
                objects.append(str(obj))
            build = subprocess.run(flags + ['-fobjc-arc', '-framework', 'Cocoa', '-framework', 'CoreAudio',
                str(ROOT / 'Sources/ControlledCapture/ApplicationSession.m'),
                str(ROOT / 'Sources/ControlledCapture/ApplicationSource.m'),
                str(ROOT / 'Sources/ControlledCapture/ControlledCapture.m'),
                str(ROOT / 'Sources/ControlledCapture/SystemAudioSource.m'),
                str(ROOT / 'Sources/MacbeeApp/SSHBrowse.m'),
                str(ROOT / 'Sources/MacbeeApp/AddMacSheet.m'),
                str(ROOT / 'Sources/SSHLogin/SSHSignIn.m'),
                str(ROOT / 'Sources/SSHLogin/SSHControlMaster.m'),
                str(ROOT / 'Tests/recovery_lifecycle_test.m'), *objects, '-o', str(binary)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=8)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('recovery is presented without GUI', run.stdout)
