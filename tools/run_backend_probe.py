#!/usr/bin/env python3
"""One synthetic source, two explicitly silent /dev/null Snapcast sinks.
No captured samples or PCM recordings. Fixed 12-second input, bounded peers.
"""
from ssh_control import BASE_SSH_OPTIONS,SSH_EXECUTABLE,batch_ssh_env,ssh_command
from ssh_discovery import ssh_destination
from local_config import SSH_HOST,REMOTE_ROOT,PROBE_PATH,require_receiver,BACKEND_ROOT
import json,os,pathlib,re,select,shlex,shutil,signal,socket,struct,subprocess,time
from build_backend import guard
ROOT=BACKEND_ROOT
BIN=ROOT/'snapcast-0.35.0'/'bin'
PORT=45840
REMOTE_PORT=45841
REMOTE_BINARY=REMOTE_ROOT+'snapclient-macbee-v035'
SSH=[SSH_EXECUTABLE,*BASE_SSH_OPTIONS]

def terminate(p):
    if p.poll() is None:
        os.killpg(p.pid,signal.SIGTERM)
        try:p.wait(timeout=4)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=2)

def stats(text):
    rows=[json.loads(x) for x in re.findall(r'MACBEE_STATS (\{[^\n]+\})',text)]
    if not rows:raise RuntimeError('No instrumented sink statistics')
    result={key:sum(row[key] for row in rows) for key in rows[0]}
    if result['pattern_frames']<240000 or result['other_frames']!=0:
        raise RuntimeError('Synthetic pattern missing/corrupted: '+str(result))
    return result

def request_stop(sig,frame):
    raise KeyboardInterrupt('stop requested')

def run():
    require_receiver()
    baseline,_=guard()
    run=ROOT/('run-'+time.strftime('%Y%m%d-%H%M%S'))
    run.mkdir();(run/'state').mkdir()
    def quick_guard():
        runtime=sum(p.stat().st_blocks*512 for p in run.rglob('*'))
        if baseline+runtime>3.5*1024**3 or shutil.disk_usage(ROOT).free<8.5*1024**3:
            raise RuntimeError('Runtime capacity guard')
    # Bind check only. Never listen on 0.0.0.0 or a LAN interface.
    with socket.socket() as test:test.bind(('127.0.0.1',PORT))
    conf=run/'snapserver.conf'
    conf.write_text(f'''[server]
threads = 1
datadir = {run/'state'}
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
source = pipe:///dev/stdin?name=macbee-synthetic&mode=read
sampleformat = 48000:16:2
codec = pcm
chunk_ms = 20
buffer = 500
[logging]
sink = stdout
filter = *:info
''')
    handles=[];processes=[];remote=None
    try:
        serverlog=(run/'server.log').open('wb');handles.append(serverlog)
        server=subprocess.Popen([str(BIN/'snapserver'),'--config',str(conf)],stdin=subprocess.PIPE,
                                stdout=serverlog,stderr=subprocess.STDOUT,start_new_session=True,bufsize=0)
        processes.append(server)
        for _ in range(50):
            if server.poll() is not None:raise RuntimeError('Server exited during setup')
            try:
                with socket.create_connection(('127.0.0.1',PORT),timeout=.1):break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('Loopback server did not become ready')
        listeners=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(server.pid),'-iTCP','-sTCP:LISTEN'],capture_output=True,text=True)
        (run/'listeners.txt').write_text(listeners.stdout)
        if f'127.0.0.1:{PORT}' not in listeners.stdout or '*:' in listeners.stdout:
            raise RuntimeError('Unexpected server bind scope')
        args=['--player','file:filename=/dev/null','--mixer','none','--logsink','stdout','--logfilter','*:info']
        local_log=(run/'local-client.log').open('wb');handles.append(local_log)
        local=subprocess.Popen([str(BIN/'snapclient')]+args+['--hostID','macbee-synthetic-local',f'tcp://127.0.0.1:{PORT}'],
                               stdout=local_log,stderr=subprocess.STDOUT,start_new_session=True)
        processes.append(local)
        # SSH-side guardian owns only its one child. It stops on STOP, EOF, HUP,
        # TERM or 25 seconds, even if the controlling tool session disappears.
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
   sys.stdin.readline();break
finally:
 if p.poll() is None:
  os.killpg(p.pid,signal.SIGTERM)
  try:p.wait(timeout=4)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 print('MACBEE_REMOTE_EXIT',p.returncode,flush=True)
'''.replace('COMMAND',repr([REMOTE_BINARY]+args+['--hostID','macbee-synthetic-remote',f'tcp://127.0.0.1:{REMOTE_PORT}']))
        remote_log=(run/'remote-client.log').open('wb');handles.append(remote_log)
        remote=subprocess.Popen(ssh_command(SSH_HOST,receiver_id='remote',extra_args=['-o','ExitOnForwardFailure=yes','-R',f'127.0.0.1:{REMOTE_PORT}:127.0.0.1:{PORT}'],remote_command='python3 -u -c '+shlex.quote(remote_code)),stdin=subprocess.PIPE,
            stdout=remote_log,stderr=subprocess.STDOUT,start_new_session=True,env=batch_ssh_env())
        processes.append(remote)
        # One source only: 3 s zeros for timing warmup, 6 s known nonzero pattern,
        # 3 s zeros for drain. Payload is generated in memory and never saved.
        start=time.monotonic();frame=0
        os.set_blocking(server.stdin.fileno(),False)
        for chunk in range(600):
            if any(p.poll() is not None for p in processes):raise RuntimeError('A peer exited during the test')
            if chunk%50==0:quick_guard()
            if chunk==145:
                for name in ('local-client.log','remote-client.log'):
                    log=(run/name).read_text(errors='replace')
                    if 'Player name: file' not in log or 'parameters: filename=/dev/null' not in log:
                        raise RuntimeError('Sink identity not confirmed before nonzero pattern: '+name)
            pattern=150<=chunk<450
            payload=bytearray()
            for i in range(960):
                value=8*((frame+i)%127+1) if pattern else 0
                payload+=struct.pack('<hh',value,-value)
            view=memoryview(payload);end=time.monotonic()+2
            while view:
                if time.monotonic()>end:raise TimeoutError('Source pipe stalled')
                if select.select([],[server.stdin],[],.1)[1]:
                    try:view=view[os.write(server.stdin.fileno(),view):]
                    except BlockingIOError:pass
            frame+=960
            time.sleep(max(0,start+(chunk+1)*.02-time.monotonic()))
        server.stdin.close()
        time.sleep(.6)
    finally:
        if remote and remote.poll() is None:
            try:remote.stdin.write(b'STOP\n');remote.stdin.flush();remote.stdin.close();remote.wait(timeout=6)
            except (OSError,subprocess.TimeoutExpired):pass
        for p in reversed(processes):terminate(p)
        for handle in handles:handle.close()
    result={'input':'one generated 48000:16:2 PCM stream','expected_pattern_frames':288000,
            'local_sink':'FilePlayer -> /dev/null','remote_sink':'FilePlayer -> /dev/null',
            'local':stats((run/'local-client.log').read_text()),
            'remote':stats((run/'remote-client.log').read_text()),
            'process_returncodes':[p.returncode for p in processes],
            'not_tested':['CoreAudio output estimator','acoustic synchronization','system audio capture','video lip-sync'],
            'clock_note':'No interhost output residual is computed from null-sink counters.'}
    (run/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2));print('Results:',run);guard()

if __name__=='__main__':
    for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,request_stop)
    run()
