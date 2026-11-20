"""System source control + PCM. No IO, capture, receiver lookup or sample storage."""
import struct,sys,time

def capture_clock_ns():
    # CoreAudio mHostTime uses Mach absolute ticks (sleep-excluding).
    return time.clock_gettime_ns(time.CLOCK_UPTIME_RAW if sys.platform=="darwin" else time.CLOCK_MONOTONIC)
from application_protocol import ApplicationDecoder,MAX_FRAMES
class SystemAudioDecoder:
    def __init__(self, stamp_clock=None, host_clock=capture_clock_ns):
        self.stamp_clock=stamp_clock;self.dropped_frames=0;self.last_stamp=0;self.require_stamps=False
        self.clocks={b'MBP3':stamp_clock,b'MBP4':host_clock};self.domain=None;self.cutoffs={}
        self.drop_through=0;self.reanchor=False
        self.buffer=bytearray();self.pcm=ApplicationDecoder(max_frames=None)
        self.ended=False;self.active=False
    def discard_through_now(self):
        # Snapserver uses continuous time; a captured PCM stamp uses uptime.
        # Never add a guessed sleep offset or compare clocks with different origins.
        if self.domain:
            self.drop_through=max(self.drop_through,self.clocks[self.domain]())
        else:
            self.cutoffs={domain:clock() for domain,clock in self.clocks.items() if clock}
    def clear(self):
        self.buffer[:]=bytes(len(self.buffer));self.buffer.clear();self.pcm.clear()
    def feed(self,data):
        if len(data)>65536 or (self.ended and data):raise ValueError('Invalid system source read')
        self.buffer.extend(data);events=[]
        try:
            while len(self.buffer)>=8:
                magic,count=struct.unpack_from('<4sI',self.buffer)
                if magic==b'MBH2':
                    if count not in (1,2) or (self.active and count==1):raise ValueError('Invalid source health phase')
                    self.active=self.active or count==2;del self.buffer[:8];events.append(('health',count));continue
                stamped=magic in self.clocks
                if (magic!=b'MBP2' and not stamped) or count>MAX_FRAMES or (stamped and not count):raise ValueError('Invalid system PCM packet')
                if count and self.require_stamps and not stamped:raise ValueError('Missing system packet timestamp')
                header=16 if stamped else 8
                size=header+4*count
                if len(self.buffer)<size:break
                if count and not self.active:raise ValueError('PCM before verified source activation')
                if count:
                    stamp=struct.unpack_from('<Q',self.buffer,8)[0] if stamped else None
                    if stamped:
                        if not self.clocks[magic]:raise ValueError('Unexpected stamped PCM packet')
                        if self.domain is None:
                            self.domain=magic;self.drop_through=self.cutoffs.get(magic,0)
                        elif self.domain!=magic:raise ValueError('PCM capture time domain changed')
                        now=self.clocks[magic]()
                        if not stamp or stamp<self.last_stamp or stamp>now:raise ValueError('Invalid source packet time')
                        self.last_stamp=stamp
                    payload=bytes(self.buffer[header:size]);del self.buffer[:size]
                    if stamped and stamp>self.drop_through and now-stamp>250_000_000:
                        self.drop_through=now;self.reanchor=True
                    if stamped and stamp<=self.drop_through:
                        self.dropped_frames+=count
                        continue
                    self.pcm.record(payload,count);events.append(('pcm',payload))
                else:
                    del self.buffer[:size];self.ended=True;self.pcm.ended=True
                    if self.buffer:raise ValueError('Bytes after END')
                    break
            return events
        except BaseException:self.clear();raise
