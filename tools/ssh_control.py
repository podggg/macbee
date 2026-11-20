"""Batch SSH invocation and per-receiver ControlPath validation.
Never starts a ControlMaster, never installs SSH_ASKPASS, never accepts host keys.
"""
import json
import os
import posixpath
import stat
import tempfile

from ssh_discovery import endpoint

SSH_EXECUTABLE = '/usr/bin/ssh'
SUN_PATH_LIMIT = 104
# OpenSSH muxserver_listen() binds <ControlPath>.<16 random chars> then renames.
OPENSSH_TEMP_BIND_MARGIN = 17
MAX_CONTROL_PATH_BYTES = SUN_PATH_LIMIT - 1 - OPENSSH_TEMP_BIND_MARGIN  # 86
CONTROL_PATHS_ENV = 'MACBEE_SSH_CONTROL_PATHS'
CONTROL_PATH_ENV = 'MACBEE_SSH_CONTROL_PATH'
BASE_SSH_OPTIONS = (
    '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=5',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', 'UpdateHostKeys=no',
    '-o', 'ControlMaster=no',
)
_STRIP_ENV = ('SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'MACBEE_ASKPASS_SOCKET', 'MACBEE_ASKPASS_OWNER')


class SSHAuthRequiredError(RuntimeError):
    code = 'auth_required'

    def __init__(self, message='Existing SSH authentication was refused. No credentials were saved.', *, receiver_id=''):
        super().__init__(message)
        self.receiver_id = receiver_id or ''


class SSHHostKeyError(RuntimeError):
    code = 'host_key_unverified'

    def __init__(self, message='SSH host identity is not verified. Macbee will not accept or replace host keys.', *, receiver_id=''):
        super().__init__(message)
        self.receiver_id = receiver_id or ''


def classify_ssh_failure(stderr_text, *, receiver_id=''):
    """Classify OpenSSH batch failures without echoing raw SSH paths or host details."""
    text = stderr_text or ''
    lower = text.lower()
    if ('Host key verification failed' in text
            or 'REMOTE HOST IDENTIFICATION HAS CHANGED' in text
            or 'revoked host key' in lower
            or 'offending ' in lower):
        return SSHHostKeyError(receiver_id=receiver_id)
    if ('Permission denied' in text
            or 'Authentication failed' in text
            or 'Too many authentication failures' in text):
        return SSHAuthRequiredError(receiver_id=receiver_id)
    return None


def _expanded_length(path):
    return len(path.replace('%C', '0' * 40).encode('utf-8'))


def validate_control_path(path, *, require_existing_dir=True):
    """Validate an absolute private ControlPath under macOS's 104-byte sun_path limit."""
    if not isinstance(path, str) or not path:
        raise ValueError('SSH ControlPath must be a non-empty path')
    if any(ord(c) <= 0x20 or ord(c) == 0x7f or c.isspace() for c in path):
        raise ValueError('SSH ControlPath contains disallowed characters')
    if not path.startswith('/') or path.endswith('/') or '//' in path:
        raise ValueError('SSH ControlPath must be a canonical absolute path')
    parts = path.split('/')[1:]
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('SSH ControlPath must not contain relative segments')
    parent, name = posixpath.split(path)
    if not parent or parent == '/' or not name:
        raise ValueError('SSH ControlPath must live inside a private directory')
    if '%' in parent:
        raise ValueError('SSH ControlPath directory must not contain percent tokens')
    if name.count('%C') > 1 or '%' in name.replace('%C', '', 1):
        raise ValueError('SSH ControlPath supports only a single %C token')
    if _expanded_length(path) > MAX_CONTROL_PATH_BYTES:
        raise ValueError('SSH ControlPath exceeds macOS socket path limit')
    if require_existing_dir:
        ancestors = parent.split('/')[1:-1]
        prefix = ''
        for component in ancestors:
            prefix = prefix + '/' + component
            try:
                st = os.lstat(prefix)
            except OSError as exc:
                raise ValueError('SSH ControlPath parent is unavailable') from exc
            if stat.S_ISLNK(st.st_mode) and st.st_uid != 0:
                raise ValueError('SSH ControlPath ancestor symlink is not trusted')
        try:
            dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ValueError('SSH ControlPath directory is missing or unsafe') from exc
        try:
            info = os.fstat(dir_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or (info.st_mode & 0o7777) != 0o700:
                raise ValueError('SSH ControlPath directory must be owned by current user with mode 0700')
            if '%C' not in name:
                try:
                    entry = os.lstat(name, dir_fd=dir_fd)
                except FileNotFoundError:
                    entry = None
                if entry is not None and (not stat.S_ISSOCK(entry.st_mode) or entry.st_uid != os.getuid()):
                    raise ValueError('SSH ControlPath target must be a user-owned socket')
        finally:
            os.close(dir_fd)
        real_parent = os.path.realpath(parent)
        if _expanded_length(posixpath.join(real_parent, name)) > MAX_CONTROL_PATH_BYTES:
            raise ValueError('Resolved SSH ControlPath exceeds macOS socket path limit')
    return path


def create_control_directory(prefix='macbee-ctl.'):
    """Create a private 0700 control directory short enough for %C + temp bind suffix."""
    directory = tempfile.mkdtemp(prefix=prefix, dir='/tmp')
    os.chmod(directory, 0o700)
    validate_control_path(posixpath.join(directory, '%C'), require_existing_dir=True)
    return directory


def parse_control_paths_json(raw):
    """Parse a bounded JSON mapping of receiver ID or canonical host -> ControlPath."""
    if raw is None or raw == '':
        return {}
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 32768:
        raise ValueError('SSH control-path mapping exceeds bound')
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError('SSH control-path mapping must be valid JSON') from exc
    if not isinstance(data, dict) or len(data) > 32:
        raise ValueError('SSH control-path mapping must be a bounded JSON object')
    result = {}
    for key, value in data.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 336:
            raise ValueError('Invalid SSH control-path key')
        validate_control_path(value, require_existing_dir=False)
        result[key] = value
    return result


def resolve_control_path(*, receiver_id=None, host=None, control_path=None, control_paths=None, env=None):
    """Resolve and strictly validate the ControlPath for one receiver or host, if configured."""
    source = os.environ if env is None else env
    canonical = endpoint(host).canonical if host else None
    found = None
    if control_path:
        found = control_path
    else:
        mapping = {}
        env_raw = source.get(CONTROL_PATHS_ENV)
        if env_raw:
            mapping.update(parse_control_paths_json(env_raw))
        if control_paths:
            if not isinstance(control_paths, dict) or len(control_paths) > 32:
                raise ValueError('Invalid SSH control-path mapping')
            for key, value in control_paths.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 336:
                    raise ValueError('Invalid SSH control-path key')
                validate_control_path(value, require_existing_dir=False)
                mapping[key] = value
        if receiver_id and receiver_id in mapping:
            found = mapping[receiver_id]
        elif canonical and canonical in mapping:
            found = mapping[canonical]
        elif receiver_id in (None, 'remote') and source.get(CONTROL_PATH_ENV):
            found = source.get(CONTROL_PATH_ENV)
    if found is None:
        return None
    return validate_control_path(found, require_existing_dir=True)


def batch_ssh_env(env=None):
    """Return a clean environment for batch SSH with any askpass hooks removed."""
    cleaned = dict(os.environ if env is None else env)
    for key in _STRIP_ENV:
        cleaned.pop(key, None)
    return cleaned


def ssh_command(host, *, control_path=None, receiver_id=None, control_paths=None, extra_args=(), remote_command=None, env=None):
    """Build a strict batch SSH argv with optional validated ControlPath."""
    ep = endpoint(host)
    resolved = resolve_control_path(
        receiver_id=receiver_id,
        host=ep.canonical,
        control_path=control_path,
        control_paths=control_paths,
        env=env,
    )
    command = [SSH_EXECUTABLE, *BASE_SSH_OPTIONS]
    if resolved is not None:
        command.extend(['-o', 'ControlPath=' + resolved])
    if extra_args:
        command.extend(extra_args)
    command.extend(ep.ssh_argv())
    if remote_command is not None:
        command.append(remote_command)
    return command
