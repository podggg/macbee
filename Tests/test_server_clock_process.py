"""Opt-in Linux or Darwin integration test. Generated counters -> private stdin Snapserver.
No TCP/HTTP/mDNS listeners, clients, audio devices, or capture. Set
MACBEE_OFFLINE_STAGE to a matching stage with manifest.json/bin/snapserver.
"""
import hashlib,json,os,pathlib,signal,subprocess,sys,tempfile,threading,time,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from null_transport import NullTransport
from reader_clock import ReaderClock,MAX_UNACKED,shared_clock_ns
from session_diagnostics import reason_code
STAGE=os.environ.get('MACBEE_OFFLINE_STAGE')

@unittest.skipUnless(sys.platform in ('darwin','linux') and STAGE,'requires explicit matching isolated test stage')
class ServerClockProcess(unittest.TestCase):
    def exercise(self,suspensions,seconds,recovered_input=False):
        stage=pathlib.Path(STAGE)
        manifest=json.loads((stage/'manifest.json').read_text());binary=stage/'bin/snapserver'
        self.assertIn(manifest['reader_clock_version'],(2,3))
        if manifest['reader_clock_version']==3:self.assertEqual(manifest['reader_slot_version'],1)
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(),manifest['binaries']['snapserver']['sha256'])
        with tempfile.TemporaryDirectory(prefix='macbee-private-reader-') as tmp:
            d=pathlib.Path(tmp);(d/'data').mkdir();conf=d/'snapserver.conf'
            conf.write_text(f'''[server]
threads = 1
datadir = {d}/data
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
enabled = false
publish = false
[stream]
source = pipe:///dev/stdin?name=private&mode=read&macbee_clock=v2
sampleformat = 48000:16:2
codec = pcm
chunk_ms = 20
buffer = 500
''')
            progress=ReaderClock();self.addCleanup(progress.close);opt=progress.child_options()
            env=opt['env'];env.update(HOME=tmp,TMPDIR=tmp,XDG_CONFIG_HOME=tmp)
            with (d/'log').open('wb') as log:
                child=subprocess.Popen([str(binary),'--config',str(conf)],stdin=subprocess.PIPE,
                                       stdout=log,stderr=subprocess.STDOUT,bufsize=0,cwd=tmp,**opt)
            progress.child_spawned();stop=threading.Event();worker=None;result='completed';marks=[]
            try:
                os.set_blocking(child.stdin.fileno(),False)
                transport=object.__new__(NullTransport);transport.reader_clock=progress;transport.server=child
                def check():
                    progress.poll()
                    if child.poll() is not None:raise RuntimeError('Transport peer exited')
                transport.check=check;readiness=time.monotonic()+2
                while (d/'log').stat().st_size==0 and time.monotonic()<readiness:time.sleep(.005)
                start=time.monotonic()
                for i in range(150):
                    transport.write(bytes(3840));time.sleep(max(0,start+(i+1)*.02-time.monotonic()))
                progress.poll(require=True);base=progress.sent_bytes//4;epoch=shared_clock_ns();marks=[]
                if recovered_input:
                    # Actual source starvation, twice: once all real PCM has been
                    # acknowledged do not write for 277 ms. Resume at exactly
                    # the nominal rate: no backlog is available to repay debt.
                    # No fake progress, socket listener, client, or hardware sink.
                    for incident in range(2):
                        end=time.monotonic()+.24
                        while progress.frames*4<progress.sent_bytes and time.monotonic()<end:
                            progress.poll();time.sleep(.002)
                        self.assertEqual(progress.frames*4,progress.sent_bytes)
                        time.sleep(max(0,(progress.stamp+277_000_000-shared_clock_ns())/1e9))
                        begin=time.monotonic()
                        for n in range(80):
                            transport.write((n+1).to_bytes(2,'little')*1920)
                            time.sleep(max(0,begin+(n+1)*.020-time.monotonic()))
                    self.assertGreaterEqual(progress.concealed,2*960)
                    self.assertGreaterEqual(progress.inserted,2*960)
                    self.assertLessEqual(progress.inserted,2*17*960)
                def pause_reader():
                    for at,delay in suspensions:
                        if stop.wait(max(0,(epoch/1e9+at)-shared_clock_ns()/1e9)):return
                        if child.poll() is not None:return
                        child.send_signal(signal.SIGSTOP)
                        try:stop.wait(delay)
                        finally:
                            if child.poll() is None:child.send_signal(signal.SIGCONT)
                worker=threading.Thread(target=pause_reader);worker.start();i=0;last=0
                while shared_clock_ns()-epoch<seconds*1e9:
                    check();i+=1
                    # Any metadata measurement uses only emitted frame count/timestamp;
                    # the production backend never examines sample contents.
                    transport.write(i.to_bytes(2,'little')*1920)
                    if progress.frames>max(last,base):
                        last=progress.frames
                        marks.append((progress.stamp-epoch)/1e6-(last-base)/48)
                    time.sleep(max(0,epoch/1e9+i*.020-shared_clock_ns()/1e9))
                # No more input is written. The last coalesced acknowledgement
                # must still arrive with the real consumption stamp, or the credit
                # window could deadlock at the end of a burst.
                end=time.monotonic()+.24
                while progress.frames*4<progress.sent_bytes and time.monotonic()<end:
                    progress.poll();time.sleep(.003)
                self.assertEqual(progress.frames*4,progress.sent_bytes)
            except RuntimeError as error:
                result=reason_code(error,'write')
            finally:
                stop.set()
                if worker:worker.join(1)
                if child.poll() is None:
                    child.send_signal(signal.SIGCONT);child.terminate()
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=2);self.fail('reader required forced exit')
                child.stdin.close();progress.close()
            self.assertIsNotNone(child.returncode)
            self.assertLessEqual(progress.max_unacked,MAX_UNACKED)
            return result,marks

    def test_mixed_short_pauses_keep_clock_and_bounded_queue(self):
        result,ages=self.exercise([(1,.146),(2,.074),(2.22,.074),(2.44,.074)],4)
        self.assertEqual(result,'completed');self.assertGreater(len(ages),20)
        self.assertLess(max(ages),250);self.assertLess(sum(ages[-8:])/8,100)

    def test_twice_recover_from_drained_input_without_restarting(self):
        result,_=self.exercise([],0,recovered_input=True)
        self.assertEqual(result,'completed')

    def test_no_consumption_fails_closed(self):
        result,_=self.exercise([(1,.560)],3)
        self.assertIn(result,('server_read_timeout','server_media_stale'))
