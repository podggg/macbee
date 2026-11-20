"""Private fixed-size progress channel for one owned server. Never carries PCM."""
from collections import deque
import os
import sys
import socket
import struct
import time

WIRE = struct.Struct('=QQQQQ')  # state, actual frames + time, concealed capture frames, inserted output frames
PROGRESS, STALE, REPEATED = 1, 2, 3
MAX_UNACKED = 5760  # 120ms at 48 kHz, includes up to 40ms of report coalescing
FRESH_NS = 250_000_000
STARTUP_NS = 1_000_000_000


def shared_clock_ns():
    # The system Python 3.9 monotonic() epoch is process-local. libc++ steady_clock
    # on the supported Darwin target shares CLOCK_MONOTONIC_RAW, verified separately.
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW if sys.platform == 'darwin' else time.CLOCK_MONOTONIC)


class ReaderClock:
    def __init__(self, clock=shared_clock_ns, *, bridged=False, slot_numbers=False):
        self.slot_numbers=slot_numbers;self.last_slot=0
        self.max_insert_ratio = 51 if bridged else 17  # 34 timer + 17 returned-read slots
        self.clock = clock
        self.rx, self.tx = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            for sock in (self.rx, self.tx):
                sock.setblocking(False)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        except BaseException:
            self.close()
            raise
        self.sent_bytes = self.frames = self.stamp = self.max_unacked = 0
        self.first_send = None
        self.outstanding = deque()  # <= seven frame-aligned slabs; no samples
        self.concealed = self.inserted = 0

    def child_options(self):
        fd = self.tx.fileno()
        return dict(pass_fds=(fd,), env={**os.environ, 'MACBEE_READER_FD': str(fd)})

    def child_spawned(self):
        if self.tx is not None:
            self.tx.close()
            self.tx = None

    def close(self):
        if self.rx is not None:
            self.rx.close()
            self.rx = None
        self.child_spawned()

    def wrote(self, n):
        if type(n) is not int or n<=0 or not self.has_room(n):
            raise RuntimeError('Invalid server read progress')
        now=self.clock()
        if self.first_send is None:self.first_send=now
        # A delayed receipt can acknowledge every OLD write after a new write
        # just arrived. Its old timestamp must not instantly expire that newly
        # outstanding work. Never renew the age of earlier unconsumed bytes.
        end=self.sent_bytes+n
        while self.sent_bytes<end:
            limit=min(end,(self.sent_bytes//3840+1)*3840)
            if self.outstanding and self.sent_bytes%3840:
                _,first=self.outstanding.pop();self.outstanding.append((limit,first))
            else:self.outstanding.append((limit,now))
            self.sent_bytes=limit
        self.max_unacked = max(self.max_unacked, (self.sent_bytes + 3) // 4 - self.frames)

    def skip_slots(self,count):
        if not self.slot_numbers:return
        if not 0<=count<=34:raise RuntimeError('System output pacing stalled')
        self.last_slot+=count
    def slot_header(self):
        if not self.slot_numbers:return b''
        self.last_slot+=1
        if self.last_slot>=2**64:raise RuntimeError('System output pacing stalled')
        return struct.pack('=Q',self.last_slot)

    def has_room(self, n):
        return self.sent_bytes + n - 4 * self.frames <= 4 * MAX_UNACKED

    def poll(self, require=False):
        # Bounded even if a malformed peer floods the channel. A valid producer
        # emits <=25 progress records/sec and at most one terminal record.
        for _ in range(64):
            # The absence of another datagram is evidence only as of the
            # nonblocking receive, not when Python next gets CPU time. An
            # acknowledgment may arrive immediately after the kernel says EAGAIN.
            observed_at=self.clock()
            try:
                packet = self.rx.recv(WIRE.size + 1)
            except BlockingIOError:
                break
            except OSError as error:
                raise RuntimeError('Server read progress unavailable') from error
            if len(packet) != WIRE.size:
                raise RuntimeError('Invalid server read progress')
            state, frames, stamp, concealed, inserted = WIRE.unpack(packet)
            now = self.clock()
            if ((state not in (PROGRESS, STALE) and not (self.slot_numbers and state==REPEATED)) or stamp > now or stamp < self.stamp or
                    frames < self.frames or frames * 4 > self.sent_bytes or
                    inserted < self.inserted or concealed < self.concealed or concealed > frames or
                    concealed - self.concealed > frames - self.frames or concealed % 960 or
                    inserted % 960 or inserted > self.max_insert_ratio * frames):
                raise RuntimeError('Invalid server read progress')
            if state == REPEATED:raise RuntimeError('Repeated server pacing interruptions')
            if state == STALE:
                raise RuntimeError('Server refused stale audio before distribution')
            if frames <= self.frames or not stamp:
                raise RuntimeError('Invalid server read progress')
            self.frames, self.stamp, self.concealed, self.inserted = frames, stamp, concealed, inserted
            while self.outstanding and self.outstanding[0][0]<=4*self.frames:self.outstanding.popleft()
        now = observed_at
        if not self.stamp and (require or (self.first_send is not None and now - self.first_send > STARTUP_NS)):
            raise RuntimeError('Server read progress unavailable')
        # A fully drained pipe can legitimately have no progress while waiting for
        # input. The separate source health and sender scheduling guards still apply.
        if self.stamp and self.sent_bytes > self.frames * 4 and now - max(self.stamp, self.outstanding[0][1] if self.outstanding else 0) >= FRESH_NS:
            raise RuntimeError('Server read progress expired')

    def diagnostics(self):
        return dict(reader_inserted_frames=self.inserted, reader_concealed_frames=self.concealed, reader_consumed_frames=self.frames, reader_submitted_frames=self.sent_bytes // 4,
                    reader_max_unacked_frames=self.max_unacked,
                    reader_last_report_age_ms=(max(0, self.clock() - self.stamp) // 1_000_000 if self.stamp else 0))
