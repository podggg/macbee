"""Local-only paths and an existing host-verified SSH destination.
No process, network or audio operation. Machine details belong in the ignored
.local-app/local-config.json file, never in published history.
"""
import json,os,pathlib,re
from ssh_discovery import endpoint
REPO=pathlib.Path(__file__).resolve().parents[1]
CONFIG_HOME=pathlib.Path(os.environ.get('MACBEE_RUNTIME_HOME',str(REPO/'.local-app'))).expanduser()
ASSET_ROOT=pathlib.Path(os.environ['MACBEE_ASSET_ROOT']) if 'MACBEE_ASSET_ROOT' in os.environ else None
path=CONFIG_HOME/'local-config.json'
settings=json.loads(path.read_text()) if path.is_file() else {}
if not isinstance(settings,dict):raise ValueError('Local configuration must be an object')
BACKEND_ROOT=pathlib.Path(settings.get('backend_root',str(REPO/'.local-app/backend-v035'))).expanduser()
PROBE_PATH=pathlib.Path(settings.get('probe_path',str(REPO/'.local-app/relay-runtime/bin/macbee-tone-probe'))).expanduser()
SSH_HOST=settings.get('ssh_host','')
REMOTE_ROOT=settings.get('remote_root','/UNCONFIGURED_MACBEE_RUNTIME').rstrip('/')+'/'
REMOTE_CLIENT_SHA256=settings.get('remote_client_sha256','')
if REMOTE_CLIENT_SHA256 and not re.fullmatch(r'[0-9a-f]{64}',REMOTE_CLIENT_SHA256):
    raise ValueError('Invalid configured receiver binary digest')
def require_receiver():
    try: endpoint(SSH_HOST)
    except ValueError:
        raise RuntimeError('Set an existing verified ssh_host in .local-app/local-config.json')
    root=pathlib.PurePosixPath(REMOTE_ROOT)
    if not root.is_absolute() or '..' in root.parts or root.as_posix()=='/UNCONFIGURED_MACBEE_RUNTIME' or any(c in REMOTE_ROOT for c in '\x00\n\r'):
        raise RuntimeError('Set an absolute, scoped receiver remote_root in local configuration')
