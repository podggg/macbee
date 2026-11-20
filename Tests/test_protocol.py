"""No sound is produced by these tests, including native subprocess tests."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('probe', ROOT/'tools/dual_probe.py')
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)

class PeerProtocolTests(unittest.TestCase):
    def test_ready_and_clean_stop(self):
        code = '''import json,sys
print(json.dumps({'event':'ready'}),flush=True)
assert sys.stdin.readline()=='STOP\\n'
'''
        peer = probe.Peer([sys.executable,'-u','-c',code])
        peer.close()
        self.assertEqual(peer.p.returncode,0)
        self.assertTrue(peer.p.stdin.closed)
        self.assertTrue(peer.p.stdout.closed)

    def test_split_response_is_reassembled(self):
        code = '''import sys,time
sys.stdout.write('{"event":');sys.stdout.flush();time.sleep(.02)
sys.stdout.write('"ready"}\\n');sys.stdout.flush()
sys.stdin.readline()
'''
        peer=probe.Peer([sys.executable,'-u','-c',code]);peer.close()
        self.assertEqual(peer.ready['event'],'ready')

    def test_unexpected_ready_is_rejected(self):
        code = '''import sys
print('{"event":"wrong"}',flush=True)
sys.stdin.readline()
'''
        with self.assertRaises(RuntimeError): probe.Peer([sys.executable,'-u','-c',code])

    def test_disconnect_is_rejected(self):
        with self.assertRaises(RuntimeError): probe.Peer([sys.executable,'-c','pass'])

    def test_oversize_response_is_rejected(self):
        code = "import sys;sys.stdout.write('x'*70000);sys.stdout.flush();sys.stdin.readline()"
        with self.assertRaises(RuntimeError): probe.Peer([sys.executable,'-u','-c',code])

    def test_repeat_open_and_close(self):
        for _ in range(3):
            code = "import sys;print('{\"event\":\"ready\"}',flush=True);sys.stdin.readline()"
            peer=probe.Peer([sys.executable,'-u','-c',code]);peer.close()
            self.assertEqual(peer.p.returncode,0)

@unittest.skipUnless(os.environ.get('MACBEE_PROBE'), 'set MACBEE_PROBE for native silent checks')
class NativeSilentProtocolTests(unittest.TestCase):
    def run_command(self,payload):
        return subprocess.run([os.environ['MACBEE_PROBE'],'--serve'],input=payload,
                              capture_output=True,text=True,timeout=5)
    def test_invalid_targets_never_arm(self):
        for target in ('0','-1','99999999999999999999999999','not-a-number'):
            r=self.run_command('PLAY '+target+'\n')
            self.assertEqual(r.returncode,1,r.stdout)
            self.assertNotIn('"armed"',r.stdout)
            self.assertNotIn('"result"',r.stdout)
    def test_stop_and_eof(self):
        for payload in ('','STOP\n','CLOCK\nSTOP\n'):
            r=self.run_command(payload)
            self.assertEqual(r.returncode,0,r.stdout)
            self.assertNotIn('"armed"',r.stdout)
    def test_partial_line_can_be_terminated(self):
        p=subprocess.Popen([os.environ['MACBEE_PROBE'],'--serve'],stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        try:
            self.assertEqual(json.loads(p.stdout.readline())['event'],'ready')
            p.stdin.write(b'CLO');p.stdin.flush();time.sleep(.05)
            p.terminate();p.wait(timeout=2)
            self.assertEqual(p.returncode,0)
        finally:
            if p.poll() is None:p.kill();p.wait(timeout=2)
            p.stdin.close();p.stdout.close()

if __name__=='__main__':unittest.main()
