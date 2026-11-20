#!/usr/bin/env python3
"""Approved Stage A: one synthetic source, two bounded CoreAudio speaker clients.
No captured samples or PCM recordings. Fixed 12-second input, bounded peers.
"""
from ssh_control import BASE_SSH_OPTIONS,SSH_EXECUTABLE,batch_ssh_env,ssh_command
from ssh_discovery import ssh_destination
from local_config import SSH_HOST,REMOTE_ROOT,PROBE_PATH,require_receiver,BACKEND_ROOT
import argparse,json,os,pathlib,re,select,shlex,shutil,signal,socket,subprocess,sys,time
from build_backend import guard
from coreaudio_protocol import payload,stats
ROOT=BACKEND_ROOT
BIN=ROOT/'snapcast-0.35.0'/'bin'
PORT=45840
REMOTE_PORT=45841
REMOTE_BINARY=REMOTE_ROOT+'snapclient-macbee-coreaudio'
GUARDIAN=pathlib.Path(__file__).with_name('audio_guardian.py')
SSH=[SSH_EXECUTABLE,*BASE_SSH_OPTIONS]

def terminate(p):
    if p.poll() is None:
        os.killpg(p.pid,signal.SIGTERM)
        try:p.wait(timeout=4)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=2)

def request_stop(sig,frame):
    raise KeyboardInterrupt('stop requested')
for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,request_stop)

def run(silent=False):
    require_receiver()
    print(json.dumps({'event':'preflight','detail':'Checking scoped build files and connection.'}),flush=True)
    def preflight_timeout(sig,frame):
        raise TimeoutError('File preflight exceeded 15 seconds. No backend started; inspect access/capacity before retrying.')
    old_alarm=signal.signal(signal.SIGALRM,preflight_timeout)
    signal.setitimer(signal.ITIMER_REAL,15)
    try:baseline,_=guard()
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_alarm)
    run=ROOT/(('coreaudio-silent-' if silent else 'coreaudio-')+time.strftime('%Y%m%d-%H%M%S'))
    run.mkdir();(run/'state').mkdir()
    def quick_guard():
        used=sum(p.stat().st_blocks*512 for p in run.rglob('*'))
        if baseline+used>3.5*1024**3 or shutil.disk_usage(ROOT).free<8.5*1024**3:
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
    handles=[];processes=[];remote=None;canceled=False
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
        local_log=(run/'local-client.log').open('wb');handles.append(local_log)
        local=subprocess.Popen([sys.executable,str(GUARDIAN),'--binary',str(BIN/'snapclient'),
            '--probe',str(PROBE_PATH),'--port',str(PORT),'--host-id','macbee-stage-a-local'],
            stdin=subprocess.PIPE,stdout=local_log,stderr=subprocess.STDOUT,start_new_session=True)
        processes.append(local)
        remote_log=(run/'remote-client.log').open('wb');handles.append(remote_log)
        remote_command=['python3',REMOTE_ROOT+'audio_guardian.py',
            '--binary',REMOTE_BINARY,'--probe',REMOTE_ROOT+'macbee-tone-probe',
            '--port',str(REMOTE_PORT),'--host-id','macbee-stage-a-remote']
        remote=subprocess.Popen(ssh_command(SSH_HOST,receiver_id='remote',extra_args=['-o','ExitOnForwardFailure=yes','-R',f'127.0.0.1:{REMOTE_PORT}:127.0.0.1:{PORT}'],remote_command=shlex.join(remote_command)),stdin=subprocess.PIPE,
            stdout=remote_log,stderr=subprocess.STDOUT,start_new_session=True,env=batch_ssh_env())
        processes.append(remote)
        # One source only: 3 s zeros for timing warmup, 6 s known nonzero pattern,
        # 3 s zeros for drain. Payload is generated in memory and never saved.
        start=time.monotonic()
        os.set_blocking(server.stdin.fileno(),False)
        for chunk in range(250 if silent else 600):
            if any(p.poll() is not None for p in processes):raise RuntimeError('A peer exited during the test')
            if chunk%50==0:quick_guard()
            if chunk==145:
                for name in ('local-client.log','remote-client.log'):
                    log=(run/name).read_text(errors='replace')
                    if 'Player name: coreaudio' not in log or 'MACBEE_GUARD_READY' not in log:
                        raise RuntimeError('Sink identity not confirmed before nonzero pattern: '+name)
            view=memoryview(payload(chunk,silent));end=time.monotonic()+2
            while view:
                if time.monotonic()>end:raise TimeoutError('Source pipe stalled')
                if select.select([],[server.stdin],[],.1)[1]:
                    try:view=view[os.write(server.stdin.fileno(),view):]
                    except BlockingIOError:pass
            time.sleep(max(0,start+(chunk+1)*.02-time.monotonic()))
        server.stdin.close()
        time.sleep(.6)
    except KeyboardInterrupt:
        canceled=True
    finally:
        # Ignore repeated Stop during bounded teardown; never interrupt its cleanup.
        for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        if remote and remote.poll() is None:
            try:remote.stdin.write(b'STOP\n');remote.stdin.flush();remote.stdin.close();remote.wait(timeout=6)
            except (OSError,subprocess.TimeoutExpired):pass
        if 'local' in locals() and local.poll() is None:
            try:local.stdin.write(b'STOP\n');local.stdin.flush();local.stdin.close();local.wait(timeout=4)
            except (OSError,subprocess.TimeoutExpired):pass
        for p in reversed(processes):terminate(p)
        for handle in handles:handle.close()
    if canceled:
        result={'event':'test_canceled','process_returncodes':[p.returncode for p in processes],
                'cleanup_verified':all(p.returncode==0 for p in processes)}
        for name in ('local-client.log','remote-client.log'):
            path=run/name
            text=path.read_text(errors='replace') if path.exists() else ''
            rows=[json.loads(x) for x in re.findall(r'MACBEE_GUARD_FINAL (\{[^\n]+\})',text)]
            # Once a native client started, require its guardian's clean final report.
            if 'MACBEE_GUARD_READY' in text:
                if len(rows)!=1 or rows[0].get('error') or rows[0].get('client_exit')!=0:
                    result['cleanup_verified']=False
                else:
                    before,after=rows[0].get('before',{}),rows[0].get('after',{})
                    if any(before.get(k)!=after.get(k) for k in ('device_id','volume','mute','sample_rate')):
                        result['cleanup_verified']=False
            result[name]=rows
        (run/'result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2));print('Results:',run)
        if not result['cleanup_verified']:raise RuntimeError('Cancellation cleanup needs inspection')
        return
    if any(p.returncode != 0 for p in processes):raise RuntimeError('A test process did not exit cleanly')
    result={'input':'one generated 48000:16:2 PCM stream','expected_pulses_per_client':0 if silent else 6,
            'local_sink':'CoreAudioPlayer -> built-in speakers','remote_sink':'CoreAudioPlayer -> built-in speakers',
            'local':stats((run/'local-client.log').read_text(),silent),
            'remote':stats((run/'remote-client.log').read_text(),silent),
            'process_returncodes':[p.returncode for p in processes],
            'not_tested':['acoustic synchronization','system audio capture','video lip-sync','long-run drift'],
            'clock_note':'No acoustic or interhost timing residual is inferred from callback/pulse counts.'}
    (run/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2));print('Results:',run);guard()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--silent',action='store_true')
    parser.add_argument('--watch-parent',action='store_true');args=parser.parse_args()
    watcher=None
    if args.watch_parent:
        from parent_watch import ParentWatch
        watcher=ParentWatch(sys.stdin.fileno(),lambda:os.kill(os.getpid(),signal.SIGTERM)).start()
    try:run(silent=args.silent)
    except KeyboardInterrupt:
        print(json.dumps({'event':'test_canceled_before_backend','cleanup_verified':True}),flush=True)
    except Exception as error:
        print(json.dumps({'event':'test_failed','detail':str(error)}),flush=True)
        sys.exit(1)
    finally:
        if watcher:watcher.close()
