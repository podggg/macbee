"""Native A3/A4/A5/A6 ControlMaster, Keychain opt-in, and AddMacSheet headless acceptance tests."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'Requires macOS Apple SDK')
class SSHControlMasterAndSheetTests(unittest.TestCase):
    def test_control_master_add_mac_sheet_and_keychain_opt_in(self):
        with tempfile.TemporaryDirectory(prefix='macbee-ctl-test-') as scratch:
            root = Path(scratch).resolve()
            fake_ssh = root / 'fake-ssh'
            fake_ssh_add = root / 'fake-ssh-add'
            helper = root / 'macbee-askpass'
            runner = root / 'ssh_control_master_test'
            home = root / 'home'
            home.mkdir(mode=0o700)

            c_flags = ['xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=14.2']
            objc_flags = ['xcrun', 'clang', '-fobjc-arc', '-fno-modules', '-O2', '-Wall', '-Wextra', '-Werror',
                          '-mmacosx-version-min=14.2', '-framework', 'Cocoa']

            for cmd in [
                c_flags + [str(ROOT / 'Sources/SSHLogin/askpass.c'), '-o', str(helper)],
                c_flags + [str(ROOT / 'Tests/ssh_control_master_fake_ssh.c'), '-o', str(fake_ssh)],
            ]:
                b = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                self.assertEqual(b.returncode, 0, b.stderr)
            shutil.copy2(fake_ssh, fake_ssh_add)
            fake_ssh_add.chmod(0o755)

            build = subprocess.run(
                objc_flags + [
                    str(ROOT / 'Sources/SSHLogin/SSHSignIn.m'),
                    str(ROOT / 'Sources/SSHLogin/SSHControlMaster.m'),
                    str(ROOT / 'Sources/MacbeeApp/SSHBrowse.m'),
                    str(ROOT / 'Sources/MacbeeApp/AddMacSheet.m'),
                    str(ROOT / 'Tests/ssh_control_master_test.m'),
                    '-o', str(runner),
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(build.returncode, 0, build.stderr)

            run = subprocess.run(
                [str(runner), str(fake_ssh), str(fake_ssh_add), str(helper), str(home)],
                capture_output=True, text=True, timeout=25,
            )
            self.assertEqual(run.returncode, 0, run.stdout + '\n' + run.stderr)
            self.assertIn('zero lingering ssh', run.stdout)
            self.assertNotIn('synthetic-ctl-secret-', run.stdout + run.stderr)

            pid_file = home / 'pids.log'
            if pid_file.exists():
                for line in pid_file.read_text().splitlines():
                    pid = int(line.strip())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)

    def test_application_session_control_paths_and_auth_required_fast_finish(self):
        with tempfile.TemporaryDirectory(prefix='macbee-session-auth-') as scratch:
            root = Path(scratch).resolve()
            (root / 'tools').mkdir()
            (root / 'tools/run_system_relay.py').write_text(
                'import json, os, sys\n'
                'paths = json.loads(os.environ.get("MACBEE_SSH_CONTROL_PATHS", "{}"))\n'
                'assert paths == {"r-0000000000000001": "/tmp/macbee-ctl.TEST1234/%C"}, paths\n'
                'ev = {"reason_code": "auth_required", "auth_required_receiver": "r-0000000000000001", "error": "Authentication required"}\n'
                'print(json.dumps({"event": "stopping", **ev}), flush=True)\n'
                'print(json.dumps({"event": "failed", "cleanup_verified": False, **ev}), flush=True)\n'
            )
            plist = root / 'Info.plist'
            plist.write_bytes(plistlib.dumps(dict(
                CFBundleExecutable='auth_test',
                CFBundleIdentifier='macbee.fixture.auth',
                MacbeeDevelopmentRoot=str(root),
                MacbeePythonExecutable='/usr/bin/python3',
                MacbeeApplicationTestEnabled=False,
                MacbeeBoundedSystemPacing=True,
                MacbeeSystemAudioEnabled=True,
                MacbeeManualSessionEnabled=True,
                MacbeeConfiguredOutputsEnabled=True,
            )))
            flags = ['xcrun', 'clang', '-fno-modules', '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=14.2']
            pcm_obj = root / 'pcm.o'
            b1 = subprocess.run(flags + ['-std=c11', '-c', str(ROOT / 'Sources/AudioPipeline/application_pcm.c'), '-o', str(pcm_obj)],
                                capture_output=True, text=True, timeout=30)
            self.assertEqual(b1.returncode, 0, b1.stderr)
            binary = root / 'auth_test'
            b2 = subprocess.run(
                flags + [
                    '-fobjc-arc', '-framework', 'Cocoa', '-framework', 'CoreAudio',
                    str(ROOT / 'Sources/ControlledCapture/ApplicationSession.m'),
                    str(ROOT / 'Sources/ControlledCapture/ApplicationSource.m'),
                    str(ROOT / 'Tests/application_session_auth_test.m'),
                    str(pcm_obj),
                    '-Wl,-sectcreate,__TEXT,__info_plist,' + str(plist),
                    '-o', str(binary),
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(b2.returncode, 0, b2.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=6)
            self.assertEqual(run.returncode, 0, run.stdout + '\n' + run.stderr)
            self.assertIn('instant finish', run.stdout)


if __name__ == '__main__':
    unittest.main()
