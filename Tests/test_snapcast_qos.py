"""Opt-in pinned Snapcast tests: OS QoS readback, no app or physical output."""

import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from test_pacing_process import server_config, verify_stage

USER_INITIATED = 0x19  # Darwin qos_class_t value, not a priority/concurrency estimate.
STAGE = os.environ.get('MACBEE_QOS_STAGE')
RECEIPT = re.compile(r'\bMACBEE_WORKER_QOS ([\w-]+) (\d+) (\d+) (\d+)\b')


def extract_receipts(log, role):
    return [(int(before), int(after), int(error)) for name, before, after, error
            in RECEIPT.findall(log) if name == role]


@unittest.skipUnless(sys.platform == 'darwin' and STAGE, 'requires explicit matching Darwin QoS stage')
class SnapcastQosStageTests(unittest.TestCase):
    def test_running_asio_and_file_workers_promote_actual_qos(self):
        stage = Path(STAGE)
        server = verify_stage(stage, 3)
        client = stage / 'bin/snapclient'
        manifest = json.loads((stage / 'manifest.json').read_text())
        self.assertEqual(manifest.get('darwin_worker_qos_version'), 1, 'pre-QoS stage')
        self.assertEqual(hashlib.sha256(client.read_bytes()).hexdigest(),
                         manifest['binaries']['snapclient']['sha256'])
        with tempfile.TemporaryDirectory(prefix='macbee-qos-stage-') as scratch:
            root = Path(scratch)
            env = dict(os.environ, HOME=scratch, TMPDIR=scratch, XDG_CONFIG_HOME=scratch)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            conf = server_config(root, port, 3)
            # Only synthetic stdin and a private localhost file sink. Use the
            # upstream raw input mode: this test targets scheduling, not slots.
            conf.write_text(conf.read_text().replace('&macbee_clock=v3-slots', '') +
                            '\n[logging]\nsink = stdout\nfilter = *:debug\n')
            # Start children at an explicit utility QoS baseline so main-thread
            # promotion is measurable even when tests run from an interactive
            # shell (QOS_CLASS_USER_INTERACTIVE = 33).
            launch_prefix = ['/usr/sbin/taskpolicy', '-c', 'utility']
            peer = None
            with (root / 'server.log').open('wb') as server_log, (root / 'client.log').open('wb') as client_log:
                proc = subprocess.Popen(launch_prefix + [str(server), '--config', str(conf)], stdin=subprocess.PIPE,
                                        stdout=server_log, stderr=subprocess.STDOUT, env=env, bufsize=0)
                try:
                    deadline = time.monotonic() + 3
                    while True:
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                                break
                        except ConnectionRefusedError:
                            if time.monotonic() >= deadline or proc.poll() is not None:
                                self.fail((root / 'server.log').read_text(errors='replace'))
                            time.sleep(.02)
                    peer = subprocess.Popen(launch_prefix + [str(client), '--player', 'file:filename=/dev/null', '--mixer', 'none',
                                             '--hostID', 'synthetic-qos-sink', '--logsink', 'stdout',
                                             '--logfilter', '*:debug', f'tcp://127.0.0.1:{port}'],
                                            stdin=subprocess.DEVNULL, stdout=client_log, stderr=subprocess.STDOUT,
                                            env=env)
                    os.set_blocking(proc.stdin.fileno(), False)
                    deadline = time.monotonic() + 4
                    # Small marker blocks, no audio device is opened.
                    marker = b'\x01\x00\x01\x00' * 960
                    while time.monotonic() < deadline and proc.poll() is None and peer.poll() is None:
                        try:
                            os.write(proc.stdin.fileno(), marker)
                        except BlockingIOError:
                            pass
                        client_text = (root / 'client.log').read_text(errors='replace')
                        if extract_receipts(client_text, 'client-file'):
                            break
                        time.sleep(.02)
                finally:
                    for child in (peer, proc):
                        if child is None:
                            continue
                        if child.poll() is None:
                            child.terminate()
                        try:
                            child.wait(3)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(2)
                    proc.stdin.close()
            for log, role, expected in (('server.log', 'server-io', 2), ('client.log', 'client-file', 1)):
                output = (root / log).read_text(errors='replace')
                rows = extract_receipts(output, role)
                self.assertEqual(len(rows), expected, output[-3000:])
                for before, after, error in rows:
                    self.assertEqual(error, 0, (role, before, after, error))
                    self.assertGreaterEqual(after, max(before, USER_INITIATED), (role, before, after))
                # A test on an already high-QoS task is inconclusive about a
                # promotion. Same-run API readbacks must show a real increase.
                self.assertTrue(any(before < after for before, after, _ in rows),
                                ('no measurable promotion; an already-high baseline is inconclusive', role, rows))
                print('Darwin runtime QoS', role, rows, flush=True)


ARCHIVE = os.environ.get('MACBEE_QOS_ARCHIVE')


@unittest.skipUnless(sys.platform == 'darwin' and ARCHIVE, 'requires pinned Darwin backend source archive')
class SnapcastCoreAudioQosTests(unittest.TestCase):
    def test_existing_coreaudio_worker_promotes_before_starting_its_run_loop(self):
        """Use the reconstructed real worker, stubbing only device and stream APIs."""
        import tarfile

        tools = Path(__file__).resolve().parents[1] / 'tools'
        sys.path.insert(0, str(tools))
        from release_backend import ARCHIVES, BASE_SHA, digest, patches

        self.assertEqual(digest(ARCHIVE), ARCHIVES['snapcast-v0.35.0.tar.gz'])
        rel = 'client/player/coreaudio_player.cpp'
        with tarfile.open(ARCHIVE) as archive:
            member = archive.extractfile('snapcast-0.35.0/' + rel)
            self.assertIsNotNone(member)
            full_source = member.read().decode()
        for path, old, new in patches():
            if path == rel:
                self.assertEqual(full_source.count(old), 1)
                full_source = full_source.replace(old, new)
        self.assertEqual(hashlib.sha256(full_source.encode()).hexdigest(), BASE_SHA)
        # Compile the actual worker body and implementation helper, not a
        # transcription or mock set-QoS call. AudioQueue is not linked or opened.
        routine = full_source.split('void CoreAudioPlayer::worker()', 1)[1].split(
            'void CoreAudioPlayer::initAudioQueue()', 1)[0]
        helper = re.search(r'#if defined\(__APPLE__\)\n#include <pthread\.h>.*?\n#endif',
                           full_source, re.DOTALL).group(0)
        fixture = r'''
#include <cassert>
#include <chrono>
#include <exception>
#include <iostream>
#include <thread>
#define LOG(level, tag) std::cerr
'''
        fixture += helper + r'''
struct FixtureStream {
    bool waitForChunk(std::chrono::milliseconds) { return true; }
};
namespace chronos {
void sleep(int) {}
}
struct CoreAudioPlayer {
    explicit CoreAudioPlayer(FixtureStream* stream): pubStream_(stream) {}
    void worker();
    bool active_ = true;
    FixtureStream* pubStream_;
    qos_class_t at_player_start = QOS_CLASS_UNSPECIFIED;
    void initAudioQueue() {
        at_player_start = qos_class_self();
        active_ = false;
    }
};
void CoreAudioPlayer::worker()'''
        fixture += routine + r'''
int main() {
    for (auto baseline : {QOS_CLASS_UTILITY, QOS_CLASS_USER_INTERACTIVE}) {
        std::thread worker([=] {
            const int set_result = pthread_set_qos_class_self_np(baseline, 0);
            assert(set_result == 0);
            const auto before = qos_class_self();
            assert(before == baseline);
            FixtureStream stream;
            CoreAudioPlayer player(&stream);
            player.worker();
            std::cout << static_cast<int>(before) << " "
                      << static_cast<int>(player.at_player_start) << "\n";
        });
        worker.join();
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='macbee-coreaudio-qos-') as scratch:
            root = Path(scratch)
            (root / 'fixture.cpp').write_text(fixture)
            subprocess.run(['xcrun', 'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(root / 'fixture.cpp'), '-pthread', '-o', str(root / 'fixture')],
                           check=True, capture_output=True, timeout=25)
            result = subprocess.run([str(root / 'fixture')], check=True, capture_output=True,
                                    text=True, timeout=5)
        rows = [tuple(map(int, row.split())) for row in result.stdout.splitlines()]
        self.assertEqual(rows, [(0x11, USER_INITIATED), (0x21, 0x21)])
        print('Darwin CoreAudio worker before/at device start (no device opened)', rows, flush=True)
