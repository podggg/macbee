"""Prepared C3 sink selection. Never selected by the frozen C2 app.
Reuses the same private server, PCM pipe, loopback/SSH and bounded cleanup.
It can play audio: do not execute without the exact controlled-speaker approval.
"""
from ssh_control import batch_ssh_env
from ssh_discovery import ssh_destination
from local_config import SSH_HOST,REMOTE_ROOT,PROBE_PATH,require_receiver
from audio_guardian import changed_setting
import hashlib,json,pathlib,re,shlex,subprocess,sys
from null_transport import NullTransport,BIN,ROOT,PORT,REMOTE_PORT,SSH

PROBE_HASH='8c5d81e619757e74e5d3406647e1603966563542171ad6cb74ecae874ada35e8'
GUARDIAN=pathlib.Path(__file__).with_name('audio_guardian.py')

def final_guard(text,allow_user_levels=False):
    rows=[json.loads(x) for x in re.findall(r'MACBEE_GUARD_FINAL (\{[^\n]+\})',text)]
    if len(rows)!=1:raise ValueError('Missing or repeated speaker cleanup report')
    row=rows[0]
    if row.get('error'):
        detail=str(row['error']).replace('\n',' ')[:160]
        raise ValueError('Speaker guardian failed: '+detail)
    if row.get('client_exit')!=0 or row.get('reason') in ('20-second-watchdog','lease-expired','session-watchdog','renderer-exit','guardian-end','invalid-command'):
        raise ValueError('Speaker guardian failed')
    before,after=row.get('before',{}),row.get('after',{})
    changed=changed_setting(before,after,allow_user_levels)
    if changed:raise ValueError('Speaker setting changed: '+changed)
    return row

class SpeakerTransport(NullTransport):
    local_binary=BIN/'snapclient'
    remote_binary=REMOTE_ROOT+'snapclient-macbee-coreaudio'
    host_prefix='macbee-c3'
    run_prefix='relay-speaker-'
    sinks=['CoreAudioPlayer -> guarded built-in speakers']*2
    local_guardian=GUARDIAN
    remote_guardian=REMOTE_ROOT+'audio_guardian.py'
    allow_user_levels=False
    def guardian_arguments(self):return []
    def ssh_arguments(self):return []
    def start_clients(self):
        probe=BIN/'macbee-tone-probe'
        if probe.is_symlink() or hashlib.sha256(probe.read_bytes()).hexdigest()!=PROBE_HASH:
            raise RuntimeError('Approved readonly inventory helper missing or changed')
        self.local=subprocess.Popen([sys.executable,str(self.local_guardian),'--binary',str(self.local_binary),
            '--probe',str(probe),'--port',str(PORT),'--host-id',self.host_prefix+'-local']+self.guardian_arguments(),
            stdin=subprocess.PIPE,stdout=self.log('local-client.log'),stderr=subprocess.STDOUT,
            start_new_session=True,bufsize=0)
        self.processes.append(self.local)
        command=['python3',self.remote_guardian,'--binary',self.remote_binary,
            '--probe',REMOTE_ROOT+'macbee-tone-probe','--port',str(REMOTE_PORT),'--host-id',self.host_prefix+'-remote']+self.guardian_arguments()
        self.remote=subprocess.Popen(self.ssh_command(SSH_HOST,receiver_id='remote',extra_args=self.ssh_arguments()+['-o','ExitOnForwardFailure=yes','-R',
            f'127.0.0.1:{REMOTE_PORT}:127.0.0.1:{PORT}'],remote_command=shlex.join(command)),
            stdin=subprocess.PIPE,stdout=self.log('remote-client.log'),stderr=subprocess.STDOUT,
            start_new_session=True,bufsize=0,env=batch_ssh_env())
        self.processes.append(self.remote)
    def verify_sinks(self):
        for text in self.logs():
            if 'Player name: coreaudio' not in text or 'MACBEE_GUARD_READY ' not in text:
                raise RuntimeError('Guarded speaker sink did not become ready')
    def stop_clients(self):
        # Broadcast first: a slow/disconnected remote must not delay local Stop.
        children=[child for child in (self.remote,getattr(self,'local',None)) if child]
        for child in children:
            if child.poll() is None:
                try:child.stdin.write(b'STOP\n');child.stdin.close()
                except OSError:pass
        for child in children:
            if child.poll() is None:
                try:child.wait(timeout=6)
                except subprocess.TimeoutExpired:pass
    def verify_cleanup(self,codes):
        self.cleanup_error=None
        if len(codes)!=3:
            self.cleanup_error='Owned renderer set is incomplete';return False
        try:
            texts=self.logs()
            if len(texts)!=2:raise ValueError('Missing speaker cleanup reports')
            for text in texts:final_guard(text,allow_user_levels=self.allow_user_levels)
            if any(c!=0 for c in codes):
                self.cleanup_error='Owned renderer or server exited unsuccessfully';return False
            return True
        except (OSError,ValueError) as error:
            self.cleanup_error=str(error)[:200];return False
