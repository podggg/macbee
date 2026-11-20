import hashlib,os,pathlib,sys,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
import relay_runtime

class RelayRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='macbee-runtime-unit-')
        self.root=pathlib.Path(self.temp.name);self.bin=self.root/'bin';self.bin.mkdir(mode=0o700)
        self.hashes={'snapserver':hashlib.sha256(b'fake server').hexdigest(),'snapclient':hashlib.sha256(b'fake client').hexdigest()}
        for name,content in [('snapserver',b'fake server'),('snapclient',b'fake client')]:
            (self.bin/name).write_bytes(content)
        self.patchers=[patch.object(relay_runtime,'ROOT',self.root),patch.object(relay_runtime,'BIN',self.bin),patch.object(relay_runtime,'HASHES',self.hashes)]
        for p in self.patchers:p.start()
    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.temp.cleanup()
    def test_no_execution_or_network_or_external_access(self):
        original_open=pathlib.Path.open
        def restricted_open(path,*args,**kwargs):
            self.assertTrue(path.is_relative_to(self.root),str(path))
            return original_open(path,*args,**kwargs)
        with patch('subprocess.Popen',side_effect=AssertionError('No execution')),patch('socket.socket',side_effect=AssertionError('No sockets')),patch.object(pathlib.Path,'open',restricted_open):
            result=relay_runtime.dry_run()
        self.assertFalse(result['external_volume_access']);self.assertFalse(result['audio_opened'])
        self.assertEqual(list(self.root.iterdir()),[self.bin])
    def test_hash_mismatch_fails(self):
        (self.bin/'snapclient').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):relay_runtime.guard(verify=True)
    def test_symlink_fails(self):
        (self.root/'link').symlink_to(self.bin)
        with self.assertRaises(RuntimeError):relay_runtime.guard()
    def test_nonprivate_root_fails(self):
        self.root.chmod(0o755)
        with self.assertRaises(RuntimeError):relay_runtime.guard()
        self.root.chmod(0o700)
    def test_startup_guard_prunes_old_completed_runs_preserving_bin_and_newest(self):
        other=self.root/'custom-keep';other.mkdir();(other/'note.txt').write_text('keep')
        for i in range(25):
            run_dir=self.root/f'relay-configured-20261118-{i:06d}'
            run_dir.mkdir();(run_dir/'server.log').write_bytes(b'x'*4096)
            ts=1_700_000_000+i
            os.utime(run_dir,(ts,ts))
        relay_runtime.guard(verify=True)
        remaining=sorted(p.name for p in self.root.iterdir() if p.name.startswith('relay-configured-'))
        self.assertEqual(len(remaining),relay_runtime.PRUNE_KEEP_RUNS)
        self.assertEqual(remaining[0],'relay-configured-20261118-000005')
        self.assertEqual(remaining[-1],'relay-configured-20261118-000024')
        self.assertTrue((self.bin/'snapserver').is_file())
        self.assertTrue((other/'note.txt').is_file())
        # Verify byte-budget pruning clears oversized historical runs before 16 MiB trip.
        big=self.root/'relay-configured-20261117-000001';big.mkdir()
        (big/'server.log').write_bytes(b'y'*(9*1024*1024))
        os.utime(big,(1_600_000_000,1_600_000_000))
        total,_=relay_runtime.guard(verify=True)
        self.assertFalse(big.exists())
        self.assertLessEqual(total,relay_runtime.MAX_BYTES)

if __name__=='__main__':unittest.main()
