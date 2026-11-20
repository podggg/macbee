/* Output-only controlled helper. Zero until ARM; never captures or changes settings. */
#include <AudioToolbox/AudioToolbox.h>
#include <CoreAudio/CoreAudio.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#define FRAMES 960
static volatile sig_atomic_t stopping;
static _Atomic bool armed,queueStopping;
static uint64_t position,nonzero,callbacks;
static int peak;
static double monotonic(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void stop_signal(int sig){(void)sig;stopping=1;}
static int16_t value_at(uint64_t frame){
    if(frame>=6*48000)return 0;
    double phase=(frame%48000)/48000.;
    return phase<.1?(int16_t)lround(163*pow(sin(M_PI*phase/.1),2)*sin(2*M_PI*880*phase)):0;
}
static void fill(void *context,AudioQueueRef queue,AudioQueueBufferRef buffer){
    (void)context;int16_t *out=buffer->mAudioData;
    bool play=atomic_load_explicit(&armed,memory_order_acquire);callbacks++;
    for(int i=0;i<FRAMES;i++){
        int16_t value=play?value_at(position++):0;
        out[i*2]=out[i*2+1]=value;
        if(value)nonzero++;int mag=value<0?-value:value;if(mag>peak)peak=mag;
    }
    buffer->mAudioDataByteSize=FRAMES*4;
    if(!atomic_load(&queueStopping) && AudioQueueEnqueueBuffer(queue,buffer,0,NULL)!=noErr)atomic_store(&queueStopping,true);
}
static OSStatus get(AudioObjectID object,AudioObjectPropertySelector property,AudioObjectPropertyScope scope,void *data,UInt32 size){
    AudioObjectPropertyAddress a={property,scope,kAudioObjectPropertyElementMain};return AudioObjectGetPropertyData(object,&a,0,NULL,&size,data);
}
static bool inspect(AudioObjectID *device,Float32 *volume,UInt32 *mute){
    UInt32 transport=0;Float64 rate=0;
    if(get(kAudioObjectSystemObject,kAudioHardwarePropertyDefaultOutputDevice,kAudioObjectPropertyScopeGlobal,device,sizeof(*device)))return false;
    if(get(*device,kAudioDevicePropertyTransportType,kAudioObjectPropertyScopeGlobal,&transport,sizeof(transport)) || transport!=kAudioDeviceTransportTypeBuiltIn)return false;
    if(get(*device,kAudioDevicePropertyNominalSampleRate,kAudioObjectPropertyScopeGlobal,&rate,sizeof(rate)) || rate!=48000)return false;
    if(get(*device,kAudioDevicePropertyVolumeScalar,kAudioObjectPropertyScopeOutput,volume,sizeof(*volume)) || *volume<=0)return false;
    if(get(*device,kAudioDevicePropertyMute,kAudioObjectPropertyScopeOutput,mute,sizeof(*mute)) || *mute)return false;
    return true;
}
int main(int argc,char **argv){
    if(argc==2 && strcmp(argv[1],"--self-test")==0){
        int pulses=0,quiet=48000,maximum=0,count=0;
        for(uint64_t i=0;i<6*48000;i++){int v=value_at(i);int m=v<0?-v:v;if(m>maximum)maximum=m;
            if(v){if(quiet>=2400)pulses++;quiet=0;count++;}else quiet++;}
        printf("{\"pulses\":%d,\"peak\":%d,\"nonzero_frames\":%d,\"audio_opened\":false}\n",pulses,maximum,count);
        return !(pulses==6 && maximum==163 && count==27132);
    }
    if(argc!=2 || strcmp(argv[1],"--controlled-source")!=0){fprintf(stderr,"Use --self-test or explicitly authorized --controlled-source\n");return 2;}
    setvbuf(stdout,NULL,_IONBF,0);atomic_init(&armed,false);atomic_init(&queueStopping,false);
    struct sigaction action={0};action.sa_handler=stop_signal;sigemptyset(&action.sa_mask);
    sigaction(SIGINT,&action,NULL);sigaction(SIGTERM,&action,NULL);sigaction(SIGHUP,&action,NULL);sigaction(SIGALRM,&action,NULL);alarm(15);
    AudioObjectID device=0;Float32 volume=0;UInt32 mute=0;
    if(!inspect(&device,&volume,&mute)){fprintf(stderr,"Built-in output preflight failed\n");return 1;}
    AudioStreamBasicDescription format={.mSampleRate=48000,.mFormatID=kAudioFormatLinearPCM,
      .mFormatFlags=kAudioFormatFlagIsSignedInteger|kAudioFormatFlagIsPacked,.mBytesPerPacket=4,.mFramesPerPacket=1,
      .mBytesPerFrame=4,.mChannelsPerFrame=2,.mBitsPerChannel=16};
    AudioQueueRef queue=NULL;OSStatus status=AudioQueueNewOutput(&format,fill,NULL,NULL,NULL,0,&queue);
    if(status)return 1;
    for(int i=0;i<3;i++){AudioQueueBufferRef buffer=NULL;status=AudioQueueAllocateBuffer(queue,FRAMES*4,&buffer);if(status)break;fill(NULL,queue,buffer);}
    if(!status)status=AudioQueueStart(queue,NULL);
    if(status){AudioQueueDispose(queue,true);return 1;}
    printf("{\"event\":\"source_ready\",\"pid\":%d,\"armed\":false}\n",getpid());
    double deadline=monotonic()+15,check=0;char command[32];size_t used=0;bool failed=false;
    while(!stopping && !atomic_load(&queueStopping) && monotonic()<deadline){
        if(monotonic()>=check){AudioObjectID d=0;Float32 v=0;UInt32 m=0;check=monotonic()+.25;
            if(!inspect(&d,&v,&m) || d!=device || fabs(v-volume)>.0001 || m!=mute){failed=true;stopping=1;break;}}
        struct pollfd fd={STDIN_FILENO,POLLIN|POLLHUP,0};int result=poll(&fd,1,50);
        if(result>0 && fd.revents){char bytes[32];ssize_t n=read(STDIN_FILENO,bytes,sizeof(bytes));if(n<=0)break;
            for(ssize_t i=0;i<n;i++){
                if(bytes[i]=='\n'){
                    command[used]=0;
                    if(strcmp(command,"ARM")==0 && !atomic_load(&armed)){
                        deadline=monotonic()+6.5;atomic_store_explicit(&armed,true,memory_order_release);
                        printf("{\"event\":\"source_armed\"}\n");
                    }else if(strcmp(command,"STOP")==0)stopping=1;else {failed=true;stopping=1;}
                    used=0;
                }else if(used<sizeof(command)-1)command[used++]=bytes[i];else {failed=true;stopping=1;}
            }
        }
    }
    failed=failed||atomic_load(&queueStopping);stopping=1;atomic_store(&queueStopping,true);OSStatus stopped=AudioQueueStop(queue,true),disposed=AudioQueueDispose(queue,true);
    printf("{\"event\":\"source_stopped\",\"callbacks\":%llu,\"nonzero_frames\":%llu,\"peak\":%d,\"stop_status\":%d,\"dispose_status\":%d}\n",
      (unsigned long long)callbacks,(unsigned long long)nonzero,peak,(int)stopped,(int)disposed);
    return stopped||disposed||failed;
}
