// Original Macbee diagnostic, not a streaming/synchronization backend.
// Output only: no process tap, input stream, network socket, routing or volume change.
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <mach/mach_time.h>
#include <math.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <poll.h>

#define PULSES 6
#define AMP 0.005
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "signal-safe stop requires lock-free atomic int");
static _Atomic int interrupted;
static bool report_active, coexist_existing;
static pid_t allowed_pids[32];
static size_t allowed_count;
static mach_timebase_info_data_t timebase;
static uint64_t ns(uint64_t ticks) { return (uint64_t)((long double)ticks*timebase.numer/timebase.denom); }
static uint64_t now_ns(void) { return ns(mach_absolute_time()); }
static void stop_signal(int sig) { (void)sig; interrupted=1; }
static OSStatus get(AudioObjectID obj, AudioObjectPropertySelector sel,
                    AudioObjectPropertyScope scope, void *value, UInt32 *size) {
    AudioObjectPropertyAddress a={sel,scope,kAudioObjectPropertyElementMain};
    return AudioObjectGetPropertyData(obj,&a,0,NULL,size,value);
}
static bool u32(AudioObjectID obj, AudioObjectPropertySelector sel,
                AudioObjectPropertyScope scope, UInt32 *v) {
    UInt32 n=sizeof(*v); return get(obj,sel,scope,v,&n)==noErr;
}
static int error(const char *why, OSStatus status) {
    printf("{\"event\":\"error\",\"reason\":\"%s\",\"status\":%d}\n",why,(int)status); return 1;
}
static bool json_escape(const char *src, char *dst, size_t cap) {
    size_t pos=0;
    for(const unsigned char *p=(const unsigned char *)src; *p; p++) {
        unsigned char c=*p;
        char esc[8];
        const char *rep=NULL;
        if(c=='"') rep="\\\"";
        else if(c=='\\') rep="\\\\";
        else if(c=='\b') rep="\\b";
        else if(c=='\f') rep="\\f";
        else if(c=='\n') rep="\\n";
        else if(c=='\r') rep="\\r";
        else if(c=='\t') rep="\\t";
        else if(c<0x20 || c==0x7f) { snprintf(esc,sizeof(esc),"\\u%04x",c); rep=esc; }
        if(rep) {
            size_t len=strlen(rep);
            if(pos+len>=cap) return false;
            memcpy(dst+pos,rep,len);
            pos+=len;
        } else {
            if(pos+1>=cap) return false;
            dst[pos++]=(char)c;
        }
    }
    if(pos>=cap) return false;
    dst[pos]='\0';
    return true;
}
static int active_other_output(void) {
    AudioObjectPropertyAddress a={kAudioHardwarePropertyProcessObjectList,kAudioObjectPropertyScopeGlobal,0};
    for(int attempt=0;attempt<3;attempt++) {
        UInt32 n=0;
        OSStatus s=AudioObjectGetPropertyDataSize(kAudioObjectSystemObject,&a,0,NULL,&n);
        if(s!=noErr) {
            if(s==kAudioHardwareNotReadyError && attempt<2) continue;
            return -1;
        }
        if(n%sizeof(AudioObjectID)!=0) {
            if(attempt<2) continue;
            return -1;
        }
        if(n==0) {
            if(report_active) allowed_count=0;
            return 0;
        }
        AudioObjectID *ids=malloc(n); if(!ids) return -1;
        s=AudioObjectGetPropertyData(kAudioObjectSystemObject,&a,0,NULL,&n,ids);
        if(s!=noErr) {
            free(ids);
            if((s==kAudioHardwareBadPropertySizeError || s==kAudioHardwareNotReadyError) && attempt<2) continue;
            return -1;
        }
        UInt32 total=n/sizeof(*ids);
        pid_t *found=report_active && total?calloc(total,sizeof(pid_t)):NULL;
        if(report_active && total && !found) {free(ids);return -1;}
        UInt32 found_n=0;
        int count=0;
        bool retry=false;
        for(UInt32 i=0;i<total;i++) {
            UInt32 running=0; pid_t pid=0; UInt32 size=sizeof(pid), rsize=sizeof(running);
            OSStatus sp=get(ids[i],kAudioProcessPropertyPID,kAudioObjectPropertyScopeGlobal,&pid,&size);
            if(sp==kAudioHardwareBadObjectError) continue;
            if(sp!=noErr || size!=sizeof(pid)) {retry=true;break;}
            OSStatus sr=get(ids[i],kAudioProcessPropertyIsRunningOutput,kAudioObjectPropertyScopeGlobal,&running,&rsize);
            if(sr==kAudioHardwareBadObjectError) continue;
            if(sr!=noErr || rsize!=sizeof(running)) {retry=true;break;}
            if(running && pid!=getpid()) {
                if(report_active) found[found_n++]=pid;
                bool allowed=false;
                if(coexist_existing) for(size_t j=0;j<allowed_count;j++) if(pid==allowed_pids[j]) allowed=true;
                if(!allowed) count++;
            }
        }
        free(ids);
        if(!retry) {
            if(report_active) {
                allowed_count=0;
                for(UInt32 k=0;k<found_n;k++) {
                    fprintf(stderr,"Active output PID: %d\n",found[k]);
                    if(allowed_count<sizeof(allowed_pids)/sizeof(allowed_pids[0])) allowed_pids[allowed_count++]=found[k];
                }
            }
            free(found);
            return count;
        }
        free(found);
    }
    return -1;
}
typedef struct {
    AudioObjectID device; AudioDeviceIOProcID proc;
    double rate; uint64_t target; uint64_t onset[PULSES];
    uint64_t callbacks, invalid, last_host; UInt32 last_frames;
    double max_gap_error_ns; UInt32 device_latency,stream_latency,safety,buffer;
    UInt32 mute; Float32 volume;
} Probe;
static int inspect(Probe *p) {
    UInt32 n=sizeof(p->device),transport=0; OSStatus s;
    if((s=get(kAudioObjectSystemObject,kAudioHardwarePropertyDefaultOutputDevice,kAudioObjectPropertyScopeGlobal,&p->device,&n)) || p->device==kAudioObjectUnknown) return error("default_output",s);
    if(!u32(p->device,kAudioDevicePropertyTransportType,kAudioObjectPropertyScopeGlobal,&transport) || transport!=kAudioDeviceTransportTypeBuiltIn) return error("default_not_builtin",0);
    CFStringRef uid_ref=NULL; n=sizeof(uid_ref);
    if((s=get(p->device,kAudioDevicePropertyDeviceUID,kAudioObjectPropertyScopeGlobal,&uid_ref,&n)) || !uid_ref) {
        if(uid_ref) CFRelease(uid_ref);
        return error("device_uid",s);
    }
    char uid[512]={0}, escaped_uid[3072]={0};
    bool uid_ok=CFStringGetCString(uid_ref,uid,sizeof(uid),kCFStringEncodingUTF8);
    CFRelease(uid_ref);
    if(!uid_ok || uid[0]=='\0' || !json_escape(uid,escaped_uid,sizeof(escaped_uid))) return error("device_uid",0);
    // Refuse combined input/output devices, so an IOProc never opens a microphone.
    AudioObjectPropertyAddress ia={kAudioDevicePropertyStreams,kAudioObjectPropertyScopeInput,0};
    if((s=AudioObjectGetPropertyDataSize(p->device,&ia,0,NULL,&n))) return error("input_inventory",s);
    if(n) return error("output_device_has_input_stream",0);
    AudioStreamID stream[8]; n=sizeof(stream);
    if((s=get(p->device,kAudioDevicePropertyStreams,kAudioObjectPropertyScopeOutput,stream,&n))) return error("output_streams",s);
    if(n!=sizeof(AudioStreamID)) return error("expected_one_output_stream",0);
    AudioStreamBasicDescription fmt={0}; n=sizeof(fmt);
    if((s=get(stream[0],kAudioStreamPropertyVirtualFormat,kAudioObjectPropertyScopeGlobal,&fmt,&n))) return error("stream_format",s);
    if(fmt.mFormatID!=kAudioFormatLinearPCM || !(fmt.mFormatFlags & kAudioFormatFlagIsFloat) || fmt.mBitsPerChannel!=32 || fmt.mChannelsPerFrame!=2 || fmt.mSampleRate<44100 || fmt.mSampleRate>96000) return error("unsupported_format",0);
    p->rate=fmt.mSampleRate;
    u32(p->device,kAudioDevicePropertyLatency,kAudioObjectPropertyScopeOutput,&p->device_latency);
    u32(stream[0],kAudioStreamPropertyLatency,kAudioObjectPropertyScopeGlobal,&p->stream_latency);
    u32(p->device,kAudioDevicePropertySafetyOffset,kAudioObjectPropertyScopeOutput,&p->safety);
    u32(p->device,kAudioDevicePropertyBufferFrameSize,kAudioObjectPropertyScopeGlobal,&p->buffer);
    p->mute=UINT32_MAX; p->volume=-1;
    u32(p->device,kAudioDevicePropertyMute,kAudioObjectPropertyScopeOutput,&p->mute);
    n=sizeof(p->volume); get(p->device,kAudioDevicePropertyVolumeScalar,kAudioObjectPropertyScopeOutput,&p->volume,&n);
    report_active=true; int active=active_other_output(); report_active=false;
    printf("{\"event\":\"ready\",\"device_id\":%u,\"device_uid\":\"%s\",\"sample_rate\":%.0f,\"input_streams\":0,\"active_other_outputs\":%d,\"device_latency_frames\":%u,\"stream_latency_frames\":%u,\"safety_frames\":%u,\"buffer_frames\":%u,\"mute\":%u,\"volume\":%.4f,\"max_amplitude\":%.3f}\n",p->device,escaped_uid,p->rate,active,p->device_latency,p->stream_latency,p->safety,p->buffer,p->mute,p->volume,AMP);
    return 0;
}
static OSStatus render(AudioObjectID device,const AudioTimeStamp *now,
 const AudioBufferList *input,const AudioTimeStamp *it,AudioBufferList *output,
 const AudioTimeStamp *ot,void *context) {
    (void)device;(void)now;(void)input;(void)it;
    Probe *p=context; p->callbacks++;
    for(UInt32 b=0;b<output->mNumberBuffers;b++) if(output->mBuffers[b].mData) memset(output->mBuffers[b].mData,0,output->mBuffers[b].mDataByteSize);
    if(interrupted) return noErr;
    if(!(ot->mFlags & kAudioTimeStampHostTimeValid) || !output->mNumberBuffers) {p->invalid++;return noErr;}
    uint64_t host=ns(ot->mHostTime); UInt32 frames=0;
    for(UInt32 b=0;b<output->mNumberBuffers;b++) {
        AudioBuffer *buf=&output->mBuffers[b];
        if(!buf->mData || !buf->mNumberChannels) continue;
        UInt32 f=buf->mDataByteSize/(sizeof(float)*buf->mNumberChannels);
        if(!frames) frames=f;
        if(f!=frames) {p->invalid++;return noErr;}
    }
    if(p->last_host) {
        double gap=(double)((int64_t)host-(int64_t)p->last_host)-p->last_frames*1e9/p->rate;
        if(fabs(gap)>p->max_gap_error_ns) p->max_gap_error_ns=fabs(gap);
    }
    p->last_host=host;p->last_frames=frames;
    for(UInt32 i=0;i<frames;i++) {
        double at=(double)host+i*1e9/p->rate;
        double t=(at-(double)p->target)/1e9;
        if(t<0 || t>=PULSES) continue;
        int pulse=(int)floor(t); double phase=t-pulse;
        if(phase>=0.1) continue;
        if(!p->onset[pulse]) p->onset[pulse]=(uint64_t)at;
        double envelope=sin(M_PI*phase/0.1);
        float sample=(float)(AMP*envelope*envelope*sin(2*M_PI*880*phase));
        for(UInt32 b=0;b<output->mNumberBuffers;b++) {
            AudioBuffer *buf=&output->mBuffers[b];float *data=buf->mData;
            if(data) for(UInt32 c=0;c<buf->mNumberChannels;c++) data[i*buf->mNumberChannels+c]=sample;
        }
    }
    return noErr;
}
static bool safe_state(Probe *p) {
    AudioObjectID current=0; UInt32 size=sizeof(current),mute=UINT32_MAX;
    Float32 volume=-1; Float64 rate=0;
    if(get(kAudioObjectSystemObject,kAudioHardwarePropertyDefaultOutputDevice,kAudioObjectPropertyScopeGlobal,&current,&size) || current!=p->device) return false;
    size=sizeof(volume); if(get(p->device,kAudioDevicePropertyVolumeScalar,kAudioObjectPropertyScopeOutput,&volume,&size) || fabs(volume-p->volume)>.0001) return false;
    if(!u32(p->device,kAudioDevicePropertyMute,kAudioObjectPropertyScopeOutput,&mute) || mute!=0) return false;
    size=sizeof(rate); if(get(p->device,kAudioDevicePropertyNominalSampleRate,kAudioObjectPropertyScopeGlobal,&rate,&size) || rate!=p->rate) return false;
    return active_other_output()==0;
}
static int play(Probe *p,uint64_t target) {
    uint64_t now=now_ns();
    if(target<=now || target-now<500000000 || target-now>10000000000ULL) return error("target_not_0.5_to_10_seconds_ahead",0);
    if(!safe_state(p)) return error("preflight_changed_or_unapproved_audio",0);
    if(p->mute!=0 || p->volume<=0) return error("muted_or_volume_unavailable",0);
    p->target=target; OSStatus s=AudioDeviceCreateIOProcID(p->device,render,p,&p->proc);
    if(s) return error("create_output_ioproc",s);
    s=AudioDeviceStart(p->device,p->proc);
    if(s) {AudioDeviceDestroyIOProcID(p->device,p->proc);return error("start_output",s);}
    printf("{\"event\":\"armed\",\"target_ns\":%llu}\n",(unsigned long long)target);
    uint64_t last_check=0;
    while(!interrupted && now_ns()<target+6500000000ULL) {
        if(now_ns()-last_check>250000000) {
            last_check=now_ns();
            if(!safe_state(p)) {interrupted=1;break;}
        }
        struct pollfd fd={STDIN_FILENO,POLLIN|POLLHUP,0}; int result=poll(&fd,1,50);
        if(result>0 && fd.revents) {char line[32]; if(!fgets(line,sizeof(line),stdin) || strncmp(line,"STOP",4)==0) interrupted=1;}
    }
    OSStatus stop=AudioDeviceStop(p->device,p->proc),destroy=AudioDeviceDestroyIOProcID(p->device,p->proc);
    printf("{\"event\":\"result\",\"interrupted\":%s,\"callbacks\":%llu,\"invalid_timestamps_or_buffers\":%llu,\"max_buffer_continuity_error_ns\":%.0f,\"stop_status\":%d,\"destroy_status\":%d,\"onset_host_ns\":[",interrupted?"true":"false",(unsigned long long)p->callbacks,(unsigned long long)p->invalid,p->max_gap_error_ns,(int)stop,(int)destroy);
    for(int i=0;i<PULSES;i++) printf("%s%llu",i?",":"",(unsigned long long)p->onset[i]);
    printf("]}\n");return stop||destroy||p->invalid||interrupted;
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IONBF,0); mach_timebase_info(&timebase);
    struct sigaction action={0}; action.sa_handler=stop_signal; sigemptyset(&action.sa_mask);
    sigaction(SIGINT,&action,NULL);sigaction(SIGTERM,&action,NULL);sigaction(SIGALRM,&action,NULL);
    Probe p={0}; if(inspect(&p)) return 1;
    if(argc==2 && strcmp(argv[1],"--inspect")==0) return 0;
    if(argc!=2 || (strcmp(argv[1],"--serve")!=0 && strcmp(argv[1],"--serve-coexist")!=0)) return error("usage_inspect_or_serve",0);
    coexist_existing=strcmp(argv[1],"--serve-coexist")==0;
    alarm(90); // Maximum lifetime, including disconnected coordinator.
    while(!interrupted) {
        struct pollfd fd={STDIN_FILENO,POLLIN|POLLHUP,0};
        if(poll(&fd,1,250)<=0) continue;
        char line[128]; if(!fgets(line,sizeof(line),stdin) || interrupted) break;
        uint64_t received=now_ns();
        if(strcmp(line,"CLOCK\n")==0) printf("{\"event\":\"clock\",\"t2\":%llu,\"t3\":%llu}\n",(unsigned long long)received,(unsigned long long)now_ns());
        else if(strncmp(line,"PLAY ",5)==0) {
            char *end; unsigned long long target=strtoull(line+5,&end,10);
            if(end==line+5 || (*end!='\n' && *end!='\0')) return error("invalid_target",0);
            return play(&p,(uint64_t)target);
        } else if(strcmp(line,"STOP\n")==0) break;
        else return error("unknown_command",0);
    }
    return 0;
}
