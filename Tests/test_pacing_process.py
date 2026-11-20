"""Opt-in, private M3/Linux sender pressure test. No device or real receiver.
Use the current stage's manifest/bin/snapserver via MACBEE_PACING_STAGE.
MACBEE_PACING_BASE optionally points at a clean build-7 checkout and
MACBEE_PACING_BASE_STAGE at its matching binary stage. No real capture.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import select
import signal
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEADER = struct.Struct('<HHHiiiiI')
STAGE = os.environ.get('MACBEE_PACING_STAGE')


def verify_stage(stage, version):
    data = json.loads((stage / 'manifest.json').read_text())
    server = stage / 'bin' / 'snapserver'
    assert data['reader_clock_version'] == version
    if version==3:assert data['reader_slot_version']==1
    assert hashlib.sha256(server.read_bytes()).hexdigest() == data['binaries']['snapserver']['sha256']
    return server


def server_config(tmp,port,version):
    config = tmp / 'server.conf'
    config.write_text(f"""[server]
threads = 1
datadir = {tmp}
mdns_enabled = false
[http]
enabled = false
ssl_enabled = false
publish_http = false
publish_https = false
[tcp-control]
enabled = false
publish = false
[tcp-streaming]
enabled = true
bind_to_address = 127.0.0.1
port = {port}
publish = false
[stream]
source = pipe:///dev/stdin?name=private&mode=read&macbee_clock=v{version}{"-slots" if version==3 else ""}
sampleformat = 48000:16:2
codec = pcm
chunk_ms = 20
buffer = 500
""")
    return config


def relay(source, stage, tmp, port, version):
    sys.path.insert(0, str(source / 'tools'))
    from null_transport import NullTransport
    from reader_clock import ReaderClock
    from run_system_relay import run

    class Session(NullTransport):
        outputs = ['synthetic-a', 'synthetic-b']
        sinks = ['wire-only', 'wire-only']
        renderer_identity = None
        bounded_pacing = version == 3

        def start(self):
            self.reader_clock = ReaderClock(bridged=True,slot_numbers=True) if version == 3 else ReaderClock()
            config=server_config(tmp,port,version)
            opt = self.reader_clock.child_options()
            opt['env'].update(HOME=str(tmp), TMPDIR=str(tmp), XDG_CONFIG_HOME=str(tmp))
            with (tmp / 'server.log').open('wb') as log:
                self.server = subprocess.Popen([str(verify_stage(stage, version)), '--config', str(config)],
                    stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, bufsize=0, **opt)
            (tmp / 'owned-server-pid').write_text(str(self.server.pid))
            self.reader_clock.child_spawned()
            os.set_blocking(self.server.stdin.fileno(), False)
            until=time.monotonic()+3
            while True:
                try:
                    with socket.create_connection(('127.0.0.1',port),.05):pass
                    break
                except ConnectionRefusedError:
                    if time.monotonic()>=until:raise
                    time.sleep(.01)
            # Production warms its pipe before declaring ready. Otherwise OS
            # launch time can become an irrelevant initial timestamp offset.
            self.zeros(.5)
            self.reader_clock.poll(require=True)

        def check(self):
            self.reader_clock.poll()
            if self.server.poll() is not None:
                raise RuntimeError('Transport peer exited')

        def close(self):
            self.server.terminate()
            self.server.wait(3)
            self.server.stdin.close()
            self.cleanup_verified = self.server.returncode == 0
            self.reader_clock.close()

    sess = Session()
    result = run(sess, lambda: os.read(0, 16384) if select.select([0], [], [], .005)[0] else None)
    print(json.dumps(dict(result=result, clock=sess.reader_clock.diagnostics())), flush=True)


def recv_n(sock, size):
    parts = bytearray()
    while len(parts) < size:
        chunk = sock.recv(size - len(parts))
        if not chunk:
            raise EOFError
        parts.extend(chunk)
    return parts


class WirePeer(threading.Thread):
    """Verify generated marker and timestamp at the real distribution boundary."""
    def __init__(self, port, number):
        super().__init__()
        self.port, self.number, self.rows, self.error = port, number, [], None

    def run(self):
        try:
            end = time.monotonic() + 2
            while True:
                try:
                    sock = socket.create_connection(('127.0.0.1', self.port), .5)
                    break
                except ConnectionRefusedError:
                    if time.monotonic() >= end:
                        raise
                    time.sleep(.01)
            with sock:
                sock.settimeout(3)
                packet = json.dumps(dict(MAC=f'02:00:00:00:00:{self.number:02d}', HostName='fixture',
                    Version='0.35.0', ClientName='WireFixture', OS='synthetic', Arch='test',
                    Instance=1, ID=f'fixture-{self.number}', SnapStreamProtocolVersion=2)).encode()
                data = struct.pack('<I', len(packet)) + packet
                sock.sendall(HEADER.pack(5, 1, 0, 0, 0, 0, 0, len(data)) + data)
                while True:
                    kind, _, _, _, _, _, _, size = HEADER.unpack(recv_n(sock, HEADER.size))
                    assert size <= 65536
                    data = recv_n(sock, size)
                    if kind == 2:
                        sec, usec, length = struct.unpack_from('<iiI', data)
                        assert length == 3840 and len(data) == 3852
                        marker = struct.unpack_from('<h', data, 12)[0]
                        self.rows.append((sec*1000000 + usec, marker, time.monotonic()))
        except (EOFError, ConnectionResetError):
            pass
        except BaseException as error:
            self.error = error


def start_file_peers(stage, port, scratch, children):
    binary = stage/'bin'/'snapclient'
    manifest = json.loads((stage/'manifest.json').read_text())
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == manifest['binaries']['snapclient']['sha256']
    until = time.monotonic() + 2
    while True:
        try:
            with socket.create_connection(('127.0.0.1',port),.1):pass
            break
        except ConnectionRefusedError:
            if time.monotonic() >= until:raise
            time.sleep(.01)
    env = {**os.environ,'HOME':scratch,'TMPDIR':scratch,'XDG_CONFIG_HOME':scratch}
    for i in (1,2):
        children.append(subprocess.Popen([str(binary),'--player','file:filename=/dev/null','--mixer','none',
            '--logsink','stdout','--logfilter','*:info','--hostID',f'fixture-null-{i}',f'tcp://127.0.0.1:{port}'],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env))


def stop_file_peers(children):
    receipts = []
    for p in children:
        if p.poll() is None:p.terminate()
    for p in children:
        try:out,_ = p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill();out,_ = p.communicate(timeout=2)
        rows = [json.loads(x) for x in re.findall(rb'MACBEE_STATS (\{[^\n]+\})',out)]
        receipts.append(rows[0] if p.returncode==0 and len(rows)==1 and b'Player name: file' in out
                        and b'parameters: filename=/dev/null' in out else {})
    return receipts


def exercise(source, stage, version, pauses, *, pause_server=False, null_stage=None, lossless=False):
    sys.path.insert(0, str(ROOT / 'tools'))
    from system_audio_protocol import capture_clock_ns
    verify_stage(stage, version)
    with tempfile.TemporaryDirectory(prefix='macbee-fake-pressure-') as scratch:
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0));port = reserved.getsockname()[1]
        with subprocess.Popen([sys.executable, '-Bu', __file__, '--relay', str(source), str(stage), scratch,
                str(port), str(version)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, env={**os.environ, 'HOME':scratch, 'MACBEE_RUNTIME_HOME':scratch}) as runner:
            ready = runner.stdout.readline()
            assert b'"ready"' in ready, (ready, runner.stderr.read()[-800:])
            target = int((Path(scratch)/'owned-server-pid').read_text()) if pause_server else runner.pid
            def resume_owned():
                try:os.kill(target,signal.SIGCONT)
                except ProcessLookupError:pass
            sinks = [WirePeer(port, i) for i in (1, 2)]
            for p in sinks:p.start()
            last = time.monotonic();start = last;markers = {};remaining = b'';resumed = []
            os.set_blocking(runner.stdin.fileno(), False)
            stops = list(pauses);suspended = False;resume = None;i = 0
            players=[];played=[];source_pattern_frames=0;after=max(a+b for a,b in pauses)
            try:
                if null_stage:start_file_peers(null_stage,port,scratch,players)
                while time.monotonic() - start < after+(2.55 if null_stage else 1.35) and runner.poll() is None:
                    now = time.monotonic()
                    if suspended and now >= resume:
                        resume_owned();suspended = False;resumed.append(now)
                    if stops and now - start >= stops[0][0]:
                        _, d = stops.pop(0);os.kill(target,signal.SIGSTOP);suspended = True;resume = now+d
                    if now >= last:
                        last += .020;i += 1
                        if not remaining or lossless:
                            stamp = capture_clock_ns()
                            data = (struct.pack('<4sIQ', b'MBP4', 960, stamp) if version == 3
                                    else struct.pack('<4sI', b'MBP2', 960))
                            left,right=i,i
                            if null_stage:
                                # No matching PCM exists before all suspensions finish.
                                left=8*(1+i%127) if after+.28 < now-start <= after+1.28 else 0
                                right=-left
                                if left:source_pattern_frames+=960
                            remaining += struct.pack('<4sI', b'MBH2', 2) + data + struct.pack('<hh', left, right)*960
                            assert len(remaining)<160000
                            markers[i] = now
                    if remaining:
                        try:remaining = remaining[os.write(runner.stdin.fileno(), remaining):]
                        except BlockingIOError:pass
                        except BrokenPipeError:break
                    time.sleep(.001)
            finally:
                if suspended:resume_owned()
                if players:played=stop_file_peers(players)
                if remaining and runner.poll() is None:
                    os.set_blocking(runner.stdin.fileno(), True)
                    try:runner.stdin.write(remaining)
                    except BrokenPipeError:pass
                runner.stdin.close();runner.stdin = None
            out, err = runner.communicate(timeout=5)
            for p in sinks:
                p.join(4);assert not p.is_alive() and p.error is None, p.error
            assert runner.returncode == 0, err[-1200:]
            receipt = json.loads(out.splitlines()[-1])
            if null_stage:receipt.update(synthetic_source_frames=source_pattern_frames,file_peers=played)
            return receipt, [p.rows for p in sinks], markers, resumed


class ServerOnly:
    """Black-box V3 binary with two wire observers and no relay controller."""
    def __init__(self,test):
        self.test=test;self.slot=0;self.real_count=0;self.terminal=None
        self.last_frame=0;self.last_inserted=0;self.terminal_inserted=0

    def __enter__(self):
        from contextlib import ExitStack
        sys.path.insert(0,str(ROOT/'tools'))
        from reader_clock import WIRE
        self.wire=WIRE
        self.stack=ExitStack()
        try:
            tmp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='macbee-clock-only-')))
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            self.rx,tx=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
            self.stack.callback(self.rx.close);self.stack.callback(tx.close);self.rx.setblocking(False)
            self.child=subprocess.Popen([str(verify_stage(Path(STAGE),3)),'--config',str(server_config(tmp,port,3))],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,bufsize=0,
                pass_fds=(tx.fileno(),),env={**os.environ,'MACBEE_READER_FD':str(tx.fileno()),'HOME':str(tmp),
                    'XDG_CONFIG_HOME':str(tmp),'TMPDIR':str(tmp)})
            self.stack.callback(self.close_owned);tx.close()
            os.set_blocking(self.child.stdin.fileno(),False)
            self.peers=[WirePeer(port,n) for n in (3,4)]
            for peer in self.peers:peer.start()
            return self
        except BaseException:
            self.stack.close();raise

    def close_owned(self):
        if self.child.poll() is None:self.child.terminate()
        try:self.child.wait(3)
        except subprocess.TimeoutExpired:
            self.child.kill();self.child.wait(2);self.test.fail('isolated server did not stop')
        finally:self.child.stdin.close()
        for peer in getattr(self,'peers',[]):
            peer.join(4);self.test.assertFalse(peer.is_alive());self.test.assertIsNone(peer.error)

    def __exit__(self,*exc):self.stack.close()

    def skip(self,n):self.slot+=n

    def send(self):
        self.slot+=1;self.real_count+=1
        remaining=memoryview(struct.pack('=Q',self.slot)+struct.pack('=hh',self.slot,self.slot)*960)
        until=time.monotonic()+.45
        while remaining:
            if not select.select([],[self.child.stdin],[],max(0,until-time.monotonic()))[1]:
                raise TimeoutError('isolated server did not consume its bounded input')
            try:remaining=remaining[os.write(self.child.stdin.fileno(),remaining):]
            except BlockingIOError:
                if time.monotonic()>=until:raise
        self.drain()

    def drain(self):
        while select.select([self.rx],[],[],0)[0]:
            state,frames,stamp,concealed,inserted=self.wire.unpack(self.rx.recv(128))
            if state==1:self.last_frame=frames;self.last_inserted=inserted
            else:
                self.terminal=state;self.terminal_inserted=inserted;self.last_frame=frames

    def feed(self,n):
        start=time.monotonic()
        for i in range(n):
            self.send();time.sleep(max(0,start+(i+1)*.020-time.monotonic()))
        self.drain()


@unittest.skipUnless(STAGE, 'requires explicit isolated sender pressure stage')
class PacingProcessTests(unittest.TestCase):
    def test_real_sender_pauses_and_two_uninterrupted_wire_peers(self):
        pauses = [(1.0,.300), (2.2,.400), (3.5,.500), (4.3,.320)]
        r, peers, markers, resumed = exercise(ROOT, Path(STAGE), 3, pauses)
        self.assertEqual(r['result']['reason_code'], 'source_eof',r)
        self.assertTrue(r['result']['cleanup_verified'],r)
        self.assertEqual(r['result']['pacing_recoveries'],len(pauses),r)
        self.assertGreater(r['clock']['reader_inserted_frames'], 0)
        self.assertLessEqual(r['clock']['reader_max_unacked_frames'], 5760)
        a,b = [dict((stamp,(marker,t)) for stamp,marker,t in rows) for rows in peers]
        shared = sorted(a.keys() & b.keys())
        self.assertGreater(len(shared), 235)
        self.assertEqual([a[k][0] for k in shared],[b[k][0] for k in shared])
        for rows in peers:
            self.assertTrue(all(y[0]-x[0]==20000 for x,y in zip(rows,rows[1:])))
            self.assertEqual([m for _,m,_ in rows if m],sorted(m for _,m,_ in rows if m))
            for t in resumed:
                self.assertTrue(any(m and markers[m]>=t-.005 and t<=arr<t+.200 for _,m,arr in rows))
        ages=[arr-markers[m] for _,m,arr in peers[0] if m]
        self.assertLess(max(ages),.160)
        print('two-wire-peer receipt',json.dumps(dict(shared_slots=len(shared),recoveries=len(resumed),
              max_marker_wire_age_ms=round(max(ages)*1000,1),median_ms=round(statistics.median(ages)*1000,1),
              discarded_frames=r['result']['discarded_source_frames'],**r['clock'])),flush=True)

    @unittest.skipUnless(os.environ.get('MACBEE_PACING_NULL_STAGE'), 'matching instrumented file-client stage required')
    def test_two_real_file_players_render_the_post_recovery_signal(self):
        for repetition in range(2):
            with self.subTest(repetition=repetition):
                r,_,_,_=exercise(ROOT,Path(STAGE),3,[(1.3,.300),(2.2,.400),(3.3,.500),(4.25,.320)],
                                 null_stage=Path(os.environ['MACBEE_PACING_NULL_STAGE']))
                self.assertEqual(r['result']['reason_code'],'source_eof',r)
                self.assertEqual(r['result']['pacing_recoveries'],4)
                self.assertTrue(r['result']['cleanup_verified'])
                frames=r['synthetic_source_frames']
                self.assertGreaterEqual(frames,47040);self.assertEqual(len(r['file_peers']),2)
                for stats in r['file_peers']:
                    self.assertEqual(stats['other_frames'],0)
                    self.assertGreaterEqual(stats['pattern_frames'],.95*frames)
                    self.assertLessEqual(stats['pattern_frames'],1.05*frames)
                print('real null-player receipt',json.dumps(dict(repetition=repetition+1,
                      input_frames=frames,players=r['file_peers'])),flush=True)

    def test_short_jitter_uses_real_buffered_audio_without_extra_latency(self):
        for ms in (60,100,150,200):
            with self.subTest(pause_ms=ms):
                r,peers,markers,resumed=exercise(ROOT,Path(STAGE),3,[(1.5,ms/1000)],lossless=True)
                self.assertEqual(r['result']['reason_code'],'source_eof',r)
                self.assertEqual(r['clock']['reader_inserted_frames'],0,r)
                self.assertEqual(r['result']['discarded_source_frames'],0,r)
                self.assertEqual(r['result']['pacing_recoveries'],0,r)
                t=resumed[0]
                for row in peers:
                    seq=[v for _,v,at in row if t-ms/1000-.15<at<t+.70]
                    self.assertNotIn(0,seq)
                    # Whole contiguous real PCM, not a zero-filled or duplicate catch-up.
                    self.assertEqual(seq,list(range(seq[0],seq[0]+len(seq))))
                    before=[at-markers[m] for _,m,at in row if m and t-1.1<at<t-.40]
                    after=[at-markers[m] for _,m,at in row if m and t+.75<at<t+1.15]
                    delta=statistics.median(after)-statistics.median(before)
                    self.assertLess(abs(delta),.015,(ms,delta))
                print('short pause no added silence or drift',ms,flush=True)

    def test_near_threshold_disagreement_cannot_add_a_permanent_queue(self):
        for ms in (235,245,255,265,300,500):
            with self.subTest(pause_ms=ms):
                r,peers,markers,resumed=exercise(ROOT,Path(STAGE),3,[(1.5,ms/1000)],lossless=True)
                self.assertEqual(r['result']['reason_code'],'source_eof',r)
                self.assertTrue(r['result']['cleanup_verified']);t=resumed[0]
                for row in peers:
                    window=[m for _,m,at in row if t-ms/1000-.10<at<t+.65]
                    blank_runs=sum(1 for i,m in enumerate(window) if not m and (not i or window[i-1]))
                    self.assertLessEqual(blank_runs,1,(ms,window))
                    before=[at-markers[m] for _,m,at in row if m and t-1.1<at<t-.4]
                    after=[at-markers[m] for _,m,at in row if m and t+.75<at<t+1.15]
                    delta=statistics.median(after)-statistics.median(before)
                    # One source quantum of phase is allowed, never 120-250ms.
                    self.assertLess(abs(delta),.030,(ms,delta))
                print('boundary',ms,'ms; blank runs',blank_runs,'post/pre latency delta ms',round(delta*1000,1),flush=True)

    def test_whole_chain_stops_on_fifth_gap(self):
        pauses=[(.9+i*.75,.32) for i in range(5)]
        r,_,_,_=exercise(ROOT,Path(STAGE),3,pauses)
        self.assertIn(r['result']['reason_code'],('sender_pacing_recovery_limit','server_pacing_recovery_limit'),r)
        self.assertEqual(r['result']['pacing_recoveries'],4)
        self.assertTrue(r['result']['cleanup_verified'])

    def test_server_only_fifth_autonomous_gap_returns_its_own_reason(self):
        # No relay, no sender budget, no fake watchdog. A silent sender leaves
        # the last true-read stamp stationary; only the real server can signal.
        with ServerOnly(self) as server:
            server.feed(38)
            for i in range(4):
                time.sleep(.36);server.drain()
                self.assertIsNone(server.terminal,('premature cutoff',i))
                self.assertEqual(server.last_frame,server.real_count*960)
                server.skip(18);server.feed(23)
            time.sleep(.38);server.drain()
            self.assertEqual(server.terminal,3)  # Wire contract: repeated recoveries, distinct from stale (2).

    def test_server_only_late_real_slots_never_append_or_renew_on_silence(self):
        with ServerOnly(self) as server:
            server.feed(42);last_slot=server.slot
            time.sleep(.37);server.drain()
            self.assertEqual(server.last_frame,server.real_count*960)
            # Send a backlog from the old grid, including slots the server has
            # already irrevocably filled. All real input earns read credit once.
            for _ in range(18):server.send()
            server.feed(30);time.sleep(.05);server.drain()
            self.assertEqual(server.last_frame,server.slot*960)
            self.assertIsNone(server.terminal)
            for peer in server.peers:
                zero_pts=next(t for t,m,_ in peer.rows if m==last_slot)-last_slot*20000
                gap=[(t,m) for t,m,_ in peer.rows if t>zero_pts+last_slot*20000]
                self.assertGreaterEqual(sum(m==0 for _,m in gap),12)
                self.assertTrue(all(m==0 or t==zero_pts+m*20000 for t,m in gap),gap)
                self.assertTrue(all(m for t,m in gap if t>zero_pts+(last_slot+21)*20000))
            before=server.last_frame;server.feed(1);time.sleep(.77);server.drain()
            self.assertEqual(server.terminal,2);self.assertEqual(server.last_frame,before+960)
            self.assertLessEqual(server.terminal_inserted-server.last_inserted,34*960)
            self.assertGreaterEqual(server.terminal_inserted-server.last_inserted,26*960)

    def test_prolonged_absence_still_stops(self):
        r, _, _, _ = exercise(ROOT, Path(STAGE), 3, [(1,.95)])
        self.assertIn(r['result']['reason_code'], ('output_pacing_stall','server_media_stale','server_read_timeout'))
        self.assertEqual(r['result']['pacing_recoveries'],0)
        if r['result']['reason_code']=='server_read_timeout':
            # The sender was genuinely stopped for 950 ms. Depending on which
            # phase resumes first, a previously outstanding read may already be
            # expired before the hard sender/media deadline is inspected.
            self.assertGreater(r['clock']['reader_submitted_frames'],r['clock']['reader_consumed_frames'])
            self.assertGreaterEqual(r['clock']['reader_last_report_age_ms'],250)
        self.assertTrue(r['result']['cleanup_verified'])

    def test_nonconsuming_server_does_not_get_synthetic_read_credit(self):
        r, _, _, _ = exercise(ROOT, Path(STAGE), 3, [(1,.55)], pause_server=True)
        self.assertEqual(r['result']['reason_code'],'server_read_timeout',r)
        self.assertLessEqual(r['clock']['reader_max_unacked_frames'],5760)
        self.assertTrue(r['result']['cleanup_verified'],r)

    @unittest.skipUnless(os.environ.get('MACBEE_PACING_BASE_STAGE') and os.environ.get('MACBEE_PACING_BASE'), 'explicit clean build-7 baseline')
    def test_build7_reproduces_on_same_machine(self):
        r, _, _, _ = exercise(Path(os.environ['MACBEE_PACING_BASE']),Path(os.environ['MACBEE_PACING_BASE_STAGE']),2,[(1.,.400)])
        self.assertIn(r['result']['reason_code'],('output_pacing_stall','server_read_timeout','server_media_stale'),r)
        print('build-7 first cause',r['result']['reason_code'],flush=True)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--relay']:
        relay(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]))
    else:unittest.main()
