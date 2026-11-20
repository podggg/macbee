"""Explicit configured SSH receivers. Pure validation; no discovery or network.
No credentials, implicit trust, arbitrary command, or Remote Login modification.
"""
import dataclasses,re,posixpath
from ssh_discovery import endpoint
MAX_OUTPUTS=16
ID=re.compile(r'(?:remote|r-[a-f0-9]{16})\Z')
HASH=re.compile(r'[a-f0-9]{64}\Z')
AUTHORIZATION='existing-ssh-user-confirmed'
@dataclasses.dataclass(frozen=True)
class Receiver:
 id:str
 name:str
 ssh_host:str
 remote_root:str
 client_sha256:str
 selected:bool=True
 probe_sha256:str=''
 device_sha256:str=''
 def public(self):return dict(id=self.id,name=self.name,selected=self.selected,availability='not_checked',output='Default output')

def receiver(row):
 if not isinstance(row,dict) or set(row)-{'id','name','ssh_host','remote_root','client_sha256','selected','authorization','probe_sha256','device_sha256'}:
  raise ValueError('Receiver has unsupported fields')
 if row.get('authorization')!=AUTHORIZATION:raise ValueError('Receiver needs explicit SSH authorization')
 if not isinstance(row.get('id'),str) or not ID.fullmatch(row['id']):raise ValueError('Invalid receiver ID')
 name=row.get('name','')
 if not isinstance(name,str) or not 1<=len(name.strip())<=60 or any(ord(c)<32 for c in name):raise ValueError('Choose a short device name')
 host=row.get('ssh_host','')
 host=endpoint(host).canonical
 root=row.get('remote_root','')
 if not isinstance(root,str) or not root.startswith('/') or any(c in root for c in '\x00\r\n') or '..' in root.split('/') or posixpath.normpath(root)=='/':
  raise ValueError('Use an absolute scoped receiver directory')
 digest=row.get('client_sha256','')
 if digest and (not isinstance(digest,str) or not HASH.fullmatch(digest)):raise ValueError('Invalid receiver executable digest')
 probe=row.get('probe_sha256','')
 if probe and (not isinstance(probe,str) or not HASH.fullmatch(probe)):raise ValueError('Invalid receiver probe digest')
 identity=row.get('device_sha256','')
 if identity and (not isinstance(identity,str) or not HASH.fullmatch(identity)):raise ValueError('Invalid receiver identity')
 selected=row.get('selected',True)
 if type(selected) is not bool:raise ValueError('Invalid receiver selection')
 return Receiver(row['id'],name.strip(),host,root.rstrip('/')+'/',digest,selected,probe,identity)

def configured_receivers(config):
 if not isinstance(config,dict):raise ValueError('Configuration must be an object')
 rows=config.get('receivers')
 if rows is None:
  # Read-only migration. Existing owner-authorized pair remains usable; never
  # copies host details into a bundle or silently adds any new destination.
  host=config.get('ssh_host','');root=config.get('remote_root','')
  if not host and not root:return []
  rows=[dict(id='remote',name='Paired Mac',ssh_host=host,remote_root=root,
   client_sha256=config.get('remote_client_sha256',''),probe_sha256=config.get('remote_probe_sha256',''),selected=True,authorization=AUTHORIZATION)]
 if not isinstance(rows,list) or len(rows)>MAX_OUTPUTS-1:raise ValueError('Too many configured receivers')
 result=[receiver(row) for row in rows]
 if len({r.id for r in result})!=len(result):raise ValueError('Duplicate receiver ID')
 # Different aliases for the same host cannot be detected without an explicit
 # identity check; adding/provisioning will perform that check, never guessing.
 if len({r.ssh_host for r in result})!=len(result):raise ValueError('Duplicate configured SSH destination')
 identities=[r.device_sha256 for r in result if r.device_sha256]
 if len(set(identities))!=len(identities):raise ValueError('The same Mac is configured more than once')
 return result

def selected_outputs(config,requested):
 receivers={r.id:r for r in configured_receivers(config)}
 if not isinstance(requested,(list,tuple)) or not requested or len(requested)>MAX_OUTPUTS or len(set(requested))!=len(requested):
  raise ValueError('Choose a nonempty unique output selection')
 if any(key!='local' and key not in receivers for key in requested):raise ValueError('Output is not an authorized configured receiver')
 return {key:(None if key=='local' else receivers[key]) for key in requested}

class OutputState:
 """Per-session states; a remote failure never silently reenrolls or restarts."""
 def __init__(self,ids):
  if not ids or len(set(ids))!=len(ids):raise ValueError('Invalid selected outputs')
  self.states={key:'connecting' for key in ids};self.events=[]
 def ready(self,key):
  if self.states.get(key)!='connecting':raise ValueError('Unexpected output readiness')
  self.states[key]='connected'
 def failed(self,key):
  if key not in self.states:raise ValueError('Unknown output failure')
  if self.states[key]=='offline':return
  self.states[key]='offline';self.events.append(dict(output=key,state='offline'))
 def remaining(self):return [key for key,state in self.states.items() if state=='connected']
 def can_continue_after(self,key):
  self.failed(key)
  # A local renderer is part of the tap's exclusion identity: never simply
  # continue or restart it after losing that identity. Remote failures isolate.
  return key!='local' and bool(self.remaining())
 def drain_events(self):events=self.events;self.events=[];return events
