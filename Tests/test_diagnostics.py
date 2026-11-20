import importlib.util
import pathlib
import unittest
spec = importlib.util.spec_from_file_location('probe', pathlib.Path(__file__).parents[1]/'tools/dual_probe.py')
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)

class TimingTests(unittest.TestCase):
    def test_symmetric_offset(self):
        self.assertEqual(probe.clock_sample(1000, 2100, 2200, 1300), {'rtt_ns':200, 'offset_ns':1000})
    def test_monotonic_validation(self):
        for values in [(3,2,1,4),(3,4,5,2),(-1,2,3,4),(1,2,3,float('nan'))]:
            with self.assertRaises(ValueError): probe.clock_sample(*values)
    def test_negative_network_time(self):
        with self.assertRaises(ValueError): probe.clock_sample(0,100,1000,100)
    def test_filters_high_rtt_outliers(self):
        good = [{'rtt_ns':100, 'offset_ns':20} for _ in range(10)]
        bad = [{'rtt_ns':100000, 'offset_ns':999} for _ in range(50)]
        self.assertEqual(probe.estimate(good+bad)['offset_ns'],20)
    def test_few_samples_rejected(self):
        with self.assertRaises(ValueError): probe.estimate([])
    def test_preflight_blocks_busy_and_unknown(self):
        for n in (-1,1,2):
            with self.assertRaises(RuntimeError): probe.validate_ready({'active_other_outputs':n})
    def test_explicit_coexist_allows_known_output_count(self):
        probe.validate_ready({'active_other_outputs':2,'mute':0,'volume':.0625,'input_streams':0,'max_amplitude':.005},coexist=True)
    def test_coexist_never_allows_unknown_state(self):
        with self.assertRaises(RuntimeError): probe.validate_ready({'active_other_outputs':-1},coexist=True)
    def test_preflight_blocks_muted(self):
        with self.assertRaises(RuntimeError): probe.validate_ready({'active_other_outputs':0,'mute':1})
    def test_preflight_accepts_output_only(self):
        probe.validate_ready({'active_other_outputs':0,'mute':0,'volume':.0625,'input_streams':0,'max_amplitude':.005})
    def test_results_are_not_acoustic(self):
        def result(offset):
            return dict(interrupted=False,invalid_timestamps_or_buffers=0,stop_status=0,destroy_status=0,onset_host_ns=[1_000_000+i*1_000_000_000+offset for i in range(6)])
        r=probe.playback_result(result(0),result(2000),1000)
        self.assertAlmostEqual(r['max_abs_software_output_skew_ms'],.001)
        self.assertIn('NOT acoustic',r['label'])
    def test_missing_pulse_fails(self):
        r=dict(interrupted=False,invalid_timestamps_or_buffers=0,stop_status=0,destroy_status=0,onset_host_ns=[0]*6)
        with self.assertRaises(RuntimeError): probe.playback_result(r,r,0)

if __name__ == '__main__': unittest.main()
