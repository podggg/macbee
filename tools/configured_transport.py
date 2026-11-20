"""One shared Snapcast server, explicitly selected authorized SSH receivers.
Construction is inert. Only an explicit application Start may execute start().
No discovery, trust-on-first-use, Remote Login changes or automatic reconnect.
"""
import hashlib,json,os,re,shlex,signal,subprocess,sys,time
from application_transport import ApplicationTransport
from null_transport import NullTransport,SSH,PORT,REMOTE_PORT
from speaker_transport import final_guard,PROBE_HASH
from ssh_control import SSHAuthRequiredError,batch_ssh_env,classify_ssh_failure,resolve_control_path
from ssh_discovery import ssh_destination
from local_config import settings,ASSET_ROOT
from relay_runtime import BIN,ROOT,guard
from receiver_registry import selected_outputs,OutputState

class ConfiguredTransport(ApplicationTransport):
 run_prefix='relay-configured-'
 def __init__(self,outputs,config=None,source_policy='system',control_paths=None):
  if source_policy not in ('system','selected-app'):raise ValueError('Unsupported source mode')
  super().__init__(manual_session=True,control_paths=control_paths)
  self.destinations=selected_outputs(settings if config is None else config,outputs)
  self.outputs=list(self.destinations);self.source_policy=source_policy
  self.sinks=['CoreAudioPlayer -> '+key+' default output' for key in self.outputs]
  self.renderer_identity=None;self.peers={};self.output_state=OutputState(self.outputs);self.ready=False
  self.receiver_guardian_name='audio_guardian-configured-v1.py';self.failed_outputs=set();self.auth_required_receiver=None
 def validate_configuration(self):pass # Already checked against the immutable selected registry.
 def guardian_arguments(self):
  args=super().guardian_arguments()
  return args+(['--system-audio'] if self.source_policy=='system' else [])
 def log_path(self,key):return self.run_dir/(key+'-client.log')
 def logs(self):return [self.log_path(key).read_text(errors='replace') for key in self.outputs]
 def start_clients(self):
  probe=BIN/'macbee-tone-probe'
  if probe.is_symlink() or hashlib.sha256(probe.read_bytes()).hexdigest()!=PROBE_HASH:raise RuntimeError('Device probe changed')
  for key,receiver in self.destinations.items():
   common=['--host-id','macbee-'+key]+self.guardian_arguments()
   env=None
   if receiver is None:
    command=[sys.executable,str(self.local_guardian),'--binary',str(self.local_binary),'--probe',str(probe),'--port',str(PORT)]+common
   else:
    remote=['python3',receiver.remote_root+self.receiver_guardian_name,'--binary',receiver.remote_root+'snapclient-macbee-application',
     '--probe',receiver.remote_root+'macbee-tone-probe','--port',str(REMOTE_PORT)]+common
    command=self.ssh_command(receiver.ssh_host,receiver_id=receiver.id,extra_args=self.ssh_arguments()+['-o','ExitOnForwardFailure=yes','-R',f'127.0.0.1:{REMOTE_PORT}:127.0.0.1:{PORT}'],remote_command=shlex.join(remote))
    env=batch_ssh_env()
   child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=self.log(key+'-client.log'),stderr=subprocess.STDOUT,start_new_session=True,bufsize=0,env=env)
   self.peers[key]=child;self.processes.append(child);os.set_blocking(child.stdin.fileno(),False)
   if key=='local':self.local=child
 def verify_sinks(self):
  expected=dict(version=4,lease_seconds=3,max_seconds=0,level_policy='user-controlled',source_policy=self.source_policy,concurrency_policy='other-apps-allowed')
  for key in self.outputs:
   text=self.log_path(key).read_text(errors='replace')
   if 'Player name: coreaudio' not in text or 'MACBEE_GUARD_READY ' not in text:raise RuntimeError('Selected output is not ready')
   if [json.loads(x) for x in re.findall(r'MACBEE_GUARD_LEASE (\{[^\n]+\})',text)]!=[expected]:raise RuntimeError('Selected output policy is not verified')
   if key=='local' and self.source_policy=='system':
    rows=[json.loads(x) for x in re.findall(r'MACBEE_RENDERER_IDENTITY (\{[^\n]+\})',text)]
    if len(rows)!=1 or rows[0].get('guardian_pid')!=self.peers[key].pid or type(rows[0].get('renderer_pid')) is not int or rows[0]['renderer_pid']<=0:
     raise RuntimeError('Owned local renderer identity is missing')
    self.renderer_identity=dict(rows[0],backend_pid=os.getpid())
   self.output_state.ready(key)
  self.ready=True
 def _lost(self,key,code=None,error=None):
  self._heartbeat_pending.pop(key,None)
  self.last_failure_peer='local' if key=='local' else 'remote'
  if code is not None:self.last_failure_exit=code
  if error is not None:self.last_failure_errno=getattr(error,'errno',None)
  self.failed_outputs.add(key)
  can_continue=self.output_state.can_continue_after(key)
  child=self.peers[key]
  try:child.stdin.close()
  except (OSError,ValueError):pass
  # Closing the owned SSH control path stops renewals; a disconnected receiver
  # retains its independently verified three-second fail-closed lease.
  if not self.ready and key!='local' and code==255 and self.run_dir is not None:
   log=self.log_path(key)
   if log.is_file():
    classified=classify_ssh_failure(log.read_text(errors='replace'),receiver_id=key)
    if isinstance(classified,SSHAuthRequiredError):
     self.auth_required_receiver=key;raise classified
    if classified is not None:raise classified
  if not self.ready or not can_continue:raise RuntimeError('Transport peer exited')
  for field in ('last_failure_peer','last_failure_exit','last_failure_errno'):
   if hasattr(self,field):delattr(self,field)
 def check(self):
  clock=getattr(self,'reader_clock',None)
  if clock:clock.poll()
  if self.server is not None and self.server.poll() is not None:
   self.last_failure_peer='server';self.last_failure_exit=self.server.returncode;raise RuntimeError('Transport peer exited')
  for key,child in self.peers.items():
   if key not in self.failed_outputs:
    code=child.poll()
    if code is not None:self._lost(key,code)
  now=time.monotonic()
  targets=[(key,child) for key,child in self.peers.items() if key not in self.failed_outputs]
  self._send_heartbeats(targets,now,lambda key,error:self._lost(key,error=error),write=os.write)
  if now>=self.next_capacity_check:guard();self.next_capacity_check=now+1
 def drain_events(self):return self.output_state.drain_events()
 def start(self):
  guard(verify=True)
  manifest=(ASSET_ROOT or ROOT)/self.manifest_filename
  if manifest.is_symlink():raise RuntimeError('Renderer manifest link refused')
  data=json.loads(manifest.read_text());expected=data.get('sha256','')
  if len(expected)!=64 or data.get('format')!='48000:16:2' or data.get('stop_mode')!='wait-for-process-exit':raise RuntimeError('Renderer manifest invalid')
  if self.local_binary.is_symlink() or hashlib.sha256(self.local_binary.read_bytes()).hexdigest()!=expected:raise RuntimeError('Renderer binary changed')
  if self.local_guardian.is_symlink():raise RuntimeError('Guardian link refused')
  from receiver_setup import rpc,local_device_identity
  identities={local_device_identity()} if any(self.destinations.values()) else set()
  for receiver in self.destinations.values():
   if receiver is None:continue
   digests={'snapclient-macbee-application':receiver.client_sha256 or expected,
    self.receiver_guardian_name:hashlib.sha256(self.local_guardian.read_bytes()).hexdigest(),
    'macbee-tone-probe':receiver.probe_sha256 or PROBE_HASH}
   cp=resolve_control_path(receiver_id=receiver.id,host=receiver.ssh_host,control_paths=self.control_paths)
   try:
    verified=rpc(receiver.ssh_host,dict(action='check',root=receiver.remote_root,digests=digests),control_path=cp,receiver_id=receiver.id)
   except SSHAuthRequiredError as error:
    self.auth_required_receiver=error.receiver_id or receiver.id;raise
   identity=verified.get('device_sha256','')
   if len(identity)!=64 or identity in identities or (receiver.device_sha256 and identity!=receiver.device_sha256):
    raise RuntimeError('A selected receiver is duplicated or its identity changed')
   identities.add(identity)
  if self.source_policy=='system':
   from reader_clock import ReaderClock
   self.bounded_pacing=True
   self.reader_clock=ReaderClock(bridged=True,slot_numbers=True)
  NullTransport.start(self)
  if getattr(self,'reader_clock',None):self.reader_clock.poll(require=True)
 def close(self):
  previous={sig:signal.signal(sig,signal.SIG_IGN) for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
  try:
   # Broadcast before waiting. One unresponsive receiver cannot serially delay
   # stopping all of the remaining selected outputs.
   for key,child in self.peers.items():
    if child.poll() is None:
     pending=self._heartbeat_pending.pop(key,None)
     tail=pending[0] if pending and 0<len(pending[0])<5 else b''
     try:child.stdin.write(tail+b'STOP\n');child.stdin.close()
     except (OSError,ValueError):pass
   end=time.monotonic()+6
   while any(child.poll() is None for child in self.peers.values()) and time.monotonic()<end:time.sleep(.02)
   for child in self.peers.values():
    if child.poll() is None:
     try:os.killpg(child.pid,signal.SIGTERM)
     except ProcessLookupError:pass
   if self.server is not None and self.server.poll() is None:
    try:os.killpg(self.server.pid,signal.SIGTERM)
    except ProcessLookupError:pass
   end=time.monotonic()+2
   while any(child.poll() is None for child in self.processes) and time.monotonic()<end:time.sleep(.02)
   for child in self.processes:
    if child.poll() is None:
     try:os.killpg(child.pid,signal.SIGKILL)
     except ProcessLookupError:pass
   for handle in self.handles:handle.close()
   if not self.processes and not self.peers and self.server is None and self.run_dir is None:
    self.cleanup_verified=True;self.cleanup_error=None
   else:
    self.cleanup_verified=self.verify_cleanup([child.poll() for child in self.processes])
  finally:
   clock=getattr(self,'reader_clock',None)
   if clock:clock.close()
   for sig,handler in previous.items():signal.signal(sig,handler)
 def verify_cleanup(self,codes):
  self.cleanup_error=None
  try:
   if len(codes)!=len(self.outputs)+1 or any(code!=0 for code in codes):raise ValueError('Selected output exit is unconfirmed')
   texts=self.logs()
   if len(texts)!=len(self.outputs):raise ValueError('Missing selected output cleanup reports')
   for text in texts:
    row=final_guard(text,allow_user_levels=True)
    if row.get('reason') not in ('stop-command','control-eof','signal'):
     raise ValueError('Selected output cleanup reason is unconfirmed')
    if row.get('reason')=='signal' and row.get('signal') not in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
     raise ValueError('Selected output cleanup signal is unconfirmed')
   # A prior loss stays in failed_outputs/Offline history. Cleanup is a separate
   # claim established only by every owned exit and independent final receipt.
   return True
  except (OSError,ValueError) as error:self.cleanup_error=str(error)[:200];return False
