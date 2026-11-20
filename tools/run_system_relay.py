"""Explicit System Audio session. Only our PCM stdin is consumed. No capture API.
Health frames keep silent sessions alive; renderers retain a short owner lease.
"""
import argparse,collections,json,os,re,select,signal,sys,time
from reader_clock import shared_clock_ns
from system_audio_protocol import SystemAudioDecoder
from system_audio_transport import SystemAudioTransport
from session_diagnostics import StopTrace,RelaySignal,reason_code
# One normal quantum and at most one overdue quantum before polling input again.
OUTPUT_PERIOD = .020
OUTPUT_BYTES = 3840
MAX_OUTPUTS_PER_TURN = 2
MAX_PACING_LATE = .250
# Keep sender skip + up to 3 cutover-drain slots + next slot_header() <= 35 slots
# and below the Snapserver V3 720ms autonomous-bridge hard bound.
MAX_RECOVERY_LATE = .580
MAX_SKIPPED_SLOTS = 34
RECOVERY_BUDGET = 4
RECOVERY_WINDOW = 10.
# One maximum 16,384-byte native packet can straddle the 250ms debt cap.
MAX_SOURCE_DEBT = 48000


def request_relay_qos():
    """Request policy on the Darwin thread that will run the sender loop."""
    if sys.platform!='darwin':return
    import ctypes
    lib=ctypes.CDLL(None)
    if lib.qos_class_self() < 0x19:  # QOS_CLASS_USER_INITIATED in sys/qos.h
        lib.pthread_set_qos_class_self_np(0x19,0)  # advisory; never stop working audio for a scheduling hint


def event(kind,**data):print(json.dumps(dict(event=kind,**data)),flush=True)

def run(session,read,emit=event,clock=time.monotonic,trace=None):
    decoder=SystemAudioDecoder(stamp_clock=shared_clock_ns);pending=bytearray();failure=None;reason='source_end';padding=0;state=None;auth_receiver=None
    trace=trace or StopTrace(clock,os.environ.get('MACBEE_DIAGNOSTIC_SESSION',''));max_gap=0;discarded=0;recoveries=0
    incidents=collections.deque(maxlen=RECOVERY_BUDGET);source_debt=False;await_fresh_until=None;short_wait=None;degraded_until=0;last_inserted=0;skipped_since_write=0
    def skip_due(now,through):
        nonlocal next_output,skipped_since_write
        count=max(0,int((now-next_output)//OUTPUT_PERIOD)+(1 if through else 0)) if now>=next_output else 0
        if skipped_since_write+count>MAX_SKIPPED_SLOTS:raise RuntimeError('System output pacing stalled')
        progress=getattr(session,'reader_clock',None)
        if progress is not None and hasattr(progress,'skip_slots'):progress.skip_slots(count)
        skipped_since_write+=count
        next_output+=count*OUTPUT_PERIOD
    def degraded(now):
        nonlocal degraded_until
        if now>=degraded_until:emit('state',state='degraded')
        degraded_until=now+2.0
    def reanchor(now,late,keep_tail=False):
        nonlocal next_output,discarded,recoveries,source_debt,await_fresh_until
        if late>MAX_RECOVERY_LATE:raise RuntimeError('System output pacing stalled')
        while incidents and now-incidents[0]>=RECOVERY_WINDOW:incidents.popleft()
        if len(incidents)>=RECOVERY_BUDGET:raise RuntimeError('Repeated sender pacing interruptions')
        incidents.append(now);recoveries+=1;degraded(now)
        if not keep_tail:
            discarded+=len(pending)//4;pending.clear()
            decoder.discard_through_now()
        # Never manufacture a second hole with a zero-filled catch-up burst.
        # Start a new sender period; the server alone owns missing media slots.
        skip_due(now,True);decoder.reanchor=False;source_debt=False
        wait_slots=min(3,max(0,MAX_SKIPPED_SLOTS-1-skipped_since_write))
        await_fresh_until=None if (keep_tail or not wait_slots) else now+wait_slots*OUTPUT_PERIOD
        trace.mark('pacing_recoveries',recoveries)
        trace.mark('discarded_source_frames',discarded+decoder.dropped_frames)

    try:
        session.start();emit('ready',outputs=session.outputs,sinks=session.sinks,renderer=session.renderer_identity,backend_pid=os.getpid())
        bounded=getattr(session,'bounded_pacing',False);decoder.require_stamps=bounded
        now=clock();last_loop=now;health_deadline=now+3;prepare_deadline=now+60;next_output=now+OUTPUT_PERIOD
        while not decoder.ended:
            trace.phase='check';now=clock();max_gap=max(max_gap,now-last_loop);last_loop=now
            trace.mark('last_loop',now);trace.mark('max_loop_gap',max_gap)
            try:session.check()
            finally:trace.stage_time('check',now,clock())
            now=clock()
            inserted=getattr(getattr(session,'reader_clock',None),'inserted',0)
            if inserted>last_inserted:
                degraded(now);last_inserted=inserted
            for update in session.drain_events() if hasattr(session,'drain_events') else []:
                trace.note_output_loss(update);emit('output',**update)
            if now>=health_deadline:raise TimeoutError('System source health lease expired')
            if not decoder.active and now>=prepare_deadline:raise TimeoutError('System source preparation timed out')
            trace.phase='read';began=clock()
            try:data=read()
            finally:trace.stage_time('read',began,clock())
            if data==b'':
                if decoder.buffer:raise ValueError('Truncated system source packet')
                reason='source_closed';trace.cause('source_eof');break
            if data:
                trace.phase='decode';began=clock()
                cpu=time.thread_time()
                try:packets=decoder.feed(data)
                finally:
                    trace.stage_time('decode',began,clock());trace.stage_time('decode_cpu',cpu,time.thread_time())
                if decoder.dropped_frames:trace.mark('discarded_source_frames',discarded+decoder.dropped_frames)
                for kind,payload in packets:
                    if kind=='health':
                        health_deadline=clock()+3;trace.mark('last_health')
                    else:
                        pending.extend(payload);trace.mark('last_pcm');trace.mark('pending_bytes',len(pending))
                        if len(pending)>(MAX_SOURCE_DEBT if bounded else 65536):
                            if not bounded:raise RuntimeError('System source backlog exceeded its bound')
                            # Retain a contiguous recent tail, never a partial stereo frame.
                            cut=len(pending)-2*OUTPUT_BYTES
                            del pending[:cut];source_debt=True;discarded+=cut//4;trace.mark('discarded_source_frames',discarded+decoder.dropped_frames)

            now=clock();max_gap=max(max_gap,now-last_loop);trace.mark('max_loop_gap',max_gap)
            if decoder.ended:break  # END never drains an extra block.
            if bounded and (decoder.reanchor or source_debt):
                gap=now-next_output
                reanchor(now,gap,keep_tail=source_debt and not decoder.reanchor and gap<=MAX_PACING_LATE)
                continue
            if await_fresh_until is not None:
                # While draining pre-cutover packets, let the existing server
                # bridge finish the *same* gap. Do not queue new padding behind it.
                # A healthy but actually silent native source resumes normal
                # silent output after three slots rather than timing out.
                if not pending and now<await_fresh_until:continue
                skip_due(now,False);await_fresh_until=None
            for block in range(MAX_OUTPUTS_PER_TURN):
                # The previous write may have blocked: check the clock anew before
                # each due packet. Only a bounded V3 gap may re-anchor sender work;
                # the server presentation timeline never moves or burst-drains.
                now=clock()
                if now<next_output:break
                if now>=health_deadline:raise TimeoutError('System source health lease expired')
                if not decoder.active and now>=prepare_deadline:raise TimeoutError('System source preparation timed out')
                late=now-next_output
                trace.mark('pacing_late',late)
                if late>MAX_PACING_LATE:
                    if not bounded:raise RuntimeError('System output pacing stalled')
                    reanchor(now,late)
                    break
                if bounded and decoder.active and len(pending)<OUTPUT_BYTES and state=='playing':
                    # A real packet can be a few ms behind a due slot after
                    # catch-up. Do not irrevocably fill it with zero immediately.
                    if short_wait is None:short_wait=now
                    if now-short_wait<OUTPUT_PERIOD:break
                short_wait=None
                count=min(OUTPUT_BYTES,len(pending));chunk=bytes(pending[:count]);del pending[:count]
                padding+=(OUTPUT_BYTES-count)//4;chunk+=bytes(OUTPUT_BYTES-count)
                trace.phase='write';began=clock()
                try:session.write(chunk)
                finally:trace.stage_time('write',began,clock())
                skipped_since_write=0
                trace.mark('last_output');trace.mark('pending_bytes',len(pending))
                if block:trace.catchup_write()
                next_output+=OUTPUT_PERIOD
                current='preparing' if not decoder.active else ('playing' if any(chunk) else 'waiting')
                changed=current!=state;state=current
                if degraded_until and now>=degraded_until:
                    degraded_until=0;changed=True
                if changed and not degraded_until:emit('state',state=state)
    except KeyboardInterrupt as error:reason='stop_requested';trace.cause(reason_code(error,trace.phase))
    except Exception as error:
        failure=str(error);trace.note_transport(session,error);trace.cause(reason_code(error,trace.phase))
        if getattr(error,'code',None)=='auth_required':
            candidate=getattr(error,'receiver_id','') or getattr(session,'auth_required_receiver','') or ''
            if isinstance(candidate,str) and re.fullmatch(r'remote|r-[a-f0-9]{16}',candidate):auth_receiver=candidate
    finally:
        trace.cause('source_end')
        decoder.clear();pending[:]=bytes(len(pending));pending.clear()
        previous={s:signal.signal(s,signal.SIG_IGN) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
        try:
            stopping_fields=dict(reason=reason,error=failure,reason_code=trace.first,diagnostic=trace.first_snapshot)
            if auth_receiver:stopping_fields['auth_required_receiver']=auth_receiver
            try:emit('stopping',**stopping_fields)
            except (BrokenPipeError,OSError):pass
            trace.phase='cleanup'
            try:session.close()
            except Exception as error:failure=failure or str(error)
        finally:
            for s,h in previous.items():signal.signal(s,h)
    if not session.cleanup_verified:failure=failure or 'Selected renderer cleanup is unconfirmed'
    if getattr(session,'cleanup_error',None):failure=(failure+'. ' if failure else '')+session.cleanup_error
    result=dict(reason=reason,source=decoder.pcm.statistics(),padding_frames=padding,cleanup_verified=session.cleanup_verified,sinks=session.sinks)
    if failure:result['error']=failure
    if auth_receiver:result['auth_required_receiver']=auth_receiver
    result.update(pacing_recoveries=recoveries,discarded_source_frames=discarded+decoder.dropped_frames)
    result['reason_code']=trace.first
    trace.save(getattr(session,'run_dir',None));trace.save(getattr(session,'run_dir',None),final=True)
    emit('failed' if failure else 'stopped',**result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--system-audio-stdin',action='store_true',required=True);p.add_argument('--outputs',required=True);p.add_argument('--configured-outputs',action='store_true');p.add_argument('--ssh-control-paths');args=p.parse_args()
    control_paths=None
    if args.ssh_control_paths:
        from ssh_control import parse_control_paths_json
        control_paths=parse_control_paths_json(args.ssh_control_paths)
    def stop(sig,frame):raise RelaySignal(sig)
    for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop)
    def read():return os.read(0,16384) if select.select([0],[],[],.005)[0] else None
    if args.configured_outputs:
        from configured_transport import ConfiguredTransport
        session=ConfiguredTransport(args.outputs.split(','))
        request_relay_qos()
    else:session=SystemAudioTransport(args.outputs.split(','))
    if control_paths is not None:session.control_paths=control_paths
    return int('error' in run(session,read))
if __name__=='__main__':raise SystemExit(main())
