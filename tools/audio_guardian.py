#!/usr/bin/env python3
"""Speaker guardian; manual sessions require a short renewable lease. No capture."""
import argparse,json,math,os,re,select,signal,subprocess,sys,time

class GuardianLease:
 """Bounded command parser and monotonic lease; no audio/process/network IO."""
 def __init__(self,now,seconds=0,max_seconds=20):
  if max_seconds==0 and seconds!=3:raise ValueError('No total cap requires a three-second renewable lease')
  self.seconds=seconds;self.deadline=now+seconds;self.maximum=now+max_seconds if max_seconds else None;self.pending=b''
 def expired(self,now):
  if self.maximum is not None and now>=self.maximum:return 'session-watchdog' if self.seconds else '20-second-watchdog'
  if self.seconds and now>=self.deadline:return 'lease-expired'
 def feed(self,data,now):
  expired=self.expired(now)
  if expired:return expired # An already expired lease cannot be revived.
  if not data:return 'control-eof'
  self.pending+=data
  while b'\n' in self.pending:
   line,self.pending=self.pending.split(b'\n',1)
   if line==b'PING' and self.seconds:self.deadline=now+self.seconds
   else:return 'stop-command' if line==b'STOP' else 'invalid-command'
  if len(self.pending)>64:return 'invalid-command'

DEFINITE_PROBE_ERRORS=frozenset({
 'default_not_builtin',
 'output_device_has_input_stream',
 'expected_one_output_stream',
 'unsupported_format',
})

class TransientInspectError(RuntimeError):
 pass

def validate_volume(value,allow_user_levels=False):
 if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value>1 or value<0 or (not allow_user_levels and value==0):
  raise RuntimeError('speaker safety preflight failed: volume')

def changed_setting(before,after,allow_user_levels=False):
 for key in ('device_id','sample_rate','input_streams','volume','mute'):
  if key not in before or key not in after:return key
  if key=='device_id' and ('device_uid' in before or 'device_uid' in after):
   buid,auid=before.get('device_uid'),after.get('device_uid')
   if not isinstance(buid,str) or not buid or not isinstance(auid,str) or not auid or buid!=auid:return 'device_uid'
   continue
  if key=='volume' and allow_user_levels:
   try:validate_volume(before[key],True);validate_volume(after[key],True)
   except RuntimeError:return key
  elif key=='mute' and allow_user_levels:
   if isinstance(before[key],bool) or isinstance(after[key],bool) or before[key] not in (0,1) or after[key] not in (0,1):return key
  elif before[key]!=after[key]:return key

def main():
 parser=argparse.ArgumentParser()
 for field in ('binary','probe','port','host_id'):parser.add_argument('--'+field.replace('_','-'),required=True)
 parser.add_argument('--lease-seconds',type=int,choices=[3],default=0)
 parser.add_argument('--max-seconds',type=int,default=20)
 parser.add_argument('--manual-session',action='store_true')
 parser.add_argument('--allow-user-levels',action='store_true')
 parser.add_argument('--system-audio',action='store_true')
 parser.add_argument('--diagnostic-session',default='')
 args=parser.parse_args()
 if args.diagnostic_session and not re.fullmatch(r'[0-9a-fA-F-]{36}',args.diagnostic_session):parser.error('Invalid diagnostic session key')
 if args.manual_session:
  if args.lease_seconds!=3 or args.max_seconds!=0:parser.error('Manual sessions require the three-second lease and no total cap')
 elif not (20<=args.max_seconds<=1830) or (not args.lease_seconds and args.max_seconds!=20):
  parser.error('Extended sessions require the three-second renewable lease')
 if args.allow_user_levels and not args.manual_session:parser.error('User-controlled levels require explicit manual-session policy')
 if args.system_audio and not (args.manual_session and args.allow_user_levels):parser.error('System source requires explicit manual policy')
 stop=False;reason=None;signal_number=None;started=time.monotonic()
 def end(sig,frame):
  nonlocal stop,reason,signal_number
  stop=True
  if reason is None:reason='signal';signal_number=sig
 for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(sig,end)
 def inspect():
  try:r=subprocess.run([args.probe,'--inspect'],capture_output=True,text=True,timeout=1.0)
  except subprocess.TimeoutExpired as e:raise TransientInspectError('speaker inventory timed out') from e
  except OSError as e:raise TransientInspectError('speaker inventory failed') from e
  if r.returncode:
   detail=None
   try:
    err=json.loads(r.stdout or '')
    if isinstance(err,dict) and err.get('event')=='error':
     token=err.get('reason')
     if isinstance(token,str) and re.fullmatch(r'[a-z0-9_]{1,48}',token):detail=token
   except (ValueError,TypeError):pass
   msg='speaker inventory failed: '+detail if detail else 'speaker inventory failed'
   if detail in DEFINITE_PROBE_ERRORS:raise RuntimeError(msg)
   raise TransientInspectError(msg)
  try:value=json.loads(r.stdout)
  except (ValueError,TypeError) as e:raise TransientInspectError('speaker inventory unreadable') from e
  if not isinstance(value,dict):raise TransientInspectError('speaker inventory unreadable')
  pids=set(map(int,re.findall(r'Active output PID: (\d+)',r.stderr or '')))
  try:
   validate_volume(value['volume'],args.allow_user_levels)
   if 'device_uid' in value and (not isinstance(value['device_uid'],str) or not value['device_uid']):
    raise RuntimeError('speaker safety preflight failed: device_uid')
   mute_invalid=(isinstance(value['mute'],bool) or value['mute'] not in (0,1)) if args.allow_user_levels else (isinstance(value['mute'],bool) or value['mute']!=0)
   other_invalid=isinstance(value['active_other_outputs'],bool) or not isinstance(value['active_other_outputs'],int) or (not args.manual_session and value['active_other_outputs']<0)
   input_invalid=isinstance(value['input_streams'],bool) or value['input_streams']!=0
   rate_invalid=isinstance(value['sample_rate'],bool) or value['sample_rate']!=48000
   if other_invalid or mute_invalid or input_invalid or rate_invalid:
    raise RuntimeError('speaker safety preflight failed')
  except (KeyError,TypeError) as e:raise RuntimeError('speaker safety preflight failed') from e
  return value,pids
 before,allowed=inspect()
 p=subprocess.Popen([args.binary,'--player','coreaudio','--mixer','none','--logsink','stdout','--logfilter','*:info',
  '--hostID',args.host_id,'tcp://127.0.0.1:'+args.port],start_new_session=True)
 allowed.add(p.pid)
 after=None;error=None
 try:
  print('MACBEE_GUARD_READY '+json.dumps(before),flush=True)
  if args.lease_seconds:
   policy=dict(version=3,lease_seconds=args.lease_seconds,max_seconds=args.max_seconds,level_policy='user-controlled' if args.allow_user_levels else 'fixed')
   if args.manual_session:policy.update(version=4,source_policy='system' if args.system_audio else 'selected-app',concurrency_policy='other-apps-allowed')
   print('MACBEE_GUARD_LEASE '+json.dumps(policy),flush=True)
  if args.system_audio:print('MACBEE_RENDERER_IDENTITY '+json.dumps(dict(guardian_pid=os.getpid(),renderer_pid=p.pid)),flush=True)
  lease=GuardianLease(time.monotonic(),args.lease_seconds,args.max_seconds);check=0;glitches=0
  while not stop and p.poll() is None:
   expired=lease.expired(time.monotonic())
   if expired:reason=expired;break
   if select.select([sys.stdin],[],[],.05)[0]:
    command=lease.feed(os.read(sys.stdin.fileno(),64),time.monotonic())
    if command:reason=command;break
   if time.monotonic()>check:
    try:current,pids=inspect();glitches=0;check=time.monotonic()+.5
    except TransientInspectError:
     glitches+=1
     if glitches>=2:raise
     check=time.monotonic()+.25;continue
    changed=changed_setting(before,current,args.allow_user_levels)
    if changed:raise RuntimeError('speaker setting changed: '+changed)
    if not args.manual_session and not pids.issubset(allowed):raise RuntimeError('new unrelated audio output started')
 except BaseException as e:error=str(e) or type(e).__name__;reason=reason or 'guardian-error'
 finally:
  # A renderer that ends by itself is not an incoming Stop or control EOF.
  reason=reason or ('renderer-exit' if p.poll() is not None else 'guardian-end')
  if p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=2)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=2)
  try:
   try:after,_=inspect()
   except TransientInspectError:
    if error is not None:raise
    after,_=inspect()
  except BaseException as e:error=error or str(e) or type(e).__name__
  print('MACBEE_GUARD_FINAL '+json.dumps({'before':before,'after':after,'client_exit':p.returncode,'reason':reason,'session':args.diagnostic_session or 'unlinked','signal':signal_number,'elapsed':time.monotonic()-started,'error':error}),flush=True)
  if error or p.returncode:sys.exit(1)

if __name__=='__main__':main()
