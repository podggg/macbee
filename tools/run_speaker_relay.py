#!/usr/bin/env python3
"""Prepared C3: owned synthetic tap -> guarded speaker sinks. NOT YET RUN.
Separate owner approval is required. Never started by the frozen C2 binary.
"""
import argparse,json,os,select,signal,sys
from run_capture_relay import consume_packets,event
from speaker_transport import SpeakerTransport
from coreaudio_protocol import stats

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--controlled-pcm-stdin',required=True,action='store_true')
    parser.parse_args()
    def stop(sig,frame):raise KeyboardInterrupt('Stop requested')
    for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop)
    session=SpeakerTransport();source=None;failure=None
    try:
        session.start();event('ready',sinks=session.sinks)
        def read():
            return os.read(sys.stdin.fileno(),16384) if select.select([sys.stdin],[],[],.05)[0] else None
        source=consume_packets(read,session.write);session.drain()
    except (Exception,KeyboardInterrupt) as error:failure=str(error)
    finally:session.close()
    result=dict(source_kind='owned synthetic helper tap',source=source,sinks=session.sinks,
                cleanup_verified=session.cleanup_verified,
                not_tested=['acoustic alignment','ordinary application audio','microphone','video lip-sync','long-run drift'])
    try:
        if failure:raise RuntimeError(failure)
        if not session.cleanup_verified:raise RuntimeError('Speaker cleanup requires inspection')
        local,remote=session.logs();result.update(local=stats(local),remote=stats(remote))
        result['timing_note']='Queue validity/pulse counters are not an inter-speaker acoustic offset.'
        event('complete',**result)
    except Exception as error:result['error']=str(error);event('failed',**result)
    if session.run_dir:(session.run_dir/'result.json').write_text(json.dumps(result,indent=2))
    return 1 if 'error' in result else 0

if __name__=='__main__':sys.exit(main())
