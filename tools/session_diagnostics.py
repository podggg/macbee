"""Bounded local numeric stop evidence. Never accepts PCM or free-form errors.
No IO on import; writes only two fixed 8 KiB snapshots in an owned run directory.
"""
import json,math,os,re,stat

ERROR_CODES={
 'Server refused stale audio before distribution':'server_media_stale',
 'Server read progress expired':'server_read_timeout',
 'Invalid server read progress':'server_read_invalid',
 'Server read progress unavailable':'server_read_unavailable',
 'System source health lease expired':'source_health_timeout',
 'System source preparation timed out':'source_prepare_timeout',
 'Repeated server pacing interruptions':'server_pacing_recovery_limit',
 'Repeated sender pacing interruptions':'sender_pacing_recovery_limit',
 'System output pacing stalled':'output_pacing_stall',
 'System source backlog exceeded its bound':'source_backlog',
 'PCM pipe backpressure':'pcm_backpressure',
 'Transport peer exited':'transport_peer_exit',
 'Renderer heartbeat failed':'renderer_heartbeat_write',
 'Incomplete renderer heartbeat':'renderer_heartbeat_partial',
 '16 MiB runtime / 1 GiB free-space guard':'runtime_capacity',
 'Truncated system source packet':'truncated_source_packet',
 'Existing SSH authentication was refused. No credentials were saved.':'auth_required',
 'SSH host identity is not verified. Macbee will not accept or replace host keys.':'host_key_unverified',
}
CODES=set(ERROR_CODES.values())|{'source_end','source_eof','signal_term','signal_hup','signal_int','interrupt',
 'transport_check_failed','source_read_failed','protocol_failed','pcm_write_failed','startup_failed',
 'backend_failed','selected_source_timeout','cleanup_failed','cleanup_unconfirmed','auth_required','host_key_unverified'}
class RelaySignal(KeyboardInterrupt):
 def __init__(self,number):self.number=number

def reason_code(error,phase):
 if isinstance(error,RelaySignal):return {15:'signal_term',1:'signal_hup',2:'signal_int'}.get(error.number,'interrupt')
 if isinstance(error,KeyboardInterrupt):return 'interrupt'
 code=getattr(error,'code',None)
 if code in CODES:return code
 return ERROR_CODES.get(str(error),{'check':'transport_check_failed','read':'source_read_failed',
  'decode':'protocol_failed','write':'pcm_write_failed','start':'startup_failed'}.get(phase,'backend_failed'))

class StopTrace:
 def __init__(self,clock,session_id=''):
  self.clock=clock;self.started=clock();self.phase='start';self.first=None
  self.session_id=session_id if re.fullmatch(r'[0-9a-fA-F-]{36}',session_id or '') else 'unlinked'
  self.metrics={};self.first_snapshot=None;self.transport={};self.output_losses=[]
 def note_output_loss(self,update):
  key=update.get('output')
  if update.get('state')!='offline' or not isinstance(key,str) or not re.fullmatch(r'local|remote|r-[a-f0-9]{16}',key):return
  if len(self.output_losses)>=16 or any(row['output']==key for row in self.output_losses):return
  self.output_losses.append(dict(output=key,uptime=self.clock(),elapsed=max(0,self.clock()-self.started)))
 def stage_time(self,stage,start,finish):
  # Fixed keys and numeric elapsed times only; stamps are never persisted per packet.
  if stage not in ('check','read','decode','decode_cpu','write'):raise ValueError('Unknown diagnostic stage')
  elapsed=finish-start
  if not math.isfinite(elapsed) or elapsed<0:return
  self.metrics['last_'+stage+'_s']=elapsed
  key='max_'+stage+'_s';self.metrics[key]=max(elapsed,self.metrics.get(key,0.0))
 def catchup_write(self):
  self.metrics['catchup_writes']=min(2**31-1,1+self.metrics.get('catchup_writes',0))
 def mark(self,name,value=None):
  if name not in {'last_loop','last_health','last_pcm','last_output','max_loop_gap','pacing_late','pending_bytes','pacing_recoveries','discarded_source_frames'}:raise ValueError('Unknown diagnostic metric')
  value=self.clock() if value is None else value
  if not isinstance(value,(int,float)) or not math.isfinite(value):return
  self.metrics[name]=value
 def note_transport(self,session,error=None):
  progress=getattr(session,'reader_clock',None)
  if progress:
   for key,value in progress.diagnostics().items():
    if key in ('reader_inserted_frames','reader_concealed_frames','reader_consumed_frames','reader_submitted_frames','reader_max_unacked_frames','reader_last_report_age_ms') and type(value) is int and value>=0:
     self.metrics[key]=value
  peer=getattr(session,'last_failure_peer',None)
  if peer in ('server','local','remote'):self.transport['peer']=peer
  for key,value in (('exit',getattr(session,'last_failure_exit',None)),('errno',getattr(session,'last_failure_errno',getattr(error,'errno',None)))):
   if isinstance(value,int) and -255<=value<=255:self.transport[key]=value
 def cause(self,code):
  if self.first is None:
   self.first=code if code in CODES else 'backend_failed';self.first_snapshot=self.snapshot()
 def snapshot(self):
  return dict(schema=1,session=self.session_id,phase=self.phase,uptime=self.clock(),elapsed=max(0,self.clock()-self.started),
              first_cause=self.first,metrics=dict(self.metrics),transport=dict(self.transport),
              output_losses=[dict(row) for row in self.output_losses])
 def save(self,directory,final=False):
  if directory is None:return False
  row=self.snapshot() if final else self.first_snapshot
  if row is None:return False
  data=(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode()
  if len(data)>8192:return False
  fd=None;out=None
  name='backend-final.json' if final else 'backend-first-stop.json'
  try:
   fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   info=os.fstat(fd)
   if info.st_uid!=os.getuid() or not stat.S_ISDIR(info.st_mode):return False
   out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
   view=memoryview(data)
   while view:view=view[os.write(out,view):]
   return True
  except (OSError,ValueError):return False # Diagnostics never change the audio lifecycle.
  finally:
   if out is not None:os.close(out)
   if fd is not None:os.close(fd)
