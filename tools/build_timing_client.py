#!/usr/bin/env python3
"""Compile-only marker-to-native-queue timestamp diagnostic; no input or capture.
Uses a separate copy/object; frozen capture/playback apps and clients stay intact.
"""
import hashlib,json,pathlib,shlex
from build_backend import ROOT,SOURCE,guard,run
from relay_runtime import ROOT as RUNTIME,BIN,MAX_BYTES,guard as runtime_guard
from build_application_client import BASE_SHA

def prepared_source(text):
    text=text.replace('#include <CoreAudio/CoreAudio.h>','#include <CoreAudio/CoreAudio.h>\n#include <mach/mach_time.h>\n#include <iomanip>\n#include <cmath>')
    text=text.replace('    AudioTimeStamp timestamp{};','    AudioTimeStamp timestamp{};\n    Boolean mb_discontinuity=false;')
    text=text.replace('AudioQueueGetCurrentTime(queue,timeLine_,&timestamp,NULL)','AudioQueueGetCurrentTime(queue,timeLine_,&timestamp,&mb_discontinuity)')
    old='    if(AudioQueueEnqueueBuffer(queue,bufferRef,0,NULL)!=noErr) ++mb_enqueue_errors_;'
    assert text.count(old)==1
    replacement=r'''    AudioTimeStamp mb_start{};
    OSStatus mb_enqueue=AudioQueueEnqueueBufferWithParameters(queue,bufferRef,0,NULL,0,0,0,NULL,NULL,&mb_start);
    if(mb_enqueue!=noErr) ++mb_enqueue_errors_;
    if(!mb_priming_ && mb_enqueue==noErr && pubStream_->getFormat().bits()==16 && pubStream_->getFormat().channels()==2) {
        static mach_timebase_info_data_t mb_base={0,0};
        if(!mb_base.denom) mach_timebase_info(&mb_base);
        const int16_t mb_prefix[7]={-120,77,-33,110,-85,44,-17};
        for(size_t i=0;i+8<=frames_;i++) {
            bool match=true;
            for(size_t j=0;j<7 && match;j++) {int16_t values[2];memcpy(values,buffer+(i+j)*4,4);match=values[0]==mb_prefix[j] && values[1]==mb_prefix[j];}
            if(!match) continue;
            int16_t id_sample[2];memcpy(id_sample,buffer+(i+7)*4,4);
            if(id_sample[0]!=id_sample[1] || id_sample[0]<13 || id_sample[0]>78 || id_sample[0]%13) continue;
            long double ticks=0;const char *mapping="unavailable";
            if(mb_start.mFlags & kAudioTimeStampHostTimeValid) {
                ticks=mb_start.mHostTime+(long double)i/48000.0L*1000000000.0L*mb_base.denom/mb_base.numer;
                mapping="enqueue_host";
            } else if((mb_start.mFlags & kAudioTimeStampSampleTimeValid) &&
              (timestamp.mFlags & kAudioTimeStampSampleHostTimeValid)==kAudioTimeStampSampleHostTimeValid && !mb_discontinuity) {
                ticks=timestamp.mHostTime+((long double)mb_start.mSampleTime+i-timestamp.mSampleTime)/48000.0L*1000000000.0L*mb_base.denom/mb_base.numer;
                mapping="queue_sample_host_anchor";
            }
            AudioTimeStamp mb_device{},mb_in{},mb_translated{};
            OSStatus mb_device_status=AudioQueueDeviceGetCurrentTime(queue,&mb_device);
            mb_in.mFlags=kAudioTimeStampHostTimeValid;mb_in.mHostTime=ticks>0?(uint64_t)llroundl(ticks):0;
            mb_translated.mFlags=kAudioTimeStampSampleHostTimeValid;
            OSStatus mb_translate=ticks>0?AudioQueueDeviceTranslateTime(queue,&mb_in,&mb_translated):-1;
            uint64_t host_ns=ticks>0?(uint64_t)llroundl(ticks*mb_base.numer/mb_base.denom):0;
            LOG(INFO,LOG_TAG) << std::setprecision(17) << "MACBEE_MARKER {\"id\":" << id_sample[0]/13
              << ",\"source_frame\":" << (id_sample[0]/13-1)*48000
              << ",\"buffer_offset\":" << i << ",\"host_ns\":" << host_ns
              << ",\"mapping\":\"" << mapping << "\",\"enqueue_flags\":" << mb_start.mFlags
              << ",\"enqueue_sample\":" << mb_start.mSampleTime << ",\"enqueue_host_ticks\":" << mb_start.mHostTime
              << ",\"queue_flags\":" << timestamp.mFlags << ",\"queue_sample\":" << timestamp.mSampleTime
              << ",\"queue_host_ticks\":" << timestamp.mHostTime << ",\"discontinuity\":" << (int)mb_discontinuity
              << ",\"device_status\":" << mb_device_status << ",\"device_flags\":" << mb_device.mFlags
              << ",\"device_sample\":" << mb_device.mSampleTime << ",\"device_host_ticks\":" << mb_device.mHostTime
              << ",\"translate_status\":" << mb_translate << ",\"translated_flags\":" << mb_translated.mFlags
              << ",\"translated_sample\":" << mb_translated.mSampleTime << "}\n";
            i+=7;
        }
    }'''
    return text.replace(old,replacement)

def main():
    guard();runtime_guard(verify=True)
    folder=ROOT/'timing-renderer';folder.mkdir(exist_ok=True)
    original=SOURCE/'client/player/coreaudio_player.cpp';raw=original.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==BASE_SHA
    cpp=folder/'coreaudio_player.cpp';cpp.write_text(prepared_source(raw.decode()))
    obj=folder/'coreaudio_player.cpp.o';binary=folder/'snapclient-timing'
    flags={}
    for line in (ROOT/'build/client/CMakeFiles/snapclient.dir/flags.make').read_text().splitlines():
        if ' = ' in line:
            key,value=line.split(' = ',1);flags[key]=shlex.split(value)
    run(['/usr/bin/c++']+flags['CXX_DEFINES']+flags['CXX_INCLUDES']+flags['CXX_FLAGS']+['-I'+str(SOURCE/'client/player'),'-c',str(cpp),'-o',str(obj)],'timing-compile.log')
    link=shlex.split((ROOT/'build/client/CMakeFiles/snapclient.dir/link.txt').read_text())
    old='CMakeFiles/snapclient.dir/player/coreaudio_player.cpp.o';assert link.count(old)==1
    link[link.index(old)]=str(obj);link[link.index('-o')+1]=str(binary)
    link=[str(ROOT/'build/client'/x) if x.startswith('CMakeFiles/') else x for x in link]
    run(link,'timing-link.log');assert original.read_bytes()==raw
    usage,_=runtime_guard(verify=True);assert usage+binary.stat().st_size+8192<MAX_BYTES
    destination=BIN/binary.name;assert not destination.exists()
    with destination.open('xb') as out:out.write(binary.read_bytes())
    destination.chmod(0o500)
    manifest=dict(sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),base_source_sha256=BASE_SHA,
                  patched_source_sha256=hashlib.sha256(cpp.read_bytes()).hexdigest(),executed=False)
    (RUNTIME/'timing-client.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest))
    print('runtime bytes/free',runtime_guard(verify=True))
if __name__=='__main__':main()
