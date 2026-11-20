"""No system audio or network. Only private socketpair/pipe with generated counters."""
import errno,pathlib,socket,struct,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from reader_clock import ReaderClock,WIRE,PROGRESS,STALE,MAX_UNACKED
from session_diagnostics import reason_code

class ServerClockTests(unittest.TestCase):
    def setUp(self):
        self.now=1_000_000_000
        self.c=ReaderClock(lambda:self.now);self.addCleanup(self.c.close)
        self.peer=self.c.tx.dup();self.addCleanup(self.peer.close)

    def send(self,frames,state=1,stamp=None,concealed=0,inserted=0):
        self.peer.send(WIRE.pack(state,frames,self.now if stamp is None else stamp,concealed,inserted))

    def test_socket_owned_and_bounded(self):
        self.assertEqual(self.c.rx.family,socket.AF_UNIX)
        self.assertFalse(self.c.rx.getblocking());self.assertFalse(self.c.tx.getblocking())
        opts=self.c.child_options();self.assertEqual(opts['pass_fds'],(self.c.tx.fileno(),))
        self.c.child_spawned();self.assertIsNone(self.c.tx)
        self.c.wrote(4*MAX_UNACKED)
        self.assertFalse(self.c.has_room(4));self.assertTrue(self.c.has_room(0))
        self.send(960);self.c.poll(require=True);self.assertTrue(self.c.has_room(3840))
        self.assertFalse(self.c.has_room(3841))
        self.assertEqual(self.c.max_unacked,MAX_UNACKED)

    def test_consumer_clock_not_write_clock(self):
        self.c.wrote(3840);self.send(960);self.c.poll(require=True)
        self.now+=300_000_000
        self.c.poll()  # drained + no source yet: do not invent an output consume failure
        self.c.wrote(3840);self.c.poll()  # newly outstanding, not 300ms old
        self.now+=250_000_001
        with self.assertRaisesRegex(RuntimeError,'Server read progress expired'):self.c.poll()

    def test_late_previous_receipt_cannot_expire_a_newly_submitted_block(self):
        self.c.wrote(3840);self.send(960);self.c.poll()
        self.c.wrote(3840);self.send(1920)  # queued, not observed before the pause
        self.now+=350_000_000
        self.c.wrote(3840);self.c.poll()  # New work after pause but before observing old receipt.
        self.assertEqual(self.c.frames,1920);self.assertEqual(len(self.c.outstanding),1)
        self.now+=249_000_000;self.c.wrote(3840);self.c.poll()
        self.now+=1_000_000
        with self.assertRaisesRegex(RuntimeError,'Server read progress expired'):self.c.poll()

    def test_preemption_after_empty_receive_cannot_invalidate_the_snapshot(self):
        self.c.wrote(3840);self.send(960);self.c.poll()
        self.c.wrote(3840)
        real=self.c.rx
        # Kernel decided EAGAIN before suspension; the server consumed the
        # block and its real acknowledgment arrived before Python ran again.
        owner=self
        class InterruptedReceive:
            def recv(self,n):
                try:return real.recv(n)
                except BlockingIOError:
                    owner.now+=20_000_000;owner.send(1920)
                    owner.now+=480_000_000
                    raise
        self.c.rx=InterruptedReceive()
        try:self.c.poll()
        finally:self.c.rx=real
        self.c.poll();self.assertEqual(self.c.frames,1920)
        self.assertEqual(self.c.diagnostics()['reader_last_report_age_ms'],480)
        # Conversely an unacknowledged complete block still expires at 250ms.
        self.c.wrote(3840);self.now+=250_000_000
        with self.assertRaisesRegex(RuntimeError,'Server read progress expired'):self.c.poll()

    def test_fragmented_writes_do_not_extend_the_oldest_outstanding_age(self):
        self.c.wrote(3840);self.send(960);self.c.poll()
        self.c.wrote(4);self.now+=220_000_000;self.c.wrote(3836)
        self.assertEqual(len(self.c.outstanding),1)
        self.now+=30_000_000
        with self.assertRaisesRegex(RuntimeError,'expired'):self.c.poll()

    def test_old_backlogged_message_does_not_renew_watch(self):
        self.c.wrote(3840*4);self.send(960)
        self.now+=275_000_000
        with self.assertRaisesRegex(RuntimeError,'Server read progress expired'):self.c.poll()

    def test_missing_capability_and_stale_message_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError,'unavailable'):self.c.poll(require=True)
        self.c.wrote(3840);self.send(0,STALE)
        with self.assertRaisesRegex(RuntimeError,'refused stale'):self.c.poll()

    def test_queue_full_does_not_block_or_expand(self):
        self.c.wrote(MAX_UNACKED*4);last=0
        for i in range(1,1025):
            try:self.send(i);last=i
            except OSError as err:
                if err.errno not in (errno.EAGAIN,errno.ENOBUFS):raise
                break
        self.assertGreater(last,0);self.assertLess(last,1024)
        for _ in range(20):self.c.poll()
        self.assertEqual(self.c.frames,last)
        self.send(MAX_UNACKED);self.c.poll()
        self.assertEqual(self.c.frames,MAX_UNACKED)

    def test_malformed_truncated_or_future_records(self):
        bad=(b'x',b'x'*(WIRE.size+10),WIRE.pack(1,960,self.now+1,0,0),WIRE.pack(9,960,self.now,0,0),WIRE.pack(1,961,self.now,0,0),struct.pack("=QQQ",1,960,self.now))
        self.c.wrote(3840)
        for packet in bad:
            self.peer.send(packet)
            with self.assertRaisesRegex(RuntimeError,'Invalid server read progress'):self.c.poll()
        self.send(960);self.c.poll()
        self.send(960);self.assertRaisesRegex(RuntimeError,'Invalid',self.c.poll)

    def test_specific_reader_reason_codes(self):
        for message,code in [('Server refused stale audio before distribution','server_media_stale'),
                             ('Server read progress expired','server_read_timeout'),
                             ('Invalid server read progress','server_read_invalid'),
                             ('Server read progress unavailable','server_read_unavailable')]:
            self.assertEqual(reason_code(RuntimeError(message),'check'),code)

    def test_true_input_credit_excludes_all_private_slot_header_bytes(self):
        import os
        from types import SimpleNamespace
        from null_transport import NullTransport
        clock=ReaderClock(lambda:self.now,bridged=True,slot_numbers=True);self.addCleanup(clock.close)
        rx,tx=os.pipe();self.addCleanup(os.close,rx)
        with os.fdopen(tx,'wb',buffering=0) as writer:
            s=object.__new__(NullTransport);s.reader_clock=clock;s.server=SimpleNamespace(stdin=writer);s.check=lambda:None
            s.write(bytes(3840))
            data=os.read(rx,3848);self.assertEqual((len(data),struct.unpack_from('=Q',data)[0]),(3848,1))
            clock.skip_slots(3);s.write(bytes(3840))
            data=os.read(rx,3848);self.assertEqual(struct.unpack_from('=Q',data)[0],5)
            self.assertEqual(clock.sent_bytes,7680)
            clock.tx.send(WIRE.pack(3,0,self.now,0,0))
            with self.assertRaisesRegex(RuntimeError,'Repeated server'):clock.poll()


    def test_recovery_counters_do_not_masquerade_as_reader_progress(self):
        self.c.wrote(4*1920)
        self.send(960,concealed=960,inserted=3840)
        self.c.poll(require=True)
        self.assertEqual(self.c.frames,960)
        self.assertEqual(self.c.diagnostics()['reader_inserted_frames'],3840)
        for concealed,inserted in [(0,3840),(960,0),(1921,3840),(961,3840),(960,3841),(960,960*40)]:
            self.send(1920,concealed=concealed,inserted=inserted)
            with self.assertRaisesRegex(RuntimeError,'Invalid server read progress'):self.c.poll()
        self.send(1920,concealed=960,inserted=3840)
        self.c.poll()
        self.assertEqual(self.c.frames,1920)
