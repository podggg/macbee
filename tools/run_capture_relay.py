#!/usr/bin/env python3
"""C2 backend. Live captured stdin is a separate, explicitly approved test gate.
--fixture uses generated memory samples only; it never opens a capture API.
No source samples are saved to any file or log. Both sinks are /dev/null.
"""
import argparse,json,math,os,select,signal,struct,sys,time
from capture_protocol import PacketDecoder,null_stats
from null_transport import NullTransport

def event(kind,**data):print(json.dumps(dict(event=kind,**data)),flush=True)
def fixture_packets():
    # Same six 880 Hz, 100 ms, sine-squared tones; no helper/audio device.
    for chunk in range(325):
        payload=bytearray()
        for i in range(960):
            frame=chunk*960+i;within=frame%48000
            value=round(163*math.sin(2*math.pi*880*within/48000)*math.sin(math.pi*within/4800)**2) if frame<288000 and within<4800 else 0
            payload.extend(struct.pack('<hh',value,value))
        yield struct.pack('<4sI',b'MBP1',960)+payload
    yield struct.pack('<4sI',b'MBP1',0)

def consume_packets(read,write,clock=time.monotonic):
    decoder=PacketDecoder();deadline=clock()+12
    while not decoder.ended:
        if clock()>deadline:raise TimeoutError('Twelve-second source deadline')
        data=read()
        if data is None:continue
        if not data:raise ValueError('Source closed before END')
        for payload in decoder.feed(data):write(payload)
    return decoder.finish()

def main():
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--fixture',action='store_true')
    mode.add_argument('--controlled-pcm-stdin',action='store_true')
    args=parser.parse_args()
    def stop(sig,frame):raise KeyboardInterrupt('Stop requested')
    for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop)
    session=NullTransport();source=None;failure=None
    try:
        session.start();event('ready',sinks=['FilePlayer -> /dev/null']*2)
        if args.fixture:
            packets=iter(fixture_packets());start=time.monotonic();index=0
            def read():
                nonlocal index
                time.sleep(max(0,start+index*.02-time.monotonic()));index+=1
                return next(packets,b'')
        else:
            def read():
                return os.read(sys.stdin.fileno(),16384) if select.select([sys.stdin],[],[],.05)[0] else None
        source=consume_packets(read,session.write);session.drain()
    except (Exception,KeyboardInterrupt) as error:failure=str(error)
    finally:session.close()
    result=dict(source_kind='generated fixture, no capture' if args.fixture else 'owned synthetic helper tap',
                source=source,cleanup_verified=session.cleanup_verified,
                sinks=['FilePlayer -> /dev/null']*2,
                not_tested=['CoreAudio output estimator','acoustic alignment','ordinary application audio','microphone'])
    try:
        if failure:raise RuntimeError(failure)
        if not session.cleanup_verified:raise RuntimeError('Cleanup/exit status needs inspection')
        local,remote=session.logs();result.update(local=null_stats(local),remote=null_stats(remote))
        result['counter_note']='Nonzero counts distinguish PCM from zero padding, not bitwise equality, loss, clock residual or acoustic offset.'
        event('complete',**result)
    except Exception as error:
        result['error']=str(error);event('failed',**result)
    if session.run_dir:(session.run_dir/'result.json').write_text(json.dumps(result,indent=2))
    return 1 if 'error' in result else 0

if __name__=='__main__':sys.exit(main())
