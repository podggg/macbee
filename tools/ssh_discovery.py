"""Read-only SSH destination previews. Never execute ssh, expand commands, or connect."""
from dataclasses import dataclass
from fnmatch import fnmatchcase
from glob import iglob
from ipaddress import IPv6Address
import os
from pathlib import Path
import re
import shlex
import stat

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}\Z', re.ASCII)
USER = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z', re.ASCII)
PORT = re.compile(r'[0-9]{1,5}\Z', re.ASCII)
INVALID = 'Enter an SSH alias or [user@]host[:port]; bracket IPv6 addresses.'
MAX_DEPTH, MAX_FILES, MAX_BYTES, MAX_ALIASES = 8, 128, 1024 * 1024, 1024


@dataclass(frozen=True)
class Endpoint:
    host: str
    user: str = ''
    port: int = 0

    @property
    def canonical(self):
        name = ('[' + self.host + ']') if ':' in self.host else self.host
        return (self.user + '@' if self.user else '') + name + (':' + str(self.port) if self.port else '')

    def ssh_argv(self):
        # Use '-p' separately. Raw user input must never be an SSH option.
        target = (self.user + '@' if self.user else '') + self.host
        return (['-p', str(self.port)] if self.port else []) + ['--', target]


def endpoint(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 336 or not value.isascii():
        raise ValueError(INVALID)
    user, at, rest = value.rpartition('@')
    if at and not USER.fullmatch(user):
        raise ValueError(INVALID)
    if rest.startswith('['):
        match = re.fullmatch(r'\[([A-Fa-f0-9:.]+)\](?::([0-9]{1,5}))?', rest)
        if not match:
            raise ValueError(INVALID)
        try:
            host = str(IPv6Address(match[1]))
        except ValueError:
            raise ValueError(INVALID) from None
        raw_port = match[2]
    else:
        host, sep, raw_port = rest.partition(':')
        if not NAME.fullmatch(host) or '..' in host or (sep and not raw_port):
            raise ValueError(INVALID)
    if raw_port and (not PORT.fullmatch(raw_port) or not 1 <= int(raw_port) <= 65535):
        raise ValueError(INVALID)
    return Endpoint(host, user, int(raw_port or 0))


def ssh_destination(value):
    return endpoint(value).ssh_argv()


def _matches(patterns, name):
    outcome = False
    for p in patterns:
        if not p:
            continue
        negated = p.startswith('!')
        if fnmatchcase(name, p.lstrip('!')):
            if negated:
                return False
            outcome = True
    return outcome


def list_aliases(home, *, default_user='', configured=()):
    """Return bounded, static HostName/User/Port previews, not ssh -G results.

    Includes are data, not instructions. Only regular files under the supplied
    home are eligible. Skip conditional Match blocks and unsupported tokens.
    An actual user-initiated connection is resolved separately by OpenSSH.
    """
    home = Path(home).resolve()
    ssh = home / '.ssh'
    known = set()
    for item in configured:
        try:
            known.add(endpoint(item).canonical)
        except ValueError:
            pass
    records, names, seen_names, errors = [], [], set(), set()
    budget = [0, 0]

    def visit(path, gates=(), ancestors=()):
        if len(ancestors) >= MAX_DEPTH or budget[0] >= MAX_FILES or budget[1] >= MAX_BYTES:
            errors.add('Preview limit reached')
            return
        try:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(home):
                errors.add("Includes outside your home are not previewed")
                return
            if resolved in ancestors:
                return
            # O_NONBLOCK and fstat prevent a named pipe/device from hanging preview.
            fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                s = os.fstat(stream.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_size > MAX_BYTES - budget[1]:
                    return
                data = stream.read(MAX_BYTES - budget[1] + 1)
                if len(data) > MAX_BYTES - budget[1]:
                    return
        except (OSError, RuntimeError):
            return
        budget[0] += 1
        budget[1] += len(data)
        if b'\x00' in data or b'PRIVATE KEY-----' in data[:256]:
            return
        block = ()
        in_match = False
        for line in data.decode('utf-8', errors='replace').splitlines():
            if len(line) > 8192:
                continue
            match = re.match(r'^\s*([a-zA-Z][a-zA-Z0-9]*)\s*(?:=\s*|\s+)(.*)$', line)
            if not match:
                continue
            keyword, raw = match[1].lower(), match[2]
            try:
                values = shlex.split(raw, comments=True, posix=True)
            except ValueError:
                continue
            if keyword == 'match':
                in_match = True
                errors.add('Match rules are not evaluated in previews')
                continue
            if keyword == 'host':
                in_match = False
                block = (tuple(values),)
                for alias in values:
                    if len(names) >= MAX_ALIASES:
                        errors.add('Preview limit reached')
                        break
                    if (NAME.fullmatch(alias) and alias not in seen_names and
                            all(_matches(p, alias) for p in gates + block)):
                        names.append(alias)
                        seen_names.add(alias)
                continue
            if in_match:
                continue
            conditions = gates + block
            if keyword == 'include':
                for token in values:
                    if budget[0] >= MAX_FILES:
                        break
                    # Do not expand environment variables, users, percent tokens,
                    # or command substitutions. Absolute includes must stay at home.
                    if any(c in token for c in '\x00\r\n$%\x60') or '**' in token:
                        continue
                    if token.startswith('~/'):
                        pattern = home / token[2:]
                    elif token.startswith('~'):
                        continue
                    else:
                        pattern = ssh / token
                    if '..' in pattern.parts or not pattern.is_relative_to(home):
                        errors.add("Includes outside your home are not previewed")
                        continue
                    from itertools import islice
                    hits = list(islice(iglob(str(pattern)), MAX_FILES + 1))
                    if len(hits) > MAX_FILES:
                        errors.add('Preview limit reached')
                        continue
                    for target in sorted(hits):
                        visit(target, conditions, ancestors + (resolved,))
            elif keyword in ('user', 'hostname', 'port') and len(values) == 1:
                if len(records) < 8192:
                    records.append((conditions, keyword, values[0]))
                else:
                    errors.add('Preview limit reached')
    visit(ssh / 'config')
    rows = []
    for alias in names:
        values = {}
        for conditions, key, v in records:
            if key not in values and all(_matches(p, alias) for p in conditions):
                values[key] = v
        hostname = values.get('hostname', alias).replace('%h', alias)
        user = values.get('user', default_user)
        port = values.get('port', '22')
        try:
            ep = endpoint(('[' + hostname + ']' if ':' in hostname else hostname) + ':' + port)
            if user and not USER.fullmatch(user):
                raise ValueError(INVALID)
        except ValueError:
            # Preserve the alias, but never invent an unresolved address or user.
            rows.append(dict(alias=alias, host='', user='', port=None, configured=alias in known, preview='unresolved'))
            continue
        rows.append(dict(alias=alias, host=ep.host, user=user, port=ep.port,
                         configured=alias in known, preview='static'))
    # Normalize a saved alias through the same static preview, so an alias and
    # its literal form (or two equivalent aliases) are both marked configured.
    saved = {destination_key(v, rows) for v in known}
    for r in rows:
        if destination_key(r['alias'], rows) in saved:
            r['configured'] = True
    return dict(aliases=rows, warnings=sorted(errors))


def destination_key(value, rows):
    """Preserve the SSH selector; casefold only its resolved DNS address.

    A receiver is a machine, not a login. Distinct users for the same static
    host/port do not create a second output. An unresolved alias has no safe
    address equivalence, so only its literal selector may match.
    """
    ep = endpoint(value)
    row = next((r for r in rows if r['alias'] == ep.host), None)
    if row is not None and row['preview'] != 'static':
        return ('unresolved', ep.canonical)
    name = row['host'] if row is not None else ep.host
    port = ep.port or (row['port'] if row is not None else 22)
    return ('address', name.rstrip('.').lower(), port)
