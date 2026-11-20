"""Sender pacing regressions: virtual time and generated PCM, no audio or network."""
import pathlib
import signal
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from run_system_relay import run
from session_diagnostics import RelaySignal, StopTrace


class Fixture:
    outputs = ["local"]
    sinks = ["memory"]
    renderer_identity = None
    run_dir = None

    def __init__(self, *, loop_check_ms=0, fault=None, silent=False):
        self.ms = self.turn = self.closes = 0
        self.check_ms, self.fault, self.silent = loop_check_ms, fault, silent
        self.next_frame_ms, self.next_health_ms = 5, 1
        self.input = bytearray()
        self.writes, self.output = [], []
        self.cleanup_verified = False
        self.trace = StopTrace(self.clock)

    def clock(self):
        return self.ms / 1000

    def start(self):
        pass

    def check(self):
        self.turn += 1
        self.ms += self.check_ms
        if self.turn == 12 and self.fault in ("check_stall", "recoverable_bump"):
            self.ms += 400 if self.fault == "check_stall" else 115

    def read(self):
        self.ms += 5
        if self.ms >= 3100 and self.fault != "expired":
            return b""
        while self.next_health_ms <= self.ms:
            self.input.extend(struct.pack("<4sI", b"MBH2", 2))
            self.next_health_ms += 10000 if self.fault == "expired" else 500
        while self.next_frame_ms <= self.ms:
            marker = 0 if self.silent else self.next_frame_ms // 5
            self.input.extend(struct.pack("<4sI", b"MBP2", 240))
            self.input.extend(struct.pack("<hh", marker, marker) * 240)
            self.next_frame_ms += 5
        data = bytes(self.input[:16384])
        del self.input[:16384]
        return data or None

    def write(self, chunk):
        assert len(chunk) == 3840
        extra = self.writes and self.writes[-1][0] == self.turn
        if extra and self.fault == "stop_on_extra":
            raise RelaySignal(signal.SIGTERM)
        if extra and self.fault == "backpressure":
            raise TimeoutError("PCM pipe backpressure")
        if self.fault == "write_stall" and len(self.writes) == 20:
            self.ms += 400
        self.writes.append((self.turn, self.ms))
        self.output.extend(struct.unpack_from("<h", chunk, i)[0] for i in range(0, 3840, 960))

    def close(self):
        self.closes += 1
        self.cleanup_verified = True

    def run(self):
        return run(self, self.read, lambda *args, **kwargs: None, self.clock, self.trace)


class SystemPacingTests(unittest.TestCase):
    def test_repeated_25ms_work_does_not_accumulate_false_stop(self):
        for silent in (False, True):
            with self.subTest(silent=silent):
                f = Fixture(loop_check_ms=20, silent=silent)
                result = f.run()
                self.assertEqual(result["reason_code"], "source_eof")
                self.assertTrue(result["cleanup_verified"])
                self.assertEqual(f.closes, 1)
                turns = [t for t, _ in f.writes]
                self.assertEqual(max(turns.count(t) for t in set(turns)), 2)
                self.assertGreater(len(f.writes), 145)
                self.assertEqual(result["padding_frames"], 0)
                self.assertGreater(f.trace.metrics["catchup_writes"], 0)
                if silent:
                    self.assertFalse(any(f.output))
                else:
                    self.assertEqual(f.output, list(range(1, len(f.output) + 1)))

    def test_backlog_never_burst_drains_or_drops_markers(self):
        f = Fixture(fault="recoverable_bump")
        self.assertEqual(f.run()["reason_code"], "source_eof")
        turns = [t for t, _ in f.writes]
        self.assertEqual(max(turns.count(t) for t in set(turns)), 2)
        self.assertEqual(f.output, list(range(1, len(f.output) + 1)))
        self.assertEqual(f.closes, 1)

    def test_end_packet_does_not_flush_queued_pcm(self):
        f = Fixture(loop_check_ms=20)
        def end():
            f.ms += 5
            return struct.pack("<4sI4sIhh4sI", b"MBH2", 2, b"MBP2", 1, 123, 123, b"MBP2", 0)
        result = run(f, end, lambda *a, **kw: None, f.clock, f.trace)
        self.assertEqual(result["reason_code"], "source_end")
        self.assertEqual(f.writes, [])
        self.assertEqual(f.closes, 1)

    def test_ordinary_timing_emits_single_due_block(self):
        f = Fixture()
        self.assertEqual(f.run()["reason_code"], "source_eof")
        turns = [t for t, _ in f.writes]
        self.assertEqual(len(turns), len(set(turns)))
        self.assertGreater(len(turns), 145)
        self.assertEqual(f.closes, 1)

    def test_real_stalls_and_transport_errors_still_stop(self):
        for fault, reason in [("check_stall", "output_pacing_stall"),
                              ("write_stall", "output_pacing_stall"),
                              ("stop_on_extra", "signal_term"),
                              ("backpressure", "pcm_backpressure"),
                              ("expired", "source_health_timeout")]:
            with self.subTest(fault=fault):
                f = Fixture(loop_check_ms=20, fault=fault)
                self.assertEqual(f.run()["reason_code"], reason)
                self.assertEqual(f.closes, 1)
                if fault in ("check_stall", "write_stall"):
                    stage = fault.split("_")[0]
                    self.assertGreaterEqual(f.trace.first_snapshot["metrics"]["max_" + stage + "_s"], .399)


if __name__ == "__main__":
    unittest.main()
