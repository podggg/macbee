import json
import pathlib
import struct
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'tools'))
from coreaudio_protocol import payload, stats

class SyntheticTests(unittest.TestCase):
    def test_six_pulses_and_amplitude_ceiling(self):
        pulses = nonzero = peak = quiet = 0
        for chunk in range(600):
            for left, right in struct.iter_unpack('<hh', payload(chunk)):
                self.assertEqual(left, right)
                peak = max(peak, abs(left))
                if left:
                    if quiet >= 2400: pulses += 1
                    quiet = 0
                    nonzero += 1
                else: quiet += 1
        self.assertEqual(pulses, 6)
        self.assertEqual(peak, 163)
        self.assertEqual(nonzero, 27132)
    def test_silent_source_never_nonzero(self):
        self.assertTrue(all(not any(payload(n, True)) for n in range(600)))

class NativeResultTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(callbacks=55, pulses=0, nonzero_frames=0, peak_int16=0,
            time_errors=0, priming_time_errors=0, running_time_errors=0,
            enqueue_errors=0, overlimit_samples=0, priming_queries_skipped=2)
        setting = dict(device_id=84, sample_rate=48000, input_streams=0, volume=.0625, mute=0)
        self.guard = dict(before=setting, after=setting.copy(), error=None,
                          client_exit=0, reason='stop_or_eof')
    def check(self, silent=True):
        return stats('MACBEE_COREAUDIO '+json.dumps(self.row)+'\nMACBEE_GUARD_FINAL '+json.dumps(self.guard), silent)
    def test_clean_silent(self): self.check()
    def test_clean_synthetic(self):
        self.row.update(pulses=6, nonzero_frames=27132, peak_int16=163)
        self.check(False)
    def test_runtime_errors_fail(self):
        for key in ('time_errors','running_time_errors','enqueue_errors','overlimit_samples'):
            with self.subTest(key=key):
                self.row[key]=1
                with self.assertRaises(RuntimeError): self.check()
                self.row[key]=0
    def test_missing_pulse_fails(self):
        self.row.update(pulses=5, nonzero_frames=27132, peak_int16=163)
        with self.assertRaises(RuntimeError): self.check(False)
    def test_nonzero_silent_fails(self):
        self.row['nonzero_frames']=1
        with self.assertRaises(RuntimeError): self.check()
    def test_changed_volume_fails(self):
        self.guard['after']['volume']=.5
        with self.assertRaises(RuntimeError): self.check()
    def test_watchdog_is_not_success(self):
        self.guard['reason']='20-second-watchdog'
        with self.assertRaises(RuntimeError): self.check()
    def test_no_runtime_callbacks_fail(self):
        self.row['callbacks']=2
        with self.assertRaises(RuntimeError): self.check()
    def test_missing_stats_fail(self):
        with self.assertRaises(RuntimeError): stats('connected successfully',True)
    def test_child_failure_fails(self):
        self.guard['client_exit']=1
        with self.assertRaises(RuntimeError): self.check()

class PreflightTests(unittest.TestCase):
    def test_guard_failure_never_starts_a_process(self):
        from unittest import mock
        import run_coreaudio_probe as probe
        with mock.patch.object(probe, 'require_receiver'), mock.patch.object(probe, 'guard', side_effect=RuntimeError('preflight denied')):
            with mock.patch.object(probe.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(RuntimeError, 'preflight denied'):
                    probe.run(silent=True)
                start.assert_not_called()
    def test_canceled_preflight_never_starts_a_process(self):
        from unittest import mock
        import run_coreaudio_probe as probe
        with mock.patch.object(probe, 'require_receiver'), mock.patch.object(probe, 'guard', side_effect=KeyboardInterrupt('cancel')):
            with mock.patch.object(probe.subprocess, 'Popen') as start:
                with self.assertRaises(KeyboardInterrupt): probe.run(silent=True)
                start.assert_not_called()

if __name__ == '__main__': unittest.main()
