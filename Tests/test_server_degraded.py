"""Synthetic server read telemetry; the sender is not paused or running late."""
from types import SimpleNamespace
import unittest

from test_sender_recovery_unit import MemorySession


class ServerInsertionFixture(MemorySession):
    def __init__(self, *, insert_at):
        super().__init__(pauses=[])
        self.reader_clock = SimpleNamespace(inserted=0)
        self.insert_at = dict(insert_at)
        self.reports = []

    def check(self):
        super().check()
        if self.turn in self.insert_at:
            self.reader_clock.inserted += self.insert_at[self.turn]
            self.reports.append(self.ms)


class ServerDegradedTests(unittest.TestCase):
    def test_only_new_server_concealment_marks_recovering_without_sender_stall(self):
        # The server concealed a 20 ms slot twice; each reading is cumulative.
        # Nothing else changes: the source and relay stay on the original grid.
        f = ServerInsertionFixture(insert_at=[(70, 960), (170, 960)])
        result, _ = f.exercise()
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['reason_code'], 'source_end')
        self.assertEqual(result['pacing_recoveries'], 0)
        self.assertEqual(result['discarded_source_frames'], 0)
        self.assertEqual(len(f.reports), 2)
        states = [(when, state) for when, state in f.states if when >= f.reports[0]]
        self.assertEqual([state for _, state in states], ['degraded', 'playing'])
        self.assertEqual(states[0][0], f.reports[0])
        self.assertGreaterEqual(states[1][0], f.reports[-1] + 2000)
        self.assertLess(states[1][0], f.reports[-1] + 2040)
        # Cumulative telemetry is not a level-triggered fault after recovery.
        self.assertEqual([state for when, state in f.states if when < f.reports[0]], ['playing'])
