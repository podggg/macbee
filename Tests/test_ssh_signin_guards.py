"""Defensive checks for the exact compiled interactive channel. Synthetic data only."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


def imports(object_file):
    return set(subprocess.check_output(['/usr/bin/nm','-u','-j',str(object_file)],text=True).splitlines())


# The helper can communicate via the private socket and write a framed response
# to its checked stdout pipe. It does not need a single file opening or logging API.
HELPER_IMPORTS=set('___error ___stack_chk_fail ___stack_chk_guard ___strlcpy_chk _bzero _clock_gettime '
                  '_close _connect _fcntl _fstat _getenv _getpeereid _getsockopt _getuid _lstat _memchr '
                  '_poll _recv _send _setrlimit _setsockopt _socket _strcmp _strlen _strtol _write'.split())


def forbidden_app_symbols(symbols):
    bad=re.compile(r'(?:^_+(?:NSLog|os_log|open(?:at)?|creat|fopen|freopen|fwrite|write|pwrite|printf|fprintf|syslog)(?:$|[_$])|'
                   r'\$(?:writeTo(?:File|URL)|appendToFile|fileHandleForWritingAtPath|createFileAtPath|'
                   r'setPersistentDomain|standardUserDefaults|writeData:))')
    return {s for s in symbols if bad.search(s)}


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'requires Apple SDK for private fixture')
class SignInGuards(unittest.TestCase):
    def test_exact_helper_entry_rejects_bad_owner_uid_mode_pid_and_output(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'guards'
            subprocess.run(['/usr/bin/clang','-std=c11','-Wall','-Wextra','-Werror',
                str(ROOT/'Tests/ssh_askpass_guard_test.c'),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],timeout=4,check=True)

    def test_compiled_askpass_and_app_do_not_import_file_or_log_sinks(self):
        with tempfile.TemporaryDirectory() as folder:
            d=Path(folder);flags=['/usr/bin/clang','-O1','-Wall','-Wextra','-Werror','-c']
            helper=d/'helper.o';app=d/'app.o';ctl=d/'ctl.o'
            subprocess.run(flags+[str(ROOT/'Sources/SSHLogin/askpass.c'),'-o',str(helper)],check=True)
            subprocess.run(flags+['-fobjc-arc',str(ROOT/'Sources/SSHLogin/SSHSignIn.m'),'-o',str(app)],check=True)
            subprocess.run(flags+['-fobjc-arc',str(ROOT/'Sources/SSHLogin/SSHControlMaster.m'),'-o',str(ctl)],check=True)
            self.assertEqual(imports(helper)-HELPER_IMPORTS,set())
            self.assertEqual(forbidden_app_symbols(imports(app)),set())
            self.assertEqual(forbidden_app_symbols(imports(ctl)),set())
            # Compile but never execute harmless controls, independently of path.
            # Thus writing under /tmp instead of HOME cannot evade this audit.
            control=d/'forbidden.m'
            control.write_text('#import <Foundation/Foundation.h>\n#include <stdio.h>\n'
                               'void fixture(void){FILE *f=fopen("/tmp/unused-fixture","w");'
                               'if(f)fclose(f);NSLog(@"Fixture policy control");}\n')
            obj=d/'control.o';subprocess.run(flags+[str(control),'-o',str(obj)],check=True)
            leaked=imports(obj)
            self.assertIn('_fopen',leaked-HELPER_IMPORTS)
            self.assertIn('_NSLog',forbidden_app_symbols(leaked))
            self.assertFalse(any('fopen' in s for s in imports(helper)))

    def test_real_askpass_fake_ssh_pin_cancel_and_cleanup(self):
        with tempfile.TemporaryDirectory() as scratch:
            run=subprocess.run(['/bin/sh',str(ROOT/'Tests/run_ssh_signin_fixture.sh'),scratch],
                               capture_output=True,text=True,timeout=15)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertNotIn('only-this-synthetic-fixture-',run.stderr+run.stdout)
