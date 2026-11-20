"""Bounded full-range PCM packets for one selected app. Pure parsing, no IO."""
import array,struct,sys
RATE=48000
MAX_FRAMES=4096
SESSION_SECONDS=30*60 # Legacy/diagnostic ceiling only; manual sessions opt out.
FIRST_PACKET_SECONDS=12
SOURCE_PACKET_SECONDS=5 # Complete packets, including digital silence, renew liveness.
RENDERER_LEASE_SECONDS=3
RENDERER_MAX_SECONDS=SESSION_SECONDS+30
MAX_TOTAL_FRAMES=SESSION_SECONDS*RATE
class ApplicationDecoder:
    def __init__(self,max_frames=MAX_TOTAL_FRAMES):
        self.max_frames=max_frames
        self.buffer=bytearray();self.ended=False;self.frames=0;self.nonzero=0;self.peak=0
    def clear(self):
        self.buffer[:]=bytes(len(self.buffer));self.buffer.clear()
    def feed(self,data):
        if self.ended and data:raise ValueError('Bytes after END')
        if len(data)>65536:raise ValueError('Oversized input read')
        self.buffer.extend(data);payloads=[]
        try:
            while len(self.buffer)>=8:
                magic,count=struct.unpack_from('<4sI',self.buffer)
                if magic!=b'MBP2' or count>MAX_FRAMES:raise ValueError('Invalid selected-app packet')
                size=8+4*count
                if len(self.buffer)<size:break
                if not count:
                    del self.buffer[:8];self.ended=True
                    if self.buffer:raise ValueError('Bytes after END')
                    break
                if self.max_frames is not None and self.frames+count>self.max_frames:raise ValueError('Selected-app PCM duration limit')
                payload=bytes(self.buffer[8:size]);del self.buffer[:size]
                self.record(payload,count);payloads.append(payload)
            return payloads
        except BaseException:
            self.clear();raise
    def record(self,payload,count):
        # Avoid Python callbacks per stereo frame on the time-critical sender.
        # A zero 32-bit stereo word is unique irrespective of system byte order.
        frames=array.array('i',payload)
        samples=array.array('h',payload)
        if frames.itemsize!=4 or samples.itemsize!=2:raise ValueError('Unsupported PCM word size')
        if sys.byteorder!='little':samples.byteswap()
        self.nonzero+=count-frames.count(0)
        self.peak=max(self.peak,abs(min(samples)),abs(max(samples)))
        self.frames+=count
    def statistics(self):
        return dict(frames=self.frames,nonzero_frames=self.nonzero,peak_int16=self.peak)
