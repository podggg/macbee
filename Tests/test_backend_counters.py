import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'tools'))
from run_backend_probe import stats

class CounterTests(unittest.TestCase):
    def test_pattern_distinct_from_zero_padding(self):
        result=stats('MACBEE_STATS {"requests":250,"good_chunks":240,"pattern_frames":288093,"zero_frames":10000,"other_frames":0}')
        self.assertEqual(result['pattern_frames'],288093)
    def test_zero_only_cannot_pass(self):
        with self.assertRaises(RuntimeError):
            stats('MACBEE_STATS {"requests":250,"good_chunks":240,"pattern_frames":0,"zero_frames":600000,"other_frames":0}')
    def test_other_nonzero_cannot_pass(self):
        with self.assertRaises(RuntimeError):
            stats('MACBEE_STATS {"requests":250,"good_chunks":240,"pattern_frames":288000,"zero_frames":0,"other_frames":1}')
    def test_missing_counters_cannot_pass(self):
        with self.assertRaises(RuntimeError):stats('client connected successfully')

if __name__=='__main__':unittest.main()
