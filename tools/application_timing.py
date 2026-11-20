"""Opt-in, owned-marker output timing; no capture operation on import.

Uses the already built low-amplitude timing client. Records numeric timestamps,
never PCM. Clock intervals assume nonnegative one-way delay and an approximately
affine clock relationship over this short test, not a statistical confidence bound.
"""
import json
import math
import re
import shlex
import statistics
import time

from application_transport import ApplicationTransport
from dual_probe import Peer, clock_sample, estimate
from ssh_control import batch_ssh_env
from ssh_discovery import ssh_destination
from local_config import SSH_HOST, REMOTE_ROOT
from null_transport import SSH
from relay_runtime import BIN

TIMING_SHA256='5e093674b306fca937512c4c94d29d9bc25d26283bd8fdb6df59392abb5782d5'


def summarize_clock(rows):
    result=estimate([clock_sample(**row) for row in rows])
    lower=max(row['t3']-row['t4'] for row in rows)
    upper=min(row['t2']-row['t1'] for row in rows)
    if lower>upper:raise ValueError('Clock intervals have no constant-offset intersection')
    result.update(lower_ns=lower,upper_ns=upper,
        reference_ns=(rows[0]['t1']+rows[-1]['t4'])//2,
        batch_span_ms=(rows[-1]['t4']-rows[0]['t1'])/1e6,
        offset_ns=min(upper,max(lower,result['offset_ns'])),raw=rows)
    if result['min_rtt_ms']>20 or result['best_offset_spread_ms']>2:
        raise ValueError('Clock calibration is too uncertain for this short test')
    return result


def sample_clock(peer,check):
    rows=[];deadline=time.monotonic()+2
    for _ in range(20):
        check()
        if time.monotonic()>=deadline:raise TimeoutError('Bounded clock calibration expired')
        t1=time.monotonic_ns();peer.write('CLOCK')
        row=peer.read('clock',timeout=.5);t4=time.monotonic_ns()
        rows.append(dict(t1=t1,t2=row['t2'],t3=row['t3'],t4=t4))
    return summarize_clock(rows)


def markers(text):
    result={}
    for value in re.findall(r'MACBEE_MARKER (\{[^\n]+\})',text):
        row=json.loads(value);key=row['id']
        if key in result:raise ValueError('Duplicate output marker')
        if not 1<=key<=6 or row['source_frame']!=(key-1)*48000:
            raise ValueError('Unexpected marker identity')
        if row['mapping'] not in ('enqueue_host','queue_sample_host_anchor') or row['host_ns']<=0 or row['discontinuity']:
            raise ValueError('Marker has no valid queue-to-host mapping')
        if row['translate_status'] or row['translated_flags']&3!=3 or row['device_status'] or row['device_flags']&3!=3:
            raise ValueError('Marker did not map to a running native output device')
        result[key]=row
    if set(result)!=set(range(1,7)):raise ValueError('Six unique output markers are required on each Mac')
    return result


def inverse_time(host,before,after,lo_key='offset_ns',hi_key='offset_ns'):
    span=after['reference_ns']-before['reference_ns']
    if span<=0:raise ValueError('Invalid clock calibration ordering')
    first=before[lo_key];last=after[hi_key]
    slope=(last-first)/span
    if not math.isfinite(slope) or abs(slope)>.001:
        raise ValueError('Clock model exceeds the diagnostic drift guard')
    result=(host-first+slope*before['reference_ns'])/(1+slope)
    if not before['reference_ns']<=result<=after['reference_ns']:
        raise ValueError('Marker is not bracketed by clock calibration')
    return result


def time_range(host,before,after):
    values=[inverse_time(host,before,after,a,b)
        for a in ('lower_ns','upper_ns') for b in ('lower_ns','upper_ns')]
    return min(values),max(values)


def analyze(texts,before,after):
    local,remote=map(markers,texts);deltas=[];bounds=[]
    for key in range(1,7):
        l=local[key]['host_ns'];r=remote[key]['host_ns']
        left=inverse_time(l,before['local'],after['local'])
        right=inverse_time(r,before['remote'],after['remote'])
        ll,lu=time_range(l,before['local'],after['local'])
        rl,ru=time_range(r,before['remote'],after['remote'])
        deltas.append((right-left)/1e6);bounds.append([(rl-lu)/1e6,(ru-ll)/1e6])
    scale=before['remote']['asymmetry_scale_ms']+before['local']['asymmetry_scale_ms']
    scale=max(scale,after['remote']['asymmetry_scale_ms']+after['local']['asymmetry_scale_ms'])
    return dict(receiver_minus_sender_ms=deltas,median_ms=statistics.median(deltas),
        minimum_ms=min(deltas),maximum_ms=max(deltas),
        conditional_marker_intervals_ms=bounds,
        conditional_median_interval_ms=[statistics.median(x[0] for x in bounds),statistics.median(x[1] for x in bounds)],
        half_minimum_rtt_scale_ms=scale,
        label='OS-reported scheduled output marker time mapped to associated device/host clocks; NOT acoustic arrival',
        assumptions='Nonnegative link delay; approximately constant offset within each calibration batch and affine clock mapping between batches. Intervals are model-conditional, not statistical confidence or calibrated DAC error bounds.',
        mapping_methods=sorted({row['mapping'] for row in list(local.values())+list(remote.values())}),
        marker_metadata=dict(local=list(local.values()),remote=list(remote.values())))


class TimedApplicationTransport(ApplicationTransport):
    manifest_filename='application-timing-client.json'
    local_binary=BIN/'snapclient-timing'
    remote_binary=REMOTE_ROOT+'snapclient-macbee-timing'
    run_prefix='relay-application-timing-'
    host_prefix='macbee-application-timing'

    def __init__(self,control_path=None,control_paths=None):
        super().__init__(control_path=control_path,control_paths=control_paths);self.clock_peers={};self.clock_before=None;self.timing_finished=False

    def start(self):
        import hashlib
        if hashlib.sha256(self.local_binary.read_bytes()).hexdigest()!=TIMING_SHA256:
            raise RuntimeError('Expected owned low-amplitude timing client is not staged')
        self.clock_peers['local']=Peer([str(BIN/'macbee-tone-probe'),'--serve'])
        self.clock_peers['remote']=Peer(self.ssh_command(SSH_HOST,receiver_id='remote',remote_command=shlex.join([REMOTE_ROOT+'macbee-tone-probe','--serve'])),env=batch_ssh_env())
        super().start()
        self.clock_before={host:sample_clock(peer,self.check) for host,peer in self.clock_peers.items()}

    def close(self):
        try:
            super().close()
            if self.clock_before and not self.timing_finished:
                result=dict(clock_before=self.clock_before,cleanup_verified=self.cleanup_verified)
                try:
                    after={host:sample_clock(peer,lambda:None) for host,peer in self.clock_peers.items()}
                    result['clock_after']=after
                    result['measurement']=analyze(self.logs(),self.clock_before,after)
                except Exception as error:result['measurement_error']=str(error)
                self.timing_finished=True
                if self.run_dir:(self.run_dir/'timing-metadata.json').write_text(json.dumps(result,indent=2))
                print(json.dumps(dict(event='timing_measurement',**result)),flush=True)
        finally:
            for peer in reversed(list(self.clock_peers.values())):peer.close()
            self.clock_peers={}
