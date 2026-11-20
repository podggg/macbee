"""Bounded C2 helper-only diagnostic wire format. Importing performs no IO."""
import json,re,struct

RATE=48000
MAX_FRAMES=4096
MAX_TOTAL_FRAMES=10*RATE

class PacketDecoder:
    def __init__(self):
        self.buffer=bytearray();self.ended=False;self.frames=0
        self.nonzero=0;self.peak=0;self.pulses=0;self.quiet=RATE
    def feed(self,data):
        if self.ended and data:raise ValueError('Bytes after END')
        if len(data)>65536:raise ValueError('Oversized input read')
        self.buffer.extend(data)
        payloads=[]
        while len(self.buffer)>=8:
            magic,count=struct.unpack_from('<4sI',self.buffer)
            if magic!=b'MBP1' or count>MAX_FRAMES:raise ValueError('Invalid packet header')
            size=8+count*4
            if len(self.buffer)<size:break
            if count==0:
                del self.buffer[:8];self.ended=True
                if self.buffer:raise ValueError('Bytes after END')
                break
            if self.frames+count>MAX_TOTAL_FRAMES:raise ValueError('Ten-second PCM limit')
            payload=bytes(self.buffer[8:size]);del self.buffer[:size]
            for left,right in struct.iter_unpack('<hh',payload):
                peak=max(abs(left),abs(right))
                if peak>168 or abs(left-right)>4:raise ValueError('Not the controlled low-level stereo source')
                self.peak=max(self.peak,peak)
                if peak:
                    if self.quiet>=2400:self.pulses+=1
                    self.quiet=0;self.nonzero+=1
                else:self.quiet+=1
            self.frames+=count;payloads.append(payload)
        return payloads
    def finish(self):
        if not self.ended or self.buffer:raise ValueError('Missing END or truncated packet')
        if self.pulses!=6 or not 20000<=self.nonzero<=35000 or not 100<=self.peak<=168:
            raise ValueError('Controlled six-pulse source incomplete')
        return dict(frames=self.frames,pulses=self.pulses,nonzero_frames=self.nonzero,peak=self.peak)

def null_stats(text):
    rows=[json.loads(x) for x in re.findall(r'MACBEE_STATS (\{[^\n]+\})',text)]
    if len(rows)!=1:raise ValueError('Expected one finite null-sink statistics record')
    row=rows[0]
    required=('requests','good_chunks','pattern_frames','zero_frames','other_frames')
    if any(type(row.get(k)) is not int or row[k]<0 for k in required):raise ValueError('Invalid counters')
    # The existing ramp classifier labels this mono-like sine as "other".
    # Zeros, including underrun padding, cannot meet this nonzero requirement.
    # These are counters, not waveform/sequence or acoustic timing verification.
    if row['pattern_frames'] or not 20000<=row['other_frames']<=35000 or not row['good_chunks']:
        raise ValueError('Controlled PCM not observed at null sink')
    return row
