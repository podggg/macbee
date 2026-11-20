#!/usr/bin/env python3
"""SSH/stdin-only coordinator. Default: clock diagnostics, NEVER audio.
Explicit --play is a six-second synthetic test, not system audio streaming.
"""
import argparse
import json
import math
import os
import select
import shlex
import statistics
import subprocess
import time


def clock_sample(t1, t2, t3, t4):
    if not all(isinstance(t, int) and t >= 0 for t in (t1, t2, t3, t4)):
        raise ValueError('timestamps must be nonnegative integer nanoseconds')
    if t4 < t1 or t3 < t2:
        raise ValueError('nonmonotonic sample')
    delay = (t4-t1) - (t3-t2)
    if not 0 <= delay < 2_000_000_000:
        raise ValueError('invalid/excessive round trip')
    return {'rtt_ns': delay, 'offset_ns': ((t2-t1)+(t3-t4))/2}


def estimate(samples):
    if len(samples) < 10:
        raise ValueError('need at least ten valid samples')
    best = sorted(samples, key=lambda s: s['rtt_ns'])[:max(5, len(samples)//10)]
    offsets = [s['offset_ns'] for s in best]
    rtts = sorted(s['rtt_ns'] for s in samples)
    return {'samples': len(samples), 'offset_ns': statistics.median(offsets),
            'min_rtt_ms': rtts[0]/1e6,
            'median_rtt_ms': statistics.median(rtts)/1e6,
            'p95_rtt_ms': rtts[math.ceil(.95*len(rtts))-1]/1e6,
            'best_offset_spread_ms': (max(offsets)-min(offsets))/1e6,
            # Diagnostic indicator, NOT a statistical confidence interval.
            'asymmetry_scale_ms': rtts[0]/2e6}


class Peer:
    def __init__(self, command, env=None):
        self.p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=None, bufsize=0, start_new_session=True, env=env)
        self.pending = b''
        try:
            self.ready = self.read('ready')
        except BaseException:
            self.close()
            raise

    def write(self, line):
        self.p.stdin.write((line+'\n').encode('ascii'))
        self.p.stdin.flush()

    def read(self, event, timeout=5):
        deadline = time.monotonic()+timeout
        while b'\n' not in self.pending:
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([self.p.stdout], [], [], remaining)[0]:
                raise TimeoutError('peer response timeout')
            chunk = os.read(self.p.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError('peer disconnected')
            self.pending += chunk
            if len(self.pending) > 65536:
                raise RuntimeError('oversize response')
        line, self.pending = self.pending.split(b'\n', 1)
        result = json.loads(line)
        if result.get('event') != event:
            raise RuntimeError('unexpected peer response: '+repr(result))
        return result

    def sample(self):
        t1 = time.monotonic_ns()
        self.write('CLOCK')
        response = self.read('clock')
        t4 = time.monotonic_ns()
        return clock_sample(t1, response['t2'], response['t3'], t4)

    def close(self):
        if self.p.poll() is None:
            try:
                self.write('STOP')
                self.p.stdin.close()
                self.p.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.p.terminate()
                try:
                    self.p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.p.kill()
                    self.p.wait(timeout=2)
        for stream in (self.p.stdin, self.p.stdout):
            if stream and not stream.closed:
                stream.close()


def validate_ready(ready, coexist=False):
    if ready['active_other_outputs'] < 0 or (ready['active_other_outputs'] != 0 and not coexist):
        raise RuntimeError('other audio active or its state unavailable; no playback')
    if ready['mute'] != 0 or ready['volume'] <= 0:
        raise RuntimeError('speakers muted or volume unavailable; no playback')
    if ready['input_streams'] != 0 or ready['max_amplitude'] > .005:
        raise RuntimeError('output-only low-volume safety preflight failed')


def playback_result(local, remote, offset):
    for result in (local, remote):
        if result['interrupted'] or result['invalid_timestamps_or_buffers'] or result['stop_status'] or result['destroy_status']:
            raise RuntimeError('playback interrupted, invalid, or cleanup failed')
        if len(result['onset_host_ns']) != 6 or not all(result['onset_host_ns']):
            raise RuntimeError('not all six pulse onsets observed')
    skews = [(r-offset-l)/1e6 for l, r in zip(local['onset_host_ns'], remote['onset_host_ns'])]
    return {'software_output_skew_ms': skews,
            'max_abs_software_output_skew_ms': max(map(abs, skews)),
            'label': 'HAL first-frame timestamps only; NOT acoustic synchronization or lip-sync'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', required=True)
    parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--remote', required=True)
    parser.add_argument('--control-path')
    parser.add_argument('--play', action='store_true', help='audible: six soft chirps on each Mac')
    parser.add_argument('--coexist-existing', action='store_true', help='explicit one-run mix with only output processes already present at startup')
    args = parser.parse_args()
    if args.coexist_existing and not args.play:
        parser.error('--coexist-existing requires explicit --play')
    server_mode = '--serve-coexist' if args.coexist_existing else '--serve'
    peers = []
    report = {'mode': 'synthetic-tone' if args.play else 'clock-only', 'capture': False, 'coexist_existing': args.coexist_existing}
    try:
        from ssh_control import batch_ssh_env, ssh_command
        local = Peer([args.local, server_mode]); peers.append(local)
        remote = Peer(ssh_command(args.ssh_host, control_path=args.control_path, receiver_id='remote',
                                  remote_command=shlex.quote(args.remote)+' '+server_mode),
                      env=batch_ssh_env()); peers.append(remote)
        report['local_preflight'], report['remote_preflight'] = local.ready, remote.ready
        if args.play:
            validate_ready(local.ready, args.coexist_existing); validate_ready(remote.ready, args.coexist_existing)
        # Local baseline validates Python monotonic_ns vs mach_absolute_time domain.
        local_samples = [local.sample() for _ in range(20)]
        report['local_clock'] = estimate(local_samples)
        remote_samples = []
        for _ in range(60):
            remote_samples.append(remote.sample()); time.sleep(.025)
        report['remote_clock'] = estimate(remote_samples)
        if args.play:
            for clock in (report['local_clock'], report['remote_clock']):
                if clock['best_offset_spread_ms'] > 2 or clock['min_rtt_ms'] > 20:
                    raise RuntimeError('clock estimate too uncertain; no playback')
            target = time.monotonic_ns()+3_000_000_000
            remote_target = round(target+report['remote_clock']['offset_ns'])
            local_target = round(target+report['local_clock']['offset_ns'])
            remote.write('PLAY '+str(remote_target)); remote.read('armed')
            local.write('PLAY '+str(local_target)); local.read('armed')
            report['local_result'] = local.read('result', 12)
            report['remote_result'] = remote.read('result', 3)
            report['measurement'] = playback_result(report['local_result'], report['remote_result'],
                report['remote_clock']['offset_ns']-report['local_clock']['offset_ns'])
    finally:
        for peer in reversed(peers): peer.close()
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
