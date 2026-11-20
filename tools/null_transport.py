"""Finite, loopback + existing SSH, two /dev/null Snapcast diagnostic transport.
No capture API. No arbitrary host, sink, player or file-path selection.
"""
from ssh_control import BASE_SSH_OPTIONS,SSH_EXECUTABLE,batch_ssh_env,ssh_command
from ssh_discovery import ssh_destination
from local_config import SSH_HOST,REMOTE_ROOT,PROBE_PATH,require_receiver
import json,os,pathlib,select,shlex,shutil,signal,socket,subprocess,time
from relay_runtime import ROOT,BIN,guard
PORT=45840
REMOTE_PORT=45841
REMOTE_BINARY=REMOTE_ROOT+'snapclient-macbee-v035'
SSH=[SSH_EXECUTABLE,*BASE_SSH_OPTIONS]

def terminate(p):
    if p.poll() is None:
        os.killpg(p.pid,signal.SIGTERM)
        try:p.wait(timeout=4)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=2)

class NullTransport:
    run_prefix="relay-null-"
    sinks=["FilePlayer -> /dev/null"]*2
    def __init__(self,control_path=None,control_paths=None):
        self.processes=[];self.handles=[];self.remote=None;self.server=None;self.run_dir=None
        self.baseline=0;self.cleanup_verified=False
        self.control_path=control_path;self.control_paths=dict(control_paths) if control_paths else None
    def ssh_command(self,host,*,receiver_id='remote',extra_args=(),remote_command=None):
        return ssh_command(host,control_path=self.control_path,receiver_id=receiver_id,control_paths=self.control_paths,extra_args=extra_args,remote_command=remote_command)
    def check(self):
        if any(p.poll() is not None for p in self.processes):raise RuntimeError('Transport peer exited')
        guard()
    def log(self,name):
        handle=(self.run_dir/name).open('wb');self.handles.append(handle);return handle
    def validate_configuration(self):
        require_receiver()
    def start(self):
        self.validate_configuration()
        self.baseline,_=guard(verify=True)
        self.run_dir=ROOT/(self.run_prefix+time.strftime('%Y%m%d-%H%M%S'))
        self.run_dir.mkdir();(self.run_dir/'state').mkdir()
        with socket.socket() as s:s.bind(('127.0.0.1',PORT))
        conf=self.run_dir/'snapserver.conf'
        conf.write_text(f'''[server]
threads = 1
datadir = {self.run_dir/'state'}
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
port = {PORT}
publish = false
[stream]
source = pipe:///dev/stdin?name=macbee-controlled&mode=read{"&macbee_clock=v3-slots" if getattr(self,'bounded_pacing',False) else ("&macbee_clock=v2" if getattr(self,'reader_clock',None) else '')}
sampleformat = 48000:16:2
codec = pcm
chunk_ms = 20
buffer = 500
[logging]
sink = stdout
filter = *:info
''')
        progress=getattr(self,'reader_clock',None)
        try:
            self.server=subprocess.Popen([str(BIN/'snapserver'),'--config',str(conf)],stdin=subprocess.PIPE,
                stdout=self.log('server.log'),stderr=subprocess.STDOUT,start_new_session=True,bufsize=0,
                **(progress.child_options() if progress else {}))
        finally:
            if progress:progress.child_spawned()
        self.processes.append(self.server)
        for _ in range(50):
            self.check()
            try:
                with socket.create_connection(('127.0.0.1',PORT),timeout=.1):break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('Loopback server did not become ready')
        listeners=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(self.server.pid),'-iTCP','-sTCP:LISTEN'],
                                 capture_output=True,text=True,timeout=3).stdout
        (self.run_dir/'listeners.txt').write_text(listeners)
        if f'127.0.0.1:{PORT}' not in listeners or '*:' in listeners:raise RuntimeError('Unexpected listener scope')
        self.start_clients()
        os.set_blocking(self.server.stdin.fileno(),False)
        self.zeros(3)
        self.verify_sinks()
    def start_clients(self):
        args=['--player','file:filename=/dev/null','--mixer','none','--logsink','stdout','--logfilter','*:info']
        local=subprocess.Popen([str(BIN/'snapclient')]+args+['--hostID','macbee-relay-local',f'tcp://127.0.0.1:{PORT}'],
            stdout=self.log('local-client.log'),stderr=subprocess.STDOUT,start_new_session=True)
        self.processes.append(local)
        remote_code='''import os,select,signal,subprocess,sys,time
stop=False
def end(sig,frame):
 global stop
 stop=True
for s in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(s,end)
p=subprocess.Popen(COMMAND,start_new_session=True)
try:
 deadline=time.monotonic()+25
 while not stop and time.monotonic()<deadline and p.poll() is None:
  if select.select([sys.stdin],[],[],.1)[0]:
   os.read(sys.stdin.fileno(),64);break
finally:
 if p.poll() is None:
  os.killpg(p.pid,signal.SIGTERM)
  try:p.wait(timeout=4)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 print('MACBEE_REMOTE_EXIT',p.returncode,flush=True)
'''.replace('COMMAND',repr([REMOTE_BINARY]+args+['--hostID','macbee-relay-remote',f'tcp://127.0.0.1:{REMOTE_PORT}']))
        self.remote=subprocess.Popen(self.ssh_command(SSH_HOST,receiver_id='remote',extra_args=['-o','ExitOnForwardFailure=yes','-R',
            f'127.0.0.1:{REMOTE_PORT}:127.0.0.1:{PORT}'],remote_command='python3 -u -c '+shlex.quote(remote_code)),
            stdin=subprocess.PIPE,stdout=self.log('remote-client.log'),stderr=subprocess.STDOUT,start_new_session=True,bufsize=0,env=batch_ssh_env())
        self.processes.append(self.remote)
    def verify_sinks(self):
        for name in ('local-client.log','remote-client.log'):
            text=(self.run_dir/name).read_text(errors='replace')
            if 'Player name: file' not in text or 'parameters: filename=/dev/null' not in text:
                raise RuntimeError('Null sink identity not confirmed')
    def write(self,payload):
        self.check();view=memoryview(payload);end=time.monotonic()+.5
        progress=getattr(self,'reader_clock',None)
        header_left=0
        if progress and progress.slot_numbers:
            if len(payload)!=3840:raise RuntimeError('Invalid system PCM quantum')
            header=progress.slot_header();header_left=len(header);view=memoryview(header+payload)
        while view:
            if progress:progress.poll()
            left=end-time.monotonic()
            if left<=0:
                if progress and not progress.stamp:raise RuntimeError('Server read progress unavailable')
                raise TimeoutError('PCM pipe backpressure')
            quantum=min(.02,left)
            if progress and not progress.has_room(len(view)-header_left):
                select.select([progress.rx],[],[],quantum)
                continue
            if select.select([],[self.server.stdin],[],quantum)[1]:
                try:
                    n=os.write(self.server.stdin.fileno(),view);view=view[n:]
                    header_used=min(n,header_left);header_left-=header_used
                    if progress and n>header_used:progress.wrote(n-header_used)
                except BlockingIOError:pass
    def zeros(self,seconds):
        start=time.monotonic()
        for i in range(round(seconds/.02)):
            self.write(bytes(960*4));time.sleep(max(0,start+(i+1)*.02-time.monotonic()))
    def drain(self):
        self.zeros(1);self.server.stdin.close();time.sleep(.6)
    def close(self):
        # A second signal must not interrupt owned-child cleanup.
        previous={s:signal.signal(s,signal.SIG_IGN) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
        try:
            self.stop_clients()
            for p in reversed(self.processes):terminate(p)
            for handle in self.handles:handle.close()
            codes=[p.returncode for p in self.processes]
            self.cleanup_verified=self.verify_cleanup(codes)
        finally:
            for s,handler in previous.items():signal.signal(s,handler)
    def stop_clients(self):
        if self.remote and self.remote.poll() is None:
            try:
                self.remote.stdin.write(b'STOP\n');self.remote.stdin.close();self.remote.wait(timeout=6)
            except (OSError,subprocess.TimeoutExpired):pass
    def verify_cleanup(self,codes):
        remote_exit=bool(self.run_dir and (self.run_dir/'remote-client.log').exists() and
            'MACBEE_REMOTE_EXIT 0' in (self.run_dir/'remote-client.log').read_text(errors='replace'))
        return len(codes)==3 and all(c==0 for c in codes) and remote_exit
    def logs(self):
        return [(self.run_dir/name).read_text(errors='replace') for name in ('local-client.log','remote-client.log')]
