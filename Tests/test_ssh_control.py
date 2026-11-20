"""A3 & A6 acceptance tests: private 0700 ControlPath validation, <104-byte sun_path,
recording fake ssh across every SSH call site, and structured auth_required signaling.
No real SSH connections, DNS, network, or audio playback.
"""
import hashlib
import json
import os
import pathlib
import posixpath
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import ssh_control
from ssh_control import (
    CONTROL_PATH_ENV,
    CONTROL_PATHS_ENV,
    MAX_CONTROL_PATH_BYTES,
    OPENSSH_TEMP_BIND_MARGIN,
    SUN_PATH_LIMIT,
    SSHAuthRequiredError,
    SSHHostKeyError,
    batch_ssh_env,
    classify_ssh_failure,
    create_control_directory,
    parse_control_paths_json,
    resolve_control_path,
    ssh_command,
    validate_control_path,
)
import receiver_setup
from receiver_registry import AUTHORIZATION
from configured_transport import ConfiguredTransport
from null_transport import NullTransport
from speaker_transport import SpeakerTransport, PROBE_HASH
from application_transport import ApplicationTransport
from system_audio_transport import SystemAudioTransport
from application_timing import TimedApplicationTransport, TIMING_SHA256
from run_system_relay import run as run_system
from run_application_relay import run as run_application
from session_diagnostics import CODES, StopTrace


def _write_recording_ssh(folder, *, exit_code=0, stdout='', stderr=''):
    """Create an executable fake ssh that logs argv and askpass env keys as JSON lines."""
    log_path = folder / 'ssh-calls.jsonl'
    script = folder / 'fake-ssh'
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import json, os, sys\n'
        f'log_path = {str(log_path)!r}\n'
        'record = {\n'
        '    "argv": sys.argv,\n'
        '    "askpass_env": {k: os.environ[k] for k in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE", "MACBEE_ASKPASS_SOCKET", "MACBEE_ASKPASS_OWNER") if k in os.environ},\n'
        '}\n'
        'data = (json.dumps(record) + "\\n").encode("utf-8")\n'
        'fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)\n'
        'try:\n'
        '    os.write(fd, data)\n'
        'finally:\n'
        '    os.close(fd)\n'
        f'if {stdout!r}:\n'
        f'    sys.stdout.write({stdout!r}); sys.stdout.flush()\n'
        f'if {stderr!r}:\n'
        f'    sys.stderr.write({stderr!r}); sys.stderr.flush()\n'
        f'sys.exit({exit_code!r})\n',
        encoding='utf-8',
    )
    script.chmod(0o755)
    return script, log_path


def _read_calls(log_path):
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _assert_strict_batch_call(test, record, expected_control_path, expected_dest_suffix):
    argv = record['argv']
    # Extract all -o key=value pairs in order.
    opts = []
    i = 1
    while i < len(argv):
        if argv[i] == '-o' and i + 1 < len(argv):
            opts.append(argv[i + 1])
            i += 2
        elif argv[i] == '--':
            break
        else:
            i += 1
    for required in (
        'BatchMode=yes',
        'ConnectTimeout=5',
        'StrictHostKeyChecking=yes',
        'UpdateHostKeys=no',
        'ControlMaster=no',
        'ControlPath=' + expected_control_path,
    ):
        test.assertIn(required, opts, f'Missing {required} in {argv}')
    test.assertEqual(record['askpass_env'], {}, f'Askpass env leaked into batch SSH: {record}')
    test.assertIn('--', argv)
    sep = argv.index('--')
    test.assertEqual(argv[sep + 1], expected_dest_suffix)


class ControlPathSecurityTests(unittest.TestCase):
    def make_ctl_dir(self):
        d = create_control_directory()
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_created_control_dir_is_0700_and_strictly_under_macos_sun_path_limit(self):
        with patch.dict(os.environ, {'USER': 'a' * 64, 'HOME': '/Users/' + 'longusername' * 5}):
            ctl_dir = self.make_ctl_dir()
        st = os.lstat(ctl_dir)
        self.assertTrue(stat.S_ISDIR(st.st_mode))
        self.assertEqual(st.st_uid, os.getuid())
        self.assertEqual(st.st_mode & 0o7777, 0o700)
        ctl_path = posixpath.join(ctl_dir, '%C')
        self.assertEqual(validate_control_path(ctl_path), ctl_path)
        raw_expanded = ctl_path.replace('%C', '0' * 40).encode('utf-8')
        real_expanded = posixpath.join(os.path.realpath(ctl_dir), '0' * 40).encode('utf-8')
        self.assertLessEqual(len(raw_expanded), MAX_CONTROL_PATH_BYTES)
        self.assertLessEqual(len(real_expanded), MAX_CONTROL_PATH_BYTES)
        self.assertLess(len(raw_expanded) + OPENSSH_TEMP_BIND_MARGIN, SUN_PATH_LIMIT)
        self.assertLess(len(real_expanded) + OPENSSH_TEMP_BIND_MARGIN, SUN_PATH_LIMIT)

    def test_validate_control_path_rejects_bad_modes_symlinks_tokens_and_overlong(self):
        ctl_dir = self.make_ctl_dir()
        ctl_path = posixpath.join(ctl_dir, '%C')
        # Loose permissions rejected.
        for bad_mode in (0o755, 0o750, 0o711, 0o777, 0o1700):
            os.chmod(ctl_dir, bad_mode)
            with self.subTest(mode=oct(bad_mode)), self.assertRaises(ValueError):
                validate_control_path(ctl_path)
        os.chmod(ctl_dir, 0o700)

        # Symlinked control directory rejected via O_NOFOLLOW.
        link_dir = ctl_dir + '-lnk'
        os.symlink(ctl_dir, link_dir)
        self.addCleanup(lambda: os.unlink(link_dir) if os.path.lexists(link_dir) else None)
        with self.assertRaises(ValueError):
            validate_control_path(posixpath.join(link_dir, '%C'))

        # Non-root symlinked ancestor directory rejected.
        nested_real = posixpath.join(ctl_dir, 'real')
        os.mkdir(nested_real, 0o700)
        nested_link = posixpath.join(ctl_dir, 'sym')
        os.symlink(nested_real, nested_link)
        leaf = posixpath.join(nested_real, 'leaf')
        os.mkdir(leaf, 0o700)
        with self.assertRaises(ValueError):
            validate_control_path(posixpath.join(nested_link, 'leaf', 's'))

        # Regular file masquerading as literal socket rejected; real AF_UNIX socket accepted.
        regular = posixpath.join(ctl_dir, 'not-a-sock')
        pathlib.Path(regular).write_text('x', encoding='utf-8')
        with self.assertRaises(ValueError):
            validate_control_path(regular)
        real_sock_path = posixpath.join(ctl_dir, 'ok.sock')
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        sock.bind(real_sock_path)
        self.assertEqual(validate_control_path(real_sock_path), real_sock_path)

        # Disallowed tokens, relative paths, whitespace, and >86-byte expanded paths rejected.
        bad_paths = [
            '',
            'relative/%C',
            ctl_dir + '/',
            ctl_dir + '/../other/%C',
            ctl_dir + '/%h',
            ctl_dir + '/%r_%C',
            ctl_dir + '/%%C',
            ctl_dir + '/%C-%C',
            ctl_dir + '/has space',
            ctl_dir + '/bad\nname',
            '/tmp/' + ('x' * 50) + '/%C',  # 5 + 50 + 1 + 40 = 96 > 86
            '/' + ('a' * 104),
        ]
        for bad in bad_paths:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_control_path(bad, require_existing_dir=False)

    def test_parse_and_resolve_control_paths_precedence_and_isolation(self):
        d1 = self.make_ctl_dir()
        d2 = self.make_ctl_dir()
        p1 = posixpath.join(d1, '%C')
        p2 = posixpath.join(d2, '%C')
        env = {
            CONTROL_PATHS_ENV: json.dumps({'r-0000000000000001': p1, 'user@host-b:2222': p2}),
            CONTROL_PATH_ENV: p2,
        }
        self.assertEqual(resolve_control_path(receiver_id='r-0000000000000001', host='host-a', env=env), p1)
        self.assertEqual(resolve_control_path(receiver_id='r-0000000000000002', host='user@host-b:2222', env=env), p2)
        # Single-receiver MACBEE_SSH_CONTROL_PATH must NOT bleed onto unrelated r-... IDs.
        self.assertIsNone(resolve_control_path(receiver_id='r-0000000000000003', host='host-c', env=env))
        # Legacy 'remote' ID does accept MACBEE_SSH_CONTROL_PATH fallback.
        self.assertEqual(resolve_control_path(receiver_id='remote', host='host-c', env=env), p2)
        # Explicit control_path wins over env.
        self.assertEqual(resolve_control_path(receiver_id='remote', host='host-c', control_path=p1, env=env), p1)
        # Malformed JSON or oversized mapping fails closed.
        with self.assertRaises(ValueError):
            parse_control_paths_json('not-json')
        with self.assertRaises(ValueError):
            parse_control_paths_json('[]')
        with self.assertRaises(ValueError):
            parse_control_paths_json(json.dumps({f'k{i}': p1 for i in range(33)}))

    def test_batch_ssh_env_strips_askpass_variables(self):
        dirty = {
            'PATH': '/usr/bin',
            'SSH_AUTH_SOCK': '/tmp/agent.sock',
            'SSH_ASKPASS': '/evil/askpass',
            'SSH_ASKPASS_REQUIRE': 'force',
            'MACBEE_ASKPASS_SOCKET': '/tmp/sock',
            'MACBEE_ASKPASS_OWNER': '1234',
        }
        cleaned = batch_ssh_env(dirty)
        self.assertEqual(cleaned['SSH_AUTH_SOCK'], '/tmp/agent.sock')
        for forbidden in ('SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'MACBEE_ASKPASS_SOCKET', 'MACBEE_ASKPASS_OWNER'):
            self.assertNotIn(forbidden, cleaned)


class EverySSHCallSiteRecordingTests(unittest.TestCase):
    """Uses an executable recording fake ssh to verify EVERY SSH call site passes
    the receiver's ControlPath, BatchMode=yes, StrictHostKeyChecking=yes,
    UpdateHostKeys=no, ControlMaster=no, and never leaks SSH_ASKPASS*.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='macbee-ssh-sites-')
        self.addCleanup(self.tmp.cleanup)
        self.work = pathlib.Path(self.tmp.name)
        self.ctl_dir = create_control_directory()
        self.addCleanup(shutil.rmtree, self.ctl_dir, True)
        self.ctl_path = posixpath.join(self.ctl_dir, '%C')
        self.ctl_dir_2 = create_control_directory()
        self.addCleanup(shutil.rmtree, self.ctl_dir_2, True)
        self.ctl_path_2 = posixpath.join(self.ctl_dir_2, '%C')
        # Inject dirty askpass env vars in parent to verify every call site strips them.
        self.dirty_env = patch.dict(os.environ, {
            'SSH_ASKPASS': '/tmp/should-not-inherit',
            'SSH_ASKPASS_REQUIRE': 'force',
            'MACBEE_ASKPASS_SOCKET': '/tmp/should-not-inherit.sock',
            'MACBEE_ASKPASS_OWNER': '99999',
        })
        self.dirty_env.start()
        self.addCleanup(self.dirty_env.stop)

    def _run_popen_and_wait(self, proc):
        proc.wait(timeout=5)

    def test_receiver_setup_rpc_add_check_and_cli_pass_control_path(self):
        ready_json = json.dumps({
            'ready': True,
            'root': '/Users/receiver/Library/Application Support/Macbee Receiver/abcdef0123456789',
            'architecture': 'arm64',
            'device_sha256': 'b' * 64,
            'source_policy': 'no_audio_started',
        })
        fake_ssh, log_path = _write_recording_ssh(self.work, exit_code=0, stdout=ready_json)
        assets_dir = self.work / 'assets'
        (assets_dir / 'bin').mkdir(parents=True)
        (assets_dir / 'tools').mkdir(parents=True)
        client_bytes = b'fake-snapclient'
        client_sha = hashlib.sha256(client_bytes).hexdigest()
        (assets_dir / 'bin/snapclient-application').write_bytes(client_bytes)
        (assets_dir / 'bin/macbee-tone-probe').write_bytes(b'fake-probe')
        probe_sha = hashlib.sha256(b'fake-probe').hexdigest()
        (assets_dir / 'tools/audio_guardian.py').write_text('# guardian\n', encoding='utf-8')
        (assets_dir / 'application-client.json').write_text(json.dumps({'sha256': client_sha}), encoding='utf-8')

        cfg_home = self.work / 'cfg'
        cfg_home.mkdir(mode=0o700)
        ssh_home = self.work / 'ssh-home'
        (ssh_home / '.ssh').mkdir(parents=True)

        with patch.object(ssh_control, 'SSH_EXECUTABLE', str(fake_ssh)), \
             patch.object(receiver_setup, 'ASSET_ROOT', assets_dir), \
             patch.object(receiver_setup, 'local_device_identity', return_value='a' * 64), \
             patch('speaker_transport.PROBE_HASH', probe_sha):
            added = receiver_setup.add_receiver(
                'dev@studio.local:2222', 'Studio Mac', {}, cfg_home,
                ssh_home=ssh_home, control_path=self.ctl_path,
            )
            saved_cfg = json.loads((cfg_home / 'local-config.json').read_text(encoding='utf-8'))
            checked = receiver_setup.check_receiver(
                added['id'], saved_cfg, control_path=self.ctl_path_2,
            )
        self.assertEqual(checked['availability'], 'available')
        calls = _read_calls(log_path)
        self.assertEqual(len(calls), 2)
        _assert_strict_batch_call(self, calls[0], self.ctl_path, 'dev@studio.local')
        self.assertIn('-p', calls[0]['argv'])
        self.assertIn('2222', calls[0]['argv'])
        _assert_strict_batch_call(self, calls[1], self.ctl_path_2, 'dev@studio.local')

    def test_configured_transport_passes_distinct_control_path_per_receiver(self):
        ready_json = json.dumps({
            'ready': True,
            'root': '/fixture/receiver',
            'architecture': 'arm64',
            'device_sha256': '1' * 64,
            'source_policy': 'no_audio_started',
        })
        fake_ssh, log_path = _write_recording_ssh(self.work, exit_code=0, stdout=ready_json)
        r1_id = 'r-0000000000000001'
        r2_id = 'r-0000000000000002'
        cfg = {
            'receivers': [
                {'id': r1_id, 'name': 'Mac One', 'ssh_host': 'alice@mac-one.local', 'remote_root': '/fixture/r1', 'client_sha256': '0' * 64, 'authorization': AUTHORIZATION},
                {'id': r2_id, 'name': 'Mac Two', 'ssh_host': 'bob@mac-two.local:2202', 'remote_root': '/fixture/r2', 'client_sha256': '0' * 64, 'authorization': AUTHORIZATION},
            ],
        }
        probe = self.work / 'macbee-tone-probe'
        probe.write_bytes(b'probe-bytes')
        probe_sha = hashlib.sha256(b'probe-bytes').hexdigest()
        client = self.work / 'snapclient-application'
        client.write_bytes(b'client-bytes')
        client_sha = hashlib.sha256(b'client-bytes').hexdigest()
        guardian = self.work / 'audio_guardian.py'
        guardian.write_text('# guardian\n', encoding='utf-8')
        (self.work / 'application-client.json').write_text(json.dumps({
            'sha256': client_sha, 'format': '48000:16:2', 'stop_mode': 'wait-for-process-exit',
        }), encoding='utf-8')

        identities = iter([
            {'ready': True, 'root': '/fixture/r1', 'device_sha256': '1' * 64, 'source_policy': 'no_audio_started'},
            {'ready': True, 'root': '/fixture/r2', 'device_sha256': '2' * 64, 'source_policy': 'no_audio_started'},
        ])
        orig_rpc = receiver_setup.rpc

        def recording_rpc(host, request, **kwargs):
            orig_rpc(host, request, **kwargs)
            return next(identities)

        env_map = json.dumps({r1_id: self.ctl_path, r2_id: self.ctl_path_2})
        transport = ConfiguredTransport([r1_id, r2_id], cfg, source_policy='selected-app')
        transport.local_binary = client
        transport.local_guardian = guardian
        transport.run_dir = self.work / 'run'
        transport.run_dir.mkdir()

        with patch.dict(os.environ, {CONTROL_PATHS_ENV: env_map}), \
             patch.object(ssh_control, 'SSH_EXECUTABLE', str(fake_ssh)), \
             patch('configured_transport.ASSET_ROOT', self.work), \
             patch('configured_transport.BIN', self.work), \
             patch('configured_transport.PROBE_HASH', probe_sha), \
             patch('configured_transport.guard'), \
             patch('receiver_setup.local_device_identity', return_value='f' * 64), \
             patch('receiver_setup.rpc', side_effect=recording_rpc), \
             patch('configured_transport.NullTransport.start', side_effect=lambda self_t=transport: self_t.start_clients()):
            try:
                transport.start()
            finally:
                for child in transport.processes:
                    child.wait(timeout=5)
                    if child.stdin and not child.stdin.closed:
                        child.stdin.close()
                for handle in transport.handles:
                    handle.close()

        calls = _read_calls(log_path)
        self.assertEqual(len(calls), 4)
        # First 2: sequential preflight rpc() for r1 and r2.
        _assert_strict_batch_call(self, calls[0], self.ctl_path, 'alice@mac-one.local')
        _assert_strict_batch_call(self, calls[1], self.ctl_path_2, 'bob@mac-two.local')
        # Next 2: concurrent start_clients() reverse-forwarding SSH for r1 and r2.
        by_dest = {rec['argv'][rec['argv'].index('--') + 1]: rec for rec in calls[2:]}
        self.assertEqual(set(by_dest), {'alice@mac-one.local', 'bob@mac-two.local'})
        _assert_strict_batch_call(self, by_dest['alice@mac-one.local'], self.ctl_path, 'alice@mac-one.local')
        _assert_strict_batch_call(self, by_dest['bob@mac-two.local'], self.ctl_path_2, 'bob@mac-two.local')

    def test_null_speaker_application_system_and_timing_transports_pass_control_path(self):
        client_bytes = b'app-client'
        client_sha = hashlib.sha256(client_bytes).hexdigest()
        guardian_bytes = b'# guardian\n'
        guardian_sha = hashlib.sha256(guardian_bytes).hexdigest()
        probe_bytes = b'probe'
        probe_sha = hashlib.sha256(probe_bytes).hexdigest()
        (self.work / 'macbee-tone-probe').write_bytes(probe_bytes)
        (self.work / 'snapclient').write_bytes(b'snap')
        (self.work / 'snapclient-application').write_bytes(client_bytes)
        (self.work / 'snapclient-timing').write_bytes(b'timing-bin')
        timing_sha = hashlib.sha256(b'timing-bin').hexdigest()
        guardian = self.work / 'audio_guardian.py'
        guardian.write_bytes(guardian_bytes)
        (self.work / 'application-client.json').write_text(json.dumps({
            'sha256': client_sha, 'format': '48000:16:2', 'stop_mode': 'wait-for-process-exit',
        }), encoding='utf-8')
        # Stub local executable that exits immediately.
        local_stub = self.work / 'snapclient'
        local_stub.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        local_stub.chmod(0o755)
        probe_stub = self.work / 'macbee-tone-probe'
        probe_stub.write_text('#!/usr/bin/env python3\nimport json; print(json.dumps({"event":"ready"}), flush=True)\n', encoding='utf-8')
        probe_stub.chmod(0o755)
        probe_sha = hashlib.sha256(probe_stub.read_bytes()).hexdigest()

        # 1. NullTransport.start_clients()
        (self.work / 'null').mkdir(exist_ok=True)
        fake_ssh, log_path = _write_recording_ssh(self.work / 'null', exit_code=0)
        nt = NullTransport(control_path=self.ctl_path)
        nt.run_dir = self.work / 'null-run'
        nt.run_dir.mkdir()
        with patch.object(ssh_control, 'SSH_EXECUTABLE', str(fake_ssh)), \
             patch('null_transport.BIN', self.work), \
             patch('null_transport.SSH_HOST', 'alice@null-host.local'):
            nt.start_clients()
            for p in nt.processes:
                p.wait(timeout=5)
                if p.stdin and not p.stdin.closed:
                    p.stdin.close()
            for h in nt.handles:
                h.close()
        calls = _read_calls(log_path)
        self.assertEqual(len(calls), 1)
        _assert_strict_batch_call(self, calls[0], self.ctl_path, 'alice@null-host.local')

        # 2. SpeakerTransport.start_clients()
        (self.work / 'spk').mkdir()
        fake_ssh, log_path = _write_recording_ssh(self.work / 'spk', exit_code=0)
        st = SpeakerTransport(control_path=self.ctl_path)
        st.local_guardian = local_stub
        st.run_dir = self.work / 'spk-run'
        st.run_dir.mkdir()
        with patch.object(ssh_control, 'SSH_EXECUTABLE', str(fake_ssh)), \
             patch('speaker_transport.BIN', self.work), \
             patch('speaker_transport.PROBE_HASH', probe_sha), \
             patch('speaker_transport.SSH_HOST', 'alice@spk-host.local'):
            st.start_clients()
            for p in st.processes:
                p.wait(timeout=5)
                if p.stdin and not p.stdin.closed:
                    p.stdin.close()
            for h in st.handles:
                h.close()
        calls = _read_calls(log_path)
        self.assertEqual(len(calls), 1)
        _assert_strict_batch_call(self, calls[0], self.ctl_path, 'alice@spk-host.local')

        # 3. ApplicationTransport.start() (two shasum checks)
        (self.work / 'app').mkdir()
        shasum_script = self.work / 'app/fake-ssh'
        shasum_log = self.work / 'app/ssh-calls.jsonl'
        shasum_script.write_text(
            '#!/usr/bin/env python3\n'
            'import json, os, sys\n'
            f'log_path = {str(shasum_log)!r}\n'
            'with open(log_path, "a", encoding="utf-8") as h:\n'
            '    h.write(json.dumps({"argv": sys.argv, "askpass_env": {k: os.environ[k] for k in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE") if k in os.environ}}) + "\\n")\n'
            'cmd = sys.argv[-1]\n'
            f'if "audio_guardian" in cmd: print({guardian_sha!r}, "file")\n'
            f'else: print({client_sha!r}, "file")\n',
            encoding='utf-8',
        )
        shasum_script.chmod(0o755)
        at = ApplicationTransport(control_path=self.ctl_path)
        at.local_binary = self.work / 'snapclient-application'
        at.local_guardian = guardian
        with patch.object(ssh_control, 'SSH_EXECUTABLE', str(shasum_script)), \
             patch('application_transport.require_receiver'), \
             patch('application_transport.guard'), \
             patch('application_transport.ASSET_ROOT', self.work), \
             patch('application_transport.SSH_HOST', 'alice@app-host.local'), \
             patch('application_transport.REMOTE_CLIENT_SHA256', ''), \
             patch.object(SpeakerTransport, 'start', return_value=None):
            at.start()
        calls = _read_calls(shasum_log)
        self.assertEqual(len(calls), 2)
        for rec in calls:
            _assert_strict_batch_call(self, rec, self.ctl_path, 'alice@app-host.local')

        # 4. SystemAudioTransport.start() + start_clients()
        (self.work / 'sysaudio').mkdir()
        sys_log = self.work / 'sysaudio/ssh-calls.jsonl'
        sys_script = self.work / 'sysaudio/fake-ssh'
        sys_script.write_text(
            '#!/usr/bin/env python3\n'
            'import json, os, sys\n'
            f'log_path = {str(sys_log)!r}\n'
            'with open(log_path, "a", encoding="utf-8") as h:\n'
            '    h.write(json.dumps({"argv": sys.argv, "askpass_env": {k: os.environ[k] for k in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE") if k in os.environ}}) + "\\n")\n'
            'cmd = sys.argv[-1]\n'
            f'if "audio_guardian" in cmd: print({guardian_sha!r}, "file")\n'
            f'elif "shasum" in cmd: print({client_sha!r}, "file")\n',
            encoding='utf-8',
        )
        sys_script.chmod(0o755)
        sat = SystemAudioTransport(['remote'], control_path=self.ctl_path)
        sat.local_binary = self.work / 'snapclient-application'
        sat.local_guardian = guardian
        sat.run_dir = self.work / 'sys-run'
        sat.run_dir.mkdir()
        with patch.object(ssh_control, 'SSH_EXECUTABLE', str(sys_script)), \
             patch('system_audio_transport.require_receiver'), \
             patch('system_audio_transport.guard'), \
             patch('system_audio_transport.ASSET_ROOT', self.work), \
             patch('system_audio_transport.BIN', self.work), \
             patch('system_audio_transport.PROBE_HASH', probe_sha), \
             patch('system_audio_transport.SSH_HOST', 'alice@sys-host.local'), \
             patch('system_audio_transport.REMOTE_CLIENT_SHA256', ''), \
             patch('system_audio_transport.NullTransport.start', side_effect=lambda s=sat: s.start_clients()):
            sat.start()
            for p in sat.processes:
                p.wait(timeout=5)
                if p.stdin and not p.stdin.closed:
                    p.stdin.close()
            for h in sat.handles:
                h.close()
        calls = _read_calls(sys_log)
        self.assertEqual(len(calls), 3)
        for rec in calls:
            _assert_strict_batch_call(self, rec, self.ctl_path, 'alice@sys-host.local')

        # 5. TimedApplicationTransport.start() (remote Peer)
        (self.work / 'timing').mkdir()
        timing_ssh, timing_log = _write_recording_ssh(
            self.work / 'timing', exit_code=0, stdout=json.dumps({'event': 'ready'}) + '\n',
        )
        tat = TimedApplicationTransport(control_path=self.ctl_path)
        tat.local_binary = self.work / 'snapclient-timing'
        with patch.object(ssh_control, 'SSH_EXECUTABLE', str(timing_ssh)), \
             patch('application_timing.TIMING_SHA256', timing_sha), \
             patch('application_timing.BIN', self.work), \
             patch('application_timing.SSH_HOST', 'alice@timing-host.local'), \
             patch.object(ApplicationTransport, 'start', return_value=None), \
             patch('application_timing.sample_clock', return_value={'reference_ns': 1}):
            tat.start()
            for peer in tat.clock_peers.values():
                peer.close()
        calls = _read_calls(timing_log)
        self.assertEqual(len(calls), 1)
        _assert_strict_batch_call(self, calls[0], self.ctl_path, 'alice@timing-host.local')

    def test_dual_probe_backend_probe_and_coreaudio_probe_pass_control_path(self):
        # Verify dual_probe, run_backend_probe, and run_coreaudio_probe all construct via ssh_control.ssh_command.
        import dual_probe
        import run_backend_probe
        import run_coreaudio_probe

        for mod in (run_backend_probe, run_coreaudio_probe):
            self.assertEqual(mod.SSH[0], ssh_control.SSH_EXECUTABLE)
            self.assertIn('BatchMode=yes', mod.SSH)
            self.assertIn('StrictHostKeyChecking=yes', mod.SSH)
            self.assertIn('UpdateHostKeys=no', mod.SSH)
            self.assertIn('ControlMaster=no', mod.SSH)

        (self.work / 'dp').mkdir()
        dp_ssh, dp_log = _write_recording_ssh(
            self.work / 'dp', exit_code=0, stdout=json.dumps({'event': 'ready'}) + '\n',
        )
        local_probe = self.work / 'local-probe'
        local_probe.write_text('#!/usr/bin/env python3\nimport json; print(json.dumps({"event":"ready"}), flush=True)\n', encoding='utf-8')
        local_probe.chmod(0o755)

        with patch.object(ssh_control, 'SSH_EXECUTABLE', str(dp_ssh)), \
             patch.object(sys, 'argv', ['dual_probe.py', '--local', str(local_probe), '--ssh-host', 'alice@dp-host.local', '--remote', '/opt/probe', '--control-path', self.ctl_path]), \
             patch.object(dual_probe.Peer, 'sample', side_effect=RuntimeError('stop-after-connect')):
            with self.assertRaisesRegex(RuntimeError, 'stop-after-connect'):
                dual_probe.main()
        calls = _read_calls(dp_log)
        self.assertEqual(len(calls), 1)
        _assert_strict_batch_call(self, calls[0], self.ctl_path, 'alice@dp-host.local')

        # Verify run_backend_probe.run() and run_coreaudio_probe.run() invoke recording fake ssh with ControlPath + BatchMode=yes.
        for name, mod in (('run_backend_probe', run_backend_probe), ('run_coreaudio_probe', run_coreaudio_probe)):
            sub = self.work / name
            sub.mkdir()
            fake_ssh, log_path = _write_recording_ssh(sub, exit_code=0)
            bin_dir = sub / 'snapcast-0.35.0/bin'
            bin_dir.mkdir(parents=True)
            stub = bin_dir / 'snapserver'
            stub.write_text('#!/bin/sh\nsleep 2\n', encoding='utf-8')
            stub.chmod(0o755)
            client_stub = bin_dir / 'snapclient'
            client_stub.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            client_stub.chmod(0o755)
            guardian_stub = sub / 'guardian.py'
            guardian_stub.write_text('import sys; sys.exit(0)\n', encoding='utf-8')
            real_popen = subprocess.Popen
            spawned_ssh = []

            def intercept_popen(cmd, *args, **kwargs):
                if cmd and cmd[0] == str(fake_ssh):
                    p = real_popen(cmd, *args, **kwargs)
                    p.wait(timeout=5)
                    if p.stdin and not p.stdin.closed:
                        p.stdin.close()
                    spawned_ssh.append(cmd)
                    raise RuntimeError('stop-after-ssh-record')
                p = real_popen(cmd, *args, **kwargs)
                if p.stdin and not p.stdin.closed:
                    self.addCleanup(p.stdin.close)
                return p

            fake_lsof = Mock(stdout=f'127.0.0.1:{mod.PORT} (LISTEN)\n')
            fake_sock = Mock()
            fake_sock.__enter__ = Mock(return_value=fake_sock)
            fake_sock.__exit__ = Mock(return_value=False)
            patches = [
                patch.dict(os.environ, {CONTROL_PATH_ENV: self.ctl_path}),
                patch.object(ssh_control, 'SSH_EXECUTABLE', str(fake_ssh)),
                patch.object(mod, 'ROOT', sub),
                patch.object(mod, 'BIN', bin_dir),
                patch.object(mod, 'SSH_HOST', f'alice@{name}.local'),
                patch.object(mod, 'require_receiver'),
                patch.object(mod, 'guard', return_value=(0, 10**11)),
                patch.object(mod.socket, 'socket', return_value=fake_sock),
                patch.object(mod.socket, 'create_connection', return_value=fake_sock),
                patch.object(mod.subprocess, 'run', return_value=fake_lsof),
                patch.object(mod.subprocess, 'Popen', side_effect=intercept_popen),
            ]
            if name == 'run_coreaudio_probe':
                patches.append(patch.object(mod, 'GUARDIAN', guardian_stub))
            with self.subTest(probe=name):
                stack = [p.start() for p in patches]
                try:
                    with self.assertRaisesRegex(RuntimeError, 'stop-after-ssh-record'):
                        mod.run()
                finally:
                    for p in reversed(patches):
                        p.stop()
                calls = _read_calls(log_path)
                self.assertEqual(len(calls), 1)
                _assert_strict_batch_call(self, calls[0], self.ctl_path, f'alice@{name}.local')

    def test_relay_cli_entrypoints_forward_ssh_control_paths_flag(self):
        import run_system_relay
        import run_application_relay

        mapping = {'r-0000000000000001': self.ctl_path}
        raw = json.dumps(mapping)

        sys_session = Mock()
        with patch.object(sys, 'argv', ['run_system_relay.py', '--system-audio-stdin', '--outputs', 'r-0000000000000001', '--configured-outputs', '--ssh-control-paths', raw]), \
             patch('configured_transport.ConfiguredTransport', return_value=sys_session), \
             patch.object(run_system_relay, 'request_relay_qos'), \
             patch.object(run_system_relay, 'run', return_value={}):
            self.assertEqual(run_system_relay.main(), 0)
        self.assertEqual(sys_session.control_paths, mapping)

        app_session = Mock()
        captured_policy = {}
        with patch.object(sys, 'argv', ['run_application_relay.py', '--selected-app-stdin', '--manual-session', '--outputs', 'r-0000000000000001', '--ssh-control-paths', raw]), \
             patch('configured_transport.ConfiguredTransport', side_effect=lambda outs, source_policy='system': captured_policy.update(outs=outs, source_policy=source_policy) or app_session), \
             patch.object(run_application_relay, 'run', return_value={}):
            self.assertEqual(run_application_relay.main(), 0)
        self.assertEqual(app_session.control_paths, mapping)
        self.assertEqual(captured_policy['source_policy'], 'selected-app')


class AuthRequiredSignalingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='macbee-auth-req-')
        self.addCleanup(self.tmp.cleanup)
        self.work = pathlib.Path(self.tmp.name)

    def test_classify_host_key_vs_auth_required(self):
        self.assertIsInstance(
            classify_ssh_failure('Permission denied (publickey,password).\n', receiver_id='r-0000000000000001'),
            SSHAuthRequiredError,
        )
        # Host-key failure takes precedence and is NEVER misclassified as auth_required.
        changed = classify_ssh_failure(
            'REMOTE HOST IDENTIFICATION HAS CHANGED!\nPermission denied\n',
            receiver_id='r-0000000000000001',
        )
        self.assertIsInstance(changed, SSHHostKeyError)
        self.assertEqual(changed.code, 'host_key_unverified')
        self.assertIn('auth_required', CODES)
        self.assertIn('host_key_unverified', CODES)

    def test_receiver_setup_cli_emits_structured_auth_required_and_host_key_codes(self):
        rid = 'r-0123456789abcdef'
        cfg = {
            'receivers': [{
                'id': rid,
                'name': 'Living Room',
                'ssh_host': 'secretuser@192.168.1.99',
                'remote_root': '/fixture/r',
                'client_sha256': '0' * 64,
                'probe_sha256': '1' * 64,
                'authorization': AUTHORIZATION,
            }],
        }
        assets_dir = self.work / 'assets'
        (assets_dir / 'bin').mkdir(parents=True)
        (assets_dir / 'tools').mkdir(parents=True)
        (assets_dir / 'bin/snapclient-application').write_bytes(b'c')
        c_sha = hashlib.sha256(b'c').hexdigest()
        (assets_dir / 'bin/macbee-tone-probe').write_bytes(b'p')
        p_sha = hashlib.sha256(b'p').hexdigest()
        (assets_dir / 'tools/audio_guardian.py').write_text('# g\n', encoding='utf-8')
        (assets_dir / 'application-client.json').write_text(json.dumps({'sha256': c_sha}), encoding='utf-8')

        for kind, stderr, expected_code in (
            ('auth', 'secretuser@192.168.1.99: Permission denied (publickey,keyboard-interactive).\n', 'auth_required'),
            ('hostkey', 'Host key verification failed.\n', 'host_key_unverified'),
        ):
            sub = self.work / kind
            sub.mkdir()
            fake_ssh, _ = _write_recording_ssh(sub, exit_code=255, stderr=stderr)
            out = []
            with patch.object(ssh_control, 'SSH_EXECUTABLE', str(fake_ssh)), \
                 patch.object(receiver_setup, 'settings', cfg), \
                 patch.object(receiver_setup, 'ASSET_ROOT', assets_dir), \
                 patch('speaker_transport.PROBE_HASH', p_sha), \
                 patch.object(sys, 'argv', ['receiver_setup.py', '--check', rid]), \
                 patch('builtins.print', side_effect=out.append):
                rc = receiver_setup.main()
            self.assertEqual(rc, 1)
            payload = json.loads(out[0])
            self.assertFalse(payload['ok'])
            self.assertEqual(payload['code'], expected_code)
            self.assertEqual(payload['receiver_id'], rid)
            # Never leak username/IP from ssh stderr.
            self.assertNotIn('secretuser', payload['error'])
            self.assertNotIn('192.168.1.99', payload['error'])

    def test_both_relays_emit_auth_required_with_opaque_id_and_immediate_cleanup_verified(self):
        rid = 'r-abcdef0123456789'
        cfg = {
            'receivers': [{
                'id': rid,
                'name': 'Bedroom Mac',
                'ssh_host': 'privateuser@10.20.30.40',
                'remote_root': '/fixture/r',
                'client_sha256': '0' * 64,
                'authorization': AUTHORIZATION,
            }],
        }
        client = self.work / 'snapclient-application'
        client.write_bytes(b'c-bin')
        c_sha = hashlib.sha256(b'c-bin').hexdigest()
        guardian = self.work / 'audio_guardian.py'
        guardian.write_text('# guardian\n', encoding='utf-8')
        (self.work / 'application-client.json').write_text(json.dumps({
            'sha256': c_sha, 'format': '48000:16:2', 'stop_mode': 'wait-for-process-exit',
        }), encoding='utf-8')
        fake_ssh, _ = _write_recording_ssh(
            self.work, exit_code=255,
            stderr='privateuser@10.20.30.40: Permission denied (publickey,password).\n',
        )

        for is_app in (False, True):
            with self.subTest(selected_app=is_app):
                transport = ConfiguredTransport(
                    ['local', rid], cfg, source_policy='selected-app' if is_app else 'system',
                )
                transport.local_binary = client
                transport.local_guardian = guardian
                events = []
                trace = StopTrace(lambda: 1.0)
                with patch.object(ssh_control, 'SSH_EXECUTABLE', str(fake_ssh)), \
                     patch('configured_transport.ASSET_ROOT', self.work), \
                     patch('configured_transport.guard'), \
                     patch('receiver_setup.local_device_identity', return_value='a' * 64), \
                     patch('configured_transport.NullTransport.start', side_effect=AssertionError('Must fail before server/audio')):
                    if is_app:
                        result = run_application(transport, lambda: b'', emit=lambda k, **r: events.append((k, r)), clock=lambda: 1.0, manual_session=True, trace=trace)
                    else:
                        result = run_system(transport, lambda: b'', emit=lambda k, **r: events.append((k, r)), clock=lambda: 1.0, trace=trace)
                self.assertTrue(result['cleanup_verified'], 'Must not hang 12s lease when nothing launched')
                self.assertEqual(result['reason_code'], 'auth_required')
                self.assertEqual(result['auth_required_receiver'], rid)
                self.assertEqual(trace.first, 'auth_required')
                stopping = [row for kind, row in events if kind == 'stopping']
                failed = [row for kind, row in events if kind == 'failed']
                self.assertEqual(len(stopping), 1)
                self.assertEqual(stopping[0]['reason_code'], 'auth_required')
                self.assertEqual(stopping[0]['auth_required_receiver'], rid)
                self.assertEqual(len(failed), 1)
                self.assertEqual(failed[0]['auth_required_receiver'], rid)
                # Ensure zero hostname/username/IP leaks anywhere in emitted events.
                dumped = json.dumps(events)
                self.assertNotIn('privateuser', dumped)
                self.assertNotIn('10.20.30.40', dumped)

    def test_configured_transport_lost_before_ready_detects_auth_required_in_ssh_log(self):
        rid = 'r-1111222233334444'
        cfg = {
            'receivers': [{
                'id': rid,
                'name': 'Mac',
                'ssh_host': 'alice@mac.local',
                'remote_root': '/fixture/r',
                'client_sha256': '0' * 64,
                'authorization': AUTHORIZATION,
            }],
        }
        transport = ConfiguredTransport([rid], cfg)
        transport.run_dir = self.work / 'lost-run'
        transport.run_dir.mkdir()
        transport.log_path(rid).write_text('Permission denied (publickey).\n', encoding='utf-8')
        child = Mock()
        transport.peers[rid] = child
        with self.assertRaises(SSHAuthRequiredError) as ctx:
            transport._lost(rid, code=255)
        self.assertEqual(ctx.exception.receiver_id, rid)
        self.assertEqual(transport.auth_required_receiver, rid)


class PackagingContractTests(unittest.TestCase):
    def test_package_and_build_scripts_wire_add_mac_and_no_private_paths(self):
        build_src = (ROOT / 'tools/build_application_candidate.py').read_text(encoding='utf-8')
        pkg_src = (ROOT / 'tools/package_release.py').read_text(encoding='utf-8')
        for required_m in (
            'Sources/MacbeeApp/SSHBrowse.m',
            'Sources/MacbeeApp/AddMacSheet.m',
            'Sources/SSHLogin/SSHSignIn.m',
            'Sources/SSHLogin/SSHControlMaster.m',
        ):
            self.assertIn(required_m, build_src)
        self.assertIn("MacbeeBoundedSystemPacing=True", build_src)
        self.assertIn("NSBonjourServices=['_ssh._tcp']", build_src)
        self.assertIn("NSLocalNetworkUsageDescription=", build_src)
        self.assertIn("macbee-askpass", build_src)
        self.assertIn("'ssh_control.py'", pkg_src)
        self.assertIn("'ssh_discovery.py'", pkg_src)
        self.assertIn("macbee-askpass", pkg_src)
        # Ensure none of the bundled Python modules leak forbidden release scan needles.
        import package_release
        needles = (b'/Users/', b'/Volumes/', b'/private/tmp/')
        for name in package_release.MODULES + package_release.SYSTEM_MODULES:
            raw = (ROOT / 'tools' / name).read_bytes()
            for needle in needles:
                self.assertNotIn(needle, raw, f'{needle!r} found in tools/{name}')


if __name__ == '__main__':
    unittest.main()
