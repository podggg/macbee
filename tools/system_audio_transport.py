"""Existing selected pair only. No discovery, enrollment or persistent service.
Construction/import is inert. start() requires explicit system-audio approval.
"""
import hashlib,json,os,re,shlex,subprocess,sys
from application_transport import ApplicationTransport
from null_transport import NullTransport,SSH,PORT,REMOTE_PORT
from speaker_transport import final_guard,PROBE_HASH
from ssh_control import batch_ssh_env,classify_ssh_failure
from ssh_discovery import ssh_destination
from local_config import SSH_HOST,REMOTE_ROOT,REMOTE_CLIENT_SHA256,ASSET_ROOT,require_receiver
from relay_runtime import BIN,ROOT,guard

class SystemAudioTransport(ApplicationTransport):
    remote_guardian=REMOTE_ROOT+'audio_guardian-system-v1.py'
    run_prefix='relay-system-'
    def __init__(self,outputs,control_path=None,control_paths=None):
        if not isinstance(outputs,(list,tuple)) or not outputs or len(set(outputs))!=len(outputs) or not set(outputs)<={'local','remote'}:
            raise ValueError('Choose only the configured outputs')
        super().__init__(manual_session=True,control_path=control_path,control_paths=control_paths)
        self.remote_guardian=REMOTE_ROOT+'audio_guardian-system-v1.py'
        self.outputs=[x for x in ('local','remote') if x in outputs]
        self.sinks=['CoreAudioPlayer -> '+x+' default output' for x in self.outputs]
        self.renderer_identity=None
    def guardian_arguments(self):return super().guardian_arguments()+['--system-audio']
    def validate_configuration(self):
        if 'remote' in self.outputs:require_receiver()
    def logs(self):
        return [(self.run_dir/(side+'-client.log')).read_text(errors='replace') for side in self.outputs]
    def start_clients(self):
        probe=BIN/'macbee-tone-probe'
        if probe.is_symlink() or hashlib.sha256(probe.read_bytes()).hexdigest()!=PROBE_HASH:raise RuntimeError('Device probe changed')
        common=['--port',str(PORT),'--host-id','macbee-system-local']+self.guardian_arguments()
        if 'local' in self.outputs:
            self.local=subprocess.Popen([sys.executable,str(self.local_guardian),'--binary',str(self.local_binary),'--probe',str(probe)]+common,
                stdin=subprocess.PIPE,stdout=self.log('local-client.log'),stderr=subprocess.STDOUT,start_new_session=True,bufsize=0)
            self.processes.append(self.local);os.set_blocking(self.local.stdin.fileno(),False)
        if 'remote' in self.outputs:
            command=['python3',self.remote_guardian,'--binary',self.remote_binary,'--probe',REMOTE_ROOT+'macbee-tone-probe',
                '--port',str(REMOTE_PORT),'--host-id','macbee-system-remote']+self.guardian_arguments()
            self.remote=subprocess.Popen(self.ssh_command(SSH_HOST,receiver_id='remote',extra_args=self.ssh_arguments()+['-o','ExitOnForwardFailure=yes','-R',
                f'127.0.0.1:{REMOTE_PORT}:127.0.0.1:{PORT}'],remote_command=shlex.join(command)),stdin=subprocess.PIPE,
                stdout=self.log('remote-client.log'),stderr=subprocess.STDOUT,start_new_session=True,bufsize=0,env=batch_ssh_env())
            self.processes.append(self.remote);os.set_blocking(self.remote.stdin.fileno(),False)
    def verify_sinks(self):
        expected=dict(version=4,lease_seconds=3,max_seconds=0,level_policy='user-controlled',source_policy='system',concurrency_policy='other-apps-allowed')
        for side,text in zip(self.outputs,self.logs()):
            if 'Player name: coreaudio' not in text or 'MACBEE_GUARD_READY ' not in text:raise RuntimeError('Selected output is not ready')
            policies=[json.loads(x) for x in re.findall(r'MACBEE_GUARD_LEASE (\{[^\n]+\})',text)]
            if policies!=[expected]:raise RuntimeError('System renderer lease policy is not verified')
            if side=='local':
                rows=[json.loads(x) for x in re.findall(r'MACBEE_RENDERER_IDENTITY (\{[^\n]+\})',text)]
                if len(rows)!=1 or rows[0].get('guardian_pid')!=self.local.pid or type(rows[0].get('renderer_pid')) is not int or rows[0]['renderer_pid']<=0:
                    raise RuntimeError('Owned local renderer identity is missing')
                self.renderer_identity=dict(rows[0],backend_pid=os.getpid())
    def verify_cleanup(self,codes):
        self.cleanup_error=None
        try:
            if len(codes)!=1+len(self.outputs) or any(c!=0 for c in codes):raise ValueError('Selected renderer/server exit is unconfirmed')
            for text in self.logs():final_guard(text,allow_user_levels=True)
            return True
        except (OSError,ValueError) as error:self.cleanup_error=str(error)[:200];return False
    def start(self):
        self.validate_configuration();guard(verify=True)
        manifest=(ASSET_ROOT or ROOT)/self.manifest_filename
        if manifest.is_symlink():raise RuntimeError('Renderer manifest link refused')
        data=json.loads(manifest.read_text());expected=data.get('sha256','')
        if len(expected)!=64 or data.get('format')!='48000:16:2' or data.get('stop_mode')!='wait-for-process-exit':raise RuntimeError('Renderer manifest invalid')
        if self.local_binary.is_symlink() or hashlib.sha256(self.local_binary.read_bytes()).hexdigest()!=expected:raise RuntimeError('Renderer binary changed')
        if self.local_guardian.is_symlink():raise RuntimeError('Guardian link refused')
        if 'remote' in self.outputs:
            for path,digest in ((self.remote_binary,REMOTE_CLIENT_SHA256 or expected),(self.remote_guardian,hashlib.sha256(self.local_guardian.read_bytes()).hexdigest())):
                check=subprocess.run(self.ssh_command(SSH_HOST,receiver_id='remote',remote_command='shasum -a 256 '+shlex.quote(path)),capture_output=True,text=True,timeout=7,env=batch_ssh_env())
                if check.returncode:
                    classified=classify_ssh_failure(check.stderr,receiver_id='remote')
                    if classified is not None:raise classified
                if check.returncode or check.stdout.split()[:1]!=[digest]:raise RuntimeError('Configured receiver needs the matching system-audio helper')
        NullTransport.start(self)
