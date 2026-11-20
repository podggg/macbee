import pathlib,sys,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
import local_config
class LocalConfigTests(unittest.TestCase):
    def test_missing_receiver_fails_closed(self):
        with patch.object(local_config,'SSH_HOST',''):
            with self.assertRaises(RuntimeError):local_config.require_receiver()
    def test_bad_alias_and_remote_root_fail_closed(self):
        for host,root in [('alias;command','/scoped/runtime'),('receiver','relative'),('receiver','/a/../b')]:
            with patch.object(local_config,'SSH_HOST',host),patch.object(local_config,'REMOTE_ROOT',root):
                with self.assertRaises(RuntimeError):local_config.require_receiver()
    def test_explicit_safe_configuration(self):
        with patch.object(local_config,'SSH_HOST','receiver-test'),patch.object(local_config,'REMOTE_ROOT','/scoped/runtime/'):
            local_config.require_receiver()
if __name__=='__main__':unittest.main()
