"""Prepared selected-app transport; no IO on import/construction.
Fails before starting any backend until a distinct full-range client is staged.
The frozen low-amplitude C3 client is never silently reused or overwritten.
"""
from ssh_control import batch_ssh_env,classify_ssh_failure
from ssh_discovery import ssh_destination
from local_config import SSH_HOST,REMOTE_ROOT,PROBE_PATH,require_receiver,ASSET_ROOT,REMOTE_CLIENT_SHA256
import errno,hashlib,json,os,re,shlex,subprocess,time
from speaker_transport import SpeakerTransport,REMOTE_ROOT
from relay_runtime import BIN,ROOT,guard
from application_protocol import RENDERER_LEASE_SECONDS,RENDERER_MAX_SECONDS
class ApplicationTransport(SpeakerTransport):
    manifest_filename='application-client.json'
    run_prefix='relay-application-'
    sinks=['CoreAudioPlayer -> selected-app speakers']*2
    local_binary=BIN/'snapclient-application'
    remote_binary=REMOTE_ROOT+'snapclient-macbee-application'
    host_prefix='macbee-application'
    remote_guardian=REMOTE_ROOT+'audio_guardian-application-v3.py'
    def __init__(self,manual_session=False,control_path=None,control_paths=None):
        self.manual_session=manual_session;self.allow_user_levels=manual_session
        if manual_session:self.remote_guardian=REMOTE_ROOT+'audio_guardian-application-v4.py'
        super().__init__(control_path=control_path,control_paths=control_paths);self.next_heartbeat=0;self.next_capacity_check=0
        self._heartbeat_pending={};self._heartbeat_timeout=2.0
    def ssh_arguments(self):
        # Per-session liveness only, never writes SSH or network configuration.
        return ['-o','ServerAliveInterval=1','-o','ServerAliveCountMax=3']
    def guardian_arguments(self):
        arguments=['--lease-seconds',str(RENDERER_LEASE_SECONDS),'--max-seconds',str(0 if self.manual_session else RENDERER_MAX_SECONDS)]
        if self.manual_session:arguments+=['--manual-session','--allow-user-levels']
        session=os.environ.get('MACBEE_DIAGNOSTIC_SESSION','')
        if re.fullmatch(r'[0-9a-fA-F-]{36}',session):arguments+=['--diagnostic-session',session]
        return arguments
    def start_clients(self):
        super().start_clients()
        for child in (self.local,self.remote):os.set_blocking(child.stdin.fileno(),False)
    def _send_heartbeats(self,targets,now,on_error,write=os.write):
        due=now>=self.next_heartbeat
        if due:self.next_heartbeat=now+.5
        for key,child in targets:
            state=self._heartbeat_pending.get(key)
            if due:
                if state is None:
                    state=[b'PING\n',None]
                    self._heartbeat_pending[key]=state
                elif state[1] is None:
                    state[1]=now
            if state is None:continue
            try:
                written=write(child.stdin.fileno(),state[0])
            except (OSError,ValueError) as error:
                transient=isinstance(error,BlockingIOError) or getattr(error,'errno',None) in (errno.EAGAIN,errno.EWOULDBLOCK,errno.EINTR)
                if transient:
                    if state[1] is None:state[1]=now
                    if now-state[1]<self._heartbeat_timeout:continue
                self._heartbeat_pending.pop(key,None)
                on_error(key,error)
                continue
            if written<=0:
                if state[1] is None:state[1]=now
                if now-state[1]>=self._heartbeat_timeout:
                    self._heartbeat_pending.pop(key,None)
                    on_error(key,OSError('Incomplete renderer heartbeat'))
                continue
            state[0]=state[0][written:]
            if not state[0]:
                self._heartbeat_pending.pop(key,None)
            else:
                if state[1] is None:state[1]=now
                if now-state[1]>=self._heartbeat_timeout:
                    self._heartbeat_pending.pop(key,None)
                    on_error(key,OSError('Incomplete renderer heartbeat'))
    def _heartbeat_error(self,key,error):
        self.last_failure_peer=key
        self.last_failure_errno=getattr(error,'errno',None)
        raise RuntimeError('Renderer heartbeat failed') from error
    def check(self):
        for child in self.processes:
            code=child.poll()
            if code is not None:
                self.last_failure_peer='server' if child is self.server else ('remote' if child is self.remote else 'local')
                self.last_failure_exit=code
                raise RuntimeError('Transport peer exited')
        now=time.monotonic()
        targets=[(key,child) for key,child in (('local',getattr(self,'local',None)),('remote',self.remote)) if child]
        self._send_heartbeats(targets,now,self._heartbeat_error,write=os.write)
        if now>=self.next_capacity_check:
            guard();self.next_capacity_check=now+1
    def stop_clients(self):
        children=[(key,child) for key,child in (('remote',self.remote),('local',getattr(self,'local',None))) if child]
        for key,child in children:
            if child.poll() is None:
                pending=self._heartbeat_pending.pop(key,None)
                tail=pending[0] if pending and 0<len(pending[0])<5 else b''
                try:child.stdin.write(tail+b'STOP\n');child.stdin.close()
                except OSError:pass
        for _,child in children:
            if child.poll() is None:
                try:child.wait(timeout=6)
                except subprocess.TimeoutExpired:pass
    def verify_sinks(self):
        super().verify_sinks()
        expected=dict(version=3,lease_seconds=RENDERER_LEASE_SECONDS,max_seconds=0 if self.manual_session else RENDERER_MAX_SECONDS,
                      level_policy='user-controlled' if self.allow_user_levels else 'fixed')
        if self.manual_session:expected.update(version=4,source_policy='selected-app',concurrency_policy='other-apps-allowed')
        for text in self.logs():
            rows=[json.loads(x) for x in re.findall(r'MACBEE_GUARD_LEASE (\{[^\n]+\})',text)]
            if rows!=[expected]:raise RuntimeError('Selected-app renderer lease is not confirmed')
    def start(self):
        require_receiver()
        guard(verify=True)
        manifest=(ASSET_ROOT or ROOT)/self.manifest_filename
        if not manifest.is_file() or manifest.is_symlink():
            raise RuntimeError('Full-range client is not staged; ordinary sharing remains unavailable')
        data=json.loads(manifest.read_text())
        expected=data.get('sha256','')
        if len(expected)!=64 or data.get('stop_mode')!='wait-for-process-exit' or data.get('format')!='48000:16:2':
            raise RuntimeError('Unsupported selected-app renderer manifest')
        if self.local_binary.is_symlink() or not self.local_binary.is_file() or hashlib.sha256(self.local_binary.read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Full-range renderer identity mismatch')
        # Remote identity must match before the shared server/capture starts.
        check=subprocess.run(self.ssh_command(SSH_HOST,receiver_id='remote',remote_command='shasum -a 256 '+shlex.quote(self.remote_binary)),capture_output=True,text=True,timeout=7,env=batch_ssh_env())
        remote_expected=REMOTE_CLIENT_SHA256 or expected
        if check.returncode:
            classified=classify_ssh_failure(check.stderr,receiver_id='remote')
            if classified is not None:raise classified
        if check.returncode or check.stdout.split()[:1]!=[remote_expected]:
            raise RuntimeError('Selected-app renderer is not staged on the receiver')
        if self.local_guardian.is_symlink() or not self.local_guardian.is_file():
            raise RuntimeError('Selected-app guardian source is unavailable')
        guardian_hash=hashlib.sha256(self.local_guardian.read_bytes()).hexdigest()
        check=subprocess.run(self.ssh_command(SSH_HOST,receiver_id='remote',remote_command='shasum -a 256 '+shlex.quote(self.remote_guardian)),capture_output=True,text=True,timeout=7,env=batch_ssh_env())
        if check.returncode:
            classified=classify_ssh_failure(check.stderr,receiver_id='remote')
            if classified is not None:raise classified
        if check.returncode or check.stdout.split()[:1]!=[guardian_hash]:
            raise RuntimeError('Selected-app renewable guardian is not staged on the receiver')
        super().start()
