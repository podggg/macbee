#!/usr/bin/env python3
"""Small auditable instrumentation for the approved synthetic /dev/null test.
Preserves upstream GPL headers. Changes no backend selection or output destination.
Only counters of a known synthetic pattern are logged, never PCM samples.
"""
import pathlib
from local_config import BACKEND_ROOT
ROOT=BACKEND_ROOT/'snapcast-0.35.0'

def replace(path,old,new):
    text=path.read_text()
    if text.count(old)!=1:raise RuntimeError('Unexpected upstream text: '+str(path))
    path.write_text(text.replace(old,new))

if __name__=='__main__':
    replace(ROOT/'client/player/file_player.hpp',
        '    std::shared_ptr<::FILE> file_;',
        '''    std::shared_ptr<::FILE> file_;
    // Macbee's bounded, synthetic-only null-sink diagnostic counters.
    uint64_t mb_requests_=0, mb_good_chunks_=0, mb_valid_=0, mb_zero_=0, mb_other_=0;
    bool mb_reported_=false;''')
    replace(ROOT/'client/player/file_player.cpp',
        '    if (!stream_->getPlayerChunkOrSilence(buffer_.data(), 10ms, numFrames))',
        '''    const bool mb_good=stream_->getPlayerChunkOrSilence(buffer_.data(), 10ms, numFrames);
    ++mb_requests_;
    if(mb_good) ++mb_good_chunks_;
    if (!mb_good)''')
    replace(ROOT/'client/player/file_player.cpp',
        '    if (file_)\n    {\n        fwrite',
        '''    // Test source is stereo signed 16-bit: L=8*(1..127), R=-L.
    // Zero padding cannot satisfy it. Never retain raw sample buffers or log them.
    if(stream_->getFormat().bits()==16 && stream_->getFormat().channels()==2) {
        for(uint32_t i=0;i<numFrames;i++) {
            int16_t left=0,right=0;
            memcpy(&left,buffer_.data()+i*4,2);
            memcpy(&right,buffer_.data()+i*4+2,2);
            if(left==0 && right==0) ++mb_zero_;
            else if(left>=8 && left<=1016 && left%8==0 && right==-left) ++mb_valid_;
            else ++mb_other_;
        }
    }
    if (file_)
    {
        fwrite''')
    replace(ROOT/'client/player/file_player.cpp',
        '    timer_.cancel();',
        '''    timer_.cancel();
    if(!mb_reported_) {
        mb_reported_=true;
        LOG(INFO, LOG_TAG) << "MACBEE_STATS {\\"requests\\":" << mb_requests_
            << ",\\"good_chunks\\":" << mb_good_chunks_ << ",\\"pattern_frames\\":" << mb_valid_
            << ",\\"zero_frames\\":" << mb_zero_ << ",\\"other_frames\\":" << mb_other_ << "}\\n";
    }''')
