"""Explicit UI-only SSH receiver setup. Import/list are inert and private.
Never enables Remote Login, accepts host keys, stores credentials or starts audio.
"""
import argparse,base64,dataclasses,getpass,hashlib,json,os,pathlib,stat,subprocess,sys,uuid
from receiver_registry import configured_receivers,receiver,AUTHORIZATION
from ssh_control import SSHAuthRequiredError,SSHHostKeyError,batch_ssh_env,classify_ssh_failure,ssh_command
from ssh_discovery import endpoint,ssh_destination,list_aliases,destination_key
from local_config import CONFIG_HOME,ASSET_ROOT,settings
from null_transport import SSH
REMOTE = r'''
import base64,hashlib,json,os,pathlib,platform,re,stat,subprocess,sys
request=json.loads(sys.stdin.buffer.read(10*1024*1024+1))
if platform.system()!='Darwin' or platform.machine()!='arm64' or sys.version_info<(3,9):raise RuntimeError('unsupported_receiver')
version=tuple(map(int,platform.mac_ver()[0].split('.')[:2]))
if version<(14,2) or os.getuid()==0:raise RuntimeError('unsupported_receiver')
if os.stat('/dev/console').st_uid!=os.getuid():raise RuntimeError('receiver_needs_logged_in_session')
hardware=subprocess.run(['/usr/sbin/ioreg','-rd1','-c','IOPlatformExpertDevice'],capture_output=True,text=True,timeout=3)
match=re.search(r'"IOPlatformUUID"\s*=\s*"([A-Fa-f0-9-]+)"',hardware.stdout)
if hardware.returncode or not match:raise RuntimeError('receiver_identity_unavailable')
identity=hashlib.sha256(('macbee-device-v1:'+match.group(1).lower()).encode()).hexdigest()
if identity in request.get('exclude_identities',[]):raise RuntimeError('duplicate_receiver_identity')
root=request.get('root')
if request['action']=='setup':
 files=request['files']
 if set(files)!={'snapclient-macbee-application','macbee-tone-probe','audio_guardian-configured-v1.py'}:raise RuntimeError('invalid_setup_files')
 suffix=hashlib.sha256(''.join(files[name]['sha256'] for name in sorted(files)).encode()).hexdigest()[:16]
 root=pathlib.Path.home()/'Library/Application Support/Macbee Receiver'/suffix
 path=pathlib.Path.home()
 for part in ('Library','Application Support','Macbee Receiver',suffix):
  path=path/part
  try:os.mkdir(path,0o700)
  except FileExistsError:pass
  info=path.lstat()
  if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid():raise RuntimeError('unsafe_receiver_directory')
 for name,record in files.items():
  data=base64.b64decode(record['data'],validate=True)
  if len(data)>4*1024*1024 or hashlib.sha256(data).hexdigest()!=record['sha256']:raise RuntimeError('invalid_setup_digest')
  target=root/name
  try:fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600 if name.endswith('.py') else 0o700)
  except FileExistsError:fd=None
  if fd is not None:
   try:
    view=memoryview(data)
    while view:view=view[os.write(fd,view):]
   finally:os.close(fd)
  info=target.lstat()
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or hashlib.sha256(target.read_bytes()).hexdigest()!=record['sha256']:raise RuntimeError('receiver_digest_mismatch')
else:
 root=pathlib.Path(root)
 for name,digest in request['digests'].items():
  p=root/name
  if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('receiver_needs_setup')
probe=subprocess.run([str(root/'macbee-tone-probe'),'--inspect'],capture_output=True,text=True,timeout=3)
if probe.returncode:raise RuntimeError('receiver_audio_inventory_failed')
value=json.loads(probe.stdout)
if value.get('sample_rate')!=48000 or value.get('input_streams')!=0:raise RuntimeError('unsupported_default_output')
print(json.dumps(dict(ready=True,root=str(root),architecture='arm64',device_sha256=identity,source_policy='no_audio_started')))
'''

def local_device_identity():
 import re
 hardware=subprocess.run(['/usr/sbin/ioreg','-rd1','-c','IOPlatformExpertDevice'],capture_output=True,text=True,timeout=3)
 match=re.search(r'"IOPlatformUUID"\s*=\s*"([A-Fa-f0-9-]+)"',hardware.stdout)
 if hardware.returncode or not match:raise RuntimeError('This Mac identity could not be verified')
 return hashlib.sha256(('macbee-device-v1:'+match.group(1).lower()).encode()).hexdigest()

def local_assets():
 if ASSET_ROOT is None:raise RuntimeError('Use the installed Macbee setup interface')
 names={'snapclient-macbee-application':ASSET_ROOT/'bin/snapclient-application',
        'macbee-tone-probe':ASSET_ROOT/'bin/macbee-tone-probe',
        'audio_guardian-configured-v1.py':ASSET_ROOT/'tools/audio_guardian.py'}
 result={}
 expected=json.loads((ASSET_ROOT/'application-client.json').read_text())['sha256']
 from speaker_transport import PROBE_HASH
 for name,path in names.items():
  if path.is_symlink() or not path.is_file():raise RuntimeError('Bundled receiver component is missing')
  data=path.read_bytes()
  if len(data)>4*1024**2:raise RuntimeError('Unexpected receiver component size')
  digest=hashlib.sha256(data).hexdigest()
  if name=='snapclient-macbee-application' and digest!=expected:raise RuntimeError('Bundled receiver binary changed')
  if name=='macbee-tone-probe' and digest!=PROBE_HASH:raise RuntimeError('Bundled receiver probe changed')
  result[name]=dict(sha256=digest,data=base64.b64encode(data).decode('ascii'))
 return result

def rpc(host,request,*,control_path=None,receiver_id='',control_paths=None):
 endpoint(host)
 import shlex
 command=ssh_command(host,control_path=control_path,receiver_id=receiver_id or None,control_paths=control_paths,remote_command='python3 -c '+shlex.quote(REMOTE))
 result=subprocess.run(command,input=json.dumps(request),capture_output=True,text=True,timeout=25,env=batch_ssh_env())
 if result.returncode:
  # Keep SSH paths/usernames and remote exception text out of diagnostic records.
  text=result.stderr
  classified=classify_ssh_failure(text,receiver_id=receiver_id)
  if classified is not None:raise classified
  for code,message in [('duplicate_receiver_identity','This Mac is already an output. No duplicate was added.'),('receiver_needs_logged_in_session','Log in to the receiving Mac before setup.'),('unsupported_receiver','The receiver needs Apple Silicon, macOS 14.2 or later and Python 3.9 or later.'),('receiver_needs_setup','This receiver needs the matching Macbee components.'),('unsupported_default_output','The receiving default output must support 48 kHz stereo.')]:
   if code in text:raise RuntimeError(message)
  raise RuntimeError('Receiver setup or verification failed. No security settings were changed.')
 value=json.loads(result.stdout)
 if not isinstance(value,dict) or value.get('ready') is not True or value.get('source_policy')!='no_audio_started':raise RuntimeError('Invalid receiver verification')
 return value

def save_config(config,home=CONFIG_HOME):
 home=pathlib.Path(home)
 if home.is_symlink():raise RuntimeError('Unsafe configuration directory')
 home.mkdir(mode=0o700,parents=True,exist_ok=True)
 if home.stat().st_uid!=os.getuid():raise RuntimeError('Configuration owner mismatch')
 path=home/'local-config.json'
 if path.is_symlink():raise RuntimeError('Configuration link refused')
 data=(json.dumps(config,indent=2)+'\n').encode()
 if len(data)>65536:raise RuntimeError('Configuration exceeds bound')
 temporary=home/('.config-'+uuid.uuid4().hex)
 fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  view=memoryview(data)
  while view:view=view[os.write(fd,view):]
 finally:os.close(fd)
 os.replace(temporary,path)

def add_receiver(host,name,config=None,home=CONFIG_HOME,*,ssh_home=None,control_path=None):
 host=endpoint(host).canonical
 config=dict(settings if config is None else config);old=configured_receivers(config)
 if any(x.ssh_host==host for x in old):raise ValueError('This SSH destination is already configured')
 aliases=list_aliases(ssh_home if ssh_home is not None else pathlib.Path.home(),default_user=getpass.getuser())['aliases']
 key=destination_key(host,aliases)
 if any(destination_key(x.ssh_host,aliases)==key for x in old):raise ValueError('This SSH destination is already configured')
 # Validate before any network or install, including the explicit authorization marker.
 candidate=dict(id='r-'+uuid.uuid4().hex[:16],name=name,ssh_host=host,remote_root='/pending',
                client_sha256='',selected=False,authorization=AUTHORIZATION)
 receiver(candidate)
 if len(old)>=15:raise ValueError('This build supports up to 15 remote outputs')
 assets=local_assets();sender_identity=local_device_identity()
 verified=rpc(host,dict(action='setup',files=assets,exclude_identities=[sender_identity]+[x.device_sha256 for x in old if x.device_sha256]),control_path=control_path)
 identity=verified.get('device_sha256','')
 if len(identity)!=64:raise RuntimeError('Receiver identity was not verified')
 if identity==sender_identity or any(x.device_sha256==identity for x in old):raise ValueError('This Mac is already an output; no duplicate was added')
 candidate.update(remote_root=verified['root'],client_sha256=assets['snapclient-macbee-application']['sha256'],probe_sha256=assets['macbee-tone-probe']['sha256'],device_sha256=identity);new=receiver(candidate)
 path=pathlib.Path(home)/'local-config.json'
 if path.exists():
  if path.is_symlink():raise RuntimeError('Configuration link refused')
  config=json.loads(path.read_text());old=configured_receivers(config)
  if any(destination_key(x.ssh_host,aliases)==key or x.device_sha256==identity for x in old):raise ValueError('This SSH destination was configured while setup was running')
 rows=[dict(dataclasses.asdict(x),authorization=AUTHORIZATION) for x in old]+[candidate]
 config['receivers']=rows;save_config(config,home)
 return dict(new.public(),availability='available')

def check_receiver(key,config=None,*,control_path=None):
 peers={x.id:x for x in configured_receivers(settings if config is None else config)}
 if key not in peers:raise ValueError('Choose a configured authorized receiver')
 peer=peers[key];assets=local_assets();digests={name:row['sha256'] for name,row in assets.items()}
 # Existing pair may have the earlier matching full-range client. Preserve its
 # explicit digest; the guardian must match this candidate exactly.
 if peer.client_sha256:digests['snapclient-macbee-application']=peer.client_sha256
 if peer.probe_sha256:digests['macbee-tone-probe']=peer.probe_sha256
 verified=rpc(peer.ssh_host,dict(action='check',root=peer.remote_root,digests=digests),control_path=control_path,receiver_id=peer.id)
 if peer.device_sha256 and verified.get('device_sha256')!=peer.device_sha256:raise RuntimeError('Receiver identity changed; verify this Mac before sharing')
 return dict(peer.public(),availability='available')

def discover_destinations(config=None,*,ssh_home=None,default_user=None):
 receivers=configured_receivers(settings if config is None else config)
 preview=list_aliases(ssh_home if ssh_home is not None else pathlib.Path.home(),default_user=getpass.getuser() if default_user is None else default_user,configured=[r.ssh_host for r in receivers])
 keys_seen=set();configured_keys=[]
 for r in receivers:
  dk=destination_key(r.ssh_host,preview['aliases'])
  if dk in keys_seen:continue
  keys_seen.add(dk)
  if dk[0]=='address':configured_keys.append(dict(kind='address',host=dk[1],port=dk[2]))
  else:configured_keys.append(dict(kind='unresolved',canonical=dk[1]))
 configured_keys.sort(key=lambda item:(item['kind'],item.get('host',item.get('canonical','')),item.get('port',0)))
 return dict(aliases=preview['aliases'],configured_keys=configured_keys,warnings=preview['warnings'])

def receiver_host_info(key,config=None):
 peers={x.id:x for x in configured_receivers(settings if config is None else config)}
 if key not in peers:raise ValueError('Choose a configured authorized receiver')
 peer=peers[key]
 return dict(id=peer.id,name=peer.name,ssh_host=peer.ssh_host,ssh_argv=endpoint(peer.ssh_host).ssh_argv())

def save_selection(ids,config=None,home=CONFIG_HOME):
 from receiver_registry import selected_outputs
 config=dict(settings if config is None else config);selected_outputs(config,ids)
 config['local_output_selected']='local' in ids
 config['receivers']=[dict(dataclasses.asdict(peer),selected=peer.id in ids,authorization=AUTHORIZATION) for peer in configured_receivers(config)]
 save_config(config,home);return dict(saved=True)

def main():
 parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group(required=True)
 mode.add_argument('--list',action='store_true');mode.add_argument('--list-ssh-aliases',action='store_true');mode.add_argument('--discover',action='store_true');mode.add_argument('--receiver-host');mode.add_argument('--check');mode.add_argument('--select');mode.add_argument('--add',action='store_true')
 parser.add_argument('--host');parser.add_argument('--name');parser.add_argument('--control-path');args=parser.parse_args()
 try:
  if args.list:result=dict(outputs=[x.public() for x in configured_receivers(settings)],local_selected=settings.get('local_output_selected',True))
  elif args.list_ssh_aliases or args.discover:result=discover_destinations()
  elif args.receiver_host:result=receiver_host_info(args.receiver_host)
  elif args.select:result=save_selection(args.select.split(','))
  elif args.check:result=check_receiver(args.check,control_path=args.control_path)
  else:
   if not args.host or not args.name:parser.error('Explicit host and name are required')
   result=add_receiver(args.host,args.name,control_path=args.control_path)
  print(json.dumps(dict(ok=True,result=result)))
 except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as error:
  payload=dict(ok=False,error=str(error)[:240])
  code=getattr(error,'code',None)
  if code in ('auth_required','host_key_unverified'):
   payload['code']=code
   if getattr(error,'receiver_id',''):payload['receiver_id']=error.receiver_id
  print(json.dumps(payload));return 1
 return 0
if __name__=='__main__':sys.exit(main())
