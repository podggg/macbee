import hashlib,json,pathlib,shutil,subprocess,sys,tempfile,unittest

ROOT=pathlib.Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from speaker_transport import PROBE_HASH

class ToneProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp=tempfile.TemporaryDirectory(prefix='macbee-tone-probe-test-')
        cls.binary=pathlib.Path(cls._tmp.name)/'tone_probe_test'
        compiler=shutil.which('clang') or shutil.which('cc')
        if compiler:
            subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror','-fno-modules',
                '-mmacosx-version-min=14.2','-framework','CoreFoundation',
                str(ROOT/'Tests/tone_probe_test.c'),'-o',str(cls.binary)],check=True,capture_output=True,timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_case(self,mode):
        if not self.binary.exists():self.skipTest('existing C compiler required')
        return subprocess.run([str(self.binary),mode],check=True,capture_output=True,text=True,timeout=10)

    def test_device_uid_remains_stable_across_device_id_shift(self):
        r=self.run_case('shift')
        rows=[json.loads(line) for line in r.stdout.strip().splitlines()]
        self.assertEqual(len(rows),2)
        self.assertEqual([row['device_id'] for row in rows],[84,91])
        self.assertEqual([row['device_uid'] for row in rows],['BuiltInSpeakerDevice','BuiltInSpeakerDevice'])

    def test_device_uid_json_escaping_round_trips_cleanly(self):
        r=self.run_case('json_escape')
        row=json.loads(r.stdout.strip())
        self.assertEqual(row['device_uid'],'Fake"Speaker\\Line\nCtrl\x01End')

    def test_active_other_output_skips_vanished_audio_objects(self):
        r=self.run_case('vanished_procs')
        row=json.loads(r.stdout.strip())
        self.assertEqual(row['active_other_outputs'],1)
        self.assertEqual(r.stderr.strip().splitlines(),['Active output PID: 5001'])

        r=self.run_case('vanished_only')
        row=json.loads(r.stdout.strip())
        self.assertEqual(row['active_other_outputs'],0)
        self.assertEqual(r.stderr,'')

        self.run_case('coexist_vanished')

    def test_active_other_output_retries_growth_and_teardown_without_duplicate_stderr(self):
        r=self.run_case('retry_growth')
        row=json.loads(r.stdout.strip())
        self.assertEqual(row['active_other_outputs'],1)
        self.assertEqual(r.stderr.strip().splitlines(),['Active output PID: 5001'])

        r=self.run_case('all_retries_fail')
        row=json.loads(r.stdout.strip())
        self.assertEqual(row['active_other_outputs'],-1)

    def test_non_builtin_and_missing_uid_fail_closed(self):
        row=json.loads(self.run_case('not_builtin').stdout.strip())
        self.assertEqual(row,dict(event='error',reason='default_not_builtin',status=0))
        for mode in ('uid_error','uid_empty'):
            row=json.loads(self.run_case(mode).stdout.strip())
            self.assertEqual(row['event'],'error')
            self.assertEqual(row['reason'],'device_uid')

    @unittest.skipUnless(shutil.which('xcrun'),'Xcode command line tools required')
    def test_build_probe_binary_contains_no_private_paths(self):
        with tempfile.TemporaryDirectory(prefix='macbee-probe-build-') as tmp:
            result=subprocess.run(['python3','-B',str(ROOT/'tools/build_probe.py'),tmp],
                capture_output=True,text=True,check=True,timeout=30)
            built=pathlib.Path(result.stdout.strip())
            self.assertTrue(built.is_file())
            data=built.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),PROBE_HASH)
            self.assertLessEqual(len(data),128*1024)
            for needle in (b'/Users/',b'/Volumes/',b'/private/tmp/'):
                self.assertNotIn(needle,data)
            # Confirm package_release uses the same PROBE_HASH cleanly on import.
            import package_release
            self.assertEqual(package_release.PROBE_HASH,PROBE_HASH)

if __name__=='__main__':unittest.main()
