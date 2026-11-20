import os,pathlib,sys,threading,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'tools'))
from parent_watch import ParentWatch
class ParentWatchTests(unittest.TestCase):
    def test_normal_shutdown_does_not_signal(self):
        read,write=os.pipe();event=threading.Event()
        try:
            watch=ParentWatch(read,event.set).start();watch.close();watch.close()
            self.assertFalse(event.is_set());self.assertFalse(watch.thread.is_alive())
        finally:os.close(read);os.close(write)
    def test_parent_eof_signals_once(self):
        read,write=os.pipe();event=threading.Event();calls=[]
        def callback():calls.append(1);event.set()
        watch=ParentWatch(read,callback).start()
        try:
            os.close(write);self.assertTrue(event.wait(2));watch.close()
            self.assertEqual(calls,[1]);self.assertFalse(watch.thread.is_alive())
        finally:os.close(read)
