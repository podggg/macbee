"""No private stage, devices, processes or network needed for these regressions."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from run_system_relay import run
from session_diagnostics import StopTrace


class MemorySession:
    outputs=['local'];sinks=['memory'];renderer_identity=None;run_dir=None;bounded_pacing=True
    def __init__(self,pauses):
        self.ms=0;self.until_turn=610;self.last_source=0;self.pauses=dict(pauses);self.turn=0
        self.writes=[];self.states=[];self.suspended=[];self.input=bytearray();self.cleanup_verified=False
    def clock(self):return self.ms/1000.
    def raw(self):return 1_000_000_000+self.ms*1_000_000
    def start(self):pass
    def close(self):self.cleanup_verified=True
    def check(self):
        self.turn+=1
        if self.turn in self.pauses:
            self.ms+=self.pauses[self.turn];self.suspended.append(self.ms)
    def read(self):
        self.ms+=5
        if self.turn>self.until_turn:return struct.pack('<4sI',b'MBP2',0)
        while self.last_source+20<=self.ms:
            self.last_source+=20;k=self.last_source//20
            self.input+=struct.pack('<4sI4sIQ',b'MBH2',2,b'MBP3',960,1_000_000_000+self.last_source*1_000_000)+struct.pack('<hh',k,k)*960
        data=bytes(self.input[:16384]);del self.input[:16384];return data or None
    def write(self,data):
        self.writes.append((self.ms,struct.unpack_from('<h',data)[0]))
    def exercise(self):
        trace=StopTrace(self.clock)
        with patch('run_system_relay.shared_clock_ns',self.raw):
            def event(kind,**fields):
                if kind=='state':self.states.append((self.ms,fields['state']))
            r=run(self,self.read,event,self.clock,trace)
        return r,trace


class SenderRecoveryUnitTests(unittest.TestCase):
    def test_recovery_does_not_burst_replay_expired_pcm_or_manufacture_a_zero_slot(self):
        f=MemorySession([(120,400)]);r,trace=f.exercise()
        self.assertTrue(r['cleanup_verified']);self.assertEqual(r['reason_code'],'source_end')
        self.assertEqual(r['pacing_recoveries'],1)
        end=f.suspended[0];after=[(at,m) for at,m in f.writes if at>=end]
        self.assertGreaterEqual(after[0][0]-end,20)
        self.assertEqual(len(after),len(set(at for at,_ in after)))
        self.assertTrue(all(m and at-50<=20*m<=at for at,m in after),after[:8])
        self.assertGreater(trace.metrics['discarded_source_frames'],0)

    def test_rolling_limit_and_expiry(self):
        f=MemorySession([(n,300) for n in (100,170,240,310,380)])
        r,_=f.exercise();self.assertEqual(r['reason_code'],'sender_pacing_recovery_limit');self.assertEqual(r['pacing_recoveries'],4)
        f=MemorySession([(n,300) for n in (100,170,240,310,2400)]);f.until_turn=2550
        r,_=f.exercise();self.assertEqual(r['reason_code'],'source_end');self.assertEqual(r['pacing_recoveries'],5)

    def test_degraded_is_visible_and_then_recovers_without_restart(self):
        f=MemorySession([(100,340),(180,300)])
        r,_=f.exercise()
        self.assertEqual(r['reason_code'],'source_end')
        states=[(t,s) for t,s in f.states if t>=f.suspended[0]]
        self.assertEqual([s for t,s in states],['degraded','playing'])
        self.assertGreaterEqual(states[1][0]-f.suspended[-1],2000)
        self.assertLess(states[1][0]-f.suspended[-1],2050)

    def test_recovery_slot_jump_stays_within_server_35_slot_limit(self):
        class SlottedSession(MemorySession):
            def __init__(self,pauses):
                super().__init__(pauses)
                self.last_slot=0;self.last_written_slot=0;self.max_delta=0
                self.reader_clock=self
            def skip_slots(self,count):
                assert 0<=count<=34,count
                self.last_slot+=count
            def diagnostics(self):return {}
            def write(self,data):
                self.last_slot+=1
                if self.last_written_slot:
                    self.max_delta=max(self.max_delta,self.last_slot-self.last_written_slot)
                self.last_written_slot=self.last_slot
                super().write(data)
        ok=SlottedSession([(120,550)])
        r,_=ok.exercise()
        self.assertEqual(r['reason_code'],'source_end')
        self.assertEqual(r['pacing_recoveries'],1)
        self.assertLessEqual(ok.max_delta,35)
        too_long=SlottedSession([(120,620)])
        r2,_=too_long.exercise()
        self.assertEqual(r2['reason_code'],'output_pacing_stall')
        self.assertLessEqual(too_long.max_delta,35)
