#!/usr/bin/env python3
"""Prepared one-selected-app relay. Do not run before exact source approval.
No source PCM is written to disk. SIGTERM/EOF requests our renderers to stop;
cleanup acknowledgement comes only after they exit. No ordinary app control.
"""
import argparse,json,os,re,select,signal,sys,time
from application_protocol import ApplicationDecoder,SESSION_SECONDS,FIRST_PACKET_SECONDS,SOURCE_PACKET_SECONDS,MAX_TOTAL_FRAMES
from application_transport import ApplicationTransport
from session_diagnostics import StopTrace,RelaySignal,reason_code

def event(kind,**data):print(json.dumps(dict(event=kind,**data)),flush=True)
def run(session,read,emit=event,clock=time.monotonic,manual_session=False,trace=None):
    decoder=ApplicationDecoder(max_frames=None if manual_session else MAX_TOTAL_FRAMES);failure=None;reason='source_end';auth_receiver=None
    trace=trace or StopTrace(clock,os.environ.get('MACBEE_DIAGNOSTIC_SESSION',''))
    try:
        session.start();ready=dict(sinks=session.sinks)
        if hasattr(session,'outputs'):ready.update(outputs=session.outputs,backend_pid=os.getpid())
        emit('ready',**ready)
        ready_at=clock();deadline=ready_at+FIRST_PACKET_SECONDS;stream_started=False
        # Manual sessions have no elapsed-total budget. A live source must keep
        # delivering complete packets; zero PCM counts, incomplete bytes do not.
        duration_deadline=float("inf")
        absolute_deadline=ready_at+FIRST_PACKET_SECONDS+SESSION_SECONDS+1
        while not decoder.ended:
            trace.phase='check';trace.mark('last_loop');session.check() # Peer loss/heartbeats also run when the source is silent.
            for update in session.drain_events() if hasattr(session,'drain_events') else []:
                trace.note_output_loss(update);emit('output',**update)
            if clock()>=min(deadline,duration_deadline):
                trace.cause('selected_source_timeout');raise TimeoutError('Selected-app source deadline')
            trace.phase='read';data=read()
            if clock()>=min(deadline,duration_deadline):
                trace.cause('selected_source_timeout');raise TimeoutError('Selected-app source deadline')
            if data is None:continue
            if not data:
                if decoder.buffer:raise ValueError('Truncated selected-app packet at EOF')
                reason='source_closed';trace.cause('source_eof');break
            trace.phase='decode'
            for payload in decoder.feed(data):
                trace.mark('last_pcm')
                if not stream_started:
                    stream_started=True
                    if not manual_session:duration_deadline=min(clock()+SESSION_SECONDS+1,absolute_deadline)
                deadline=clock()+SOURCE_PACKET_SECONDS
                trace.phase='write';session.write(payload);trace.mark('last_output')
        # Deliberately no extra drain/zero-tail. Await actual child exit before ACK.
    except KeyboardInterrupt as error:reason='stop_requested';trace.cause(reason_code(error,trace.phase))
    except Exception as error:
        failure=str(error);trace.note_transport(session,error);trace.cause(reason_code(error,trace.phase))
        if getattr(error,'code',None)=='auth_required':
            candidate=getattr(error,'receiver_id','') or getattr(session,'auth_required_receiver','') or ''
            if isinstance(candidate,str) and re.fullmatch(r'remote|r-[a-f0-9]{16}',candidate):auth_receiver=candidate
    finally:
        trace.cause('source_end');decoder.clear()
        # Protect the early failure notice as well as cleanup from repeated Stop.
        previous={sig:signal.signal(sig,signal.SIG_IGN) for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
        try:
            # Native owner suspends capture before potentially slow teardown.
            # The verified ACK still comes only after both renderers have exited.
            stopping_fields=dict(reason=reason,error=failure,reason_code=trace.first)
            if auth_receiver:stopping_fields['auth_required_receiver']=auth_receiver
            try:emit('stopping',**stopping_fields)
            except (BrokenPipeError,OSError):pass
            trace.phase='cleanup'
            try:session.close()
            except Exception as error:failure=failure or str(error)
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
    result=dict(reason=reason,source=decoder.statistics(),cleanup_verified=session.cleanup_verified,
                sinks=session.sinks)
    detail=getattr(session,'cleanup_error',None)
    if detail:failure=(failure+'. '+detail) if failure else detail
    if not session.cleanup_verified:failure=failure or 'Renderer cleanup is unconfirmed'
    if failure:result['error']=failure
    if auth_receiver:result['auth_required_receiver']=auth_receiver
    result['reason_code']=trace.first;trace.save(getattr(session,'run_dir',None));trace.save(getattr(session,'run_dir',None),final=True)
    emit('failed' if failure else 'stopped',**result)
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--selected-app-stdin',required=True,action='store_true')
    parser.add_argument('--manual-session',action='store_true');parser.add_argument('--outputs');parser.add_argument('--ssh-control-paths');args=parser.parse_args()
    if args.outputs and not args.manual_session:parser.error('Configured outputs require explicit manual policy')
    control_paths=None
    if args.ssh_control_paths:
        from ssh_control import parse_control_paths_json
        control_paths=parse_control_paths_json(args.ssh_control_paths)
    def stop(sig,frame):raise RelaySignal(sig)
    for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop)
    def read():return os.read(sys.stdin.fileno(),16384) if select.select([sys.stdin],[],[],.05)[0] else None
    if os.environ.get('MACBEE_OWNED_MARKER_TIMING')=='1':
        if args.manual_session:parser.error('Manual sessions cannot use the finite marker diagnostic')
        # Explicit diagnostic launch only. The default selected-app path is unchanged.
        from application_timing import TimedApplicationTransport
        session=TimedApplicationTransport()
    elif args.outputs:
        from configured_transport import ConfiguredTransport
        session=ConfiguredTransport(args.outputs.split(','),source_policy='selected-app')
    else:session=ApplicationTransport(manual_session=args.manual_session)
    if control_paths is not None:session.control_paths=control_paths
    result=run(session,read,manual_session=args.manual_session)
    return int('error' in result)
if __name__=='__main__':sys.exit(main())
