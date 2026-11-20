#import "ControlledCapture.h"
#import "ApplicationSource.h"
#import "SystemAudioSource.h"
#import <CoreAudio/CoreAudio.h>
#import <CoreAudio/CATapDescription.h>
#import <CoreAudio/AudioHardwareTapping.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <mach/mach_time.h>
#include <math.h>
#include <CoreAudio/HostTime.h>

#define MB_CAPACITY 32768
#define MB_MAX_CALLBACK 8192

typedef struct {
    AudioObjectID tap,aggregate;
    AudioDeviceIOProcID proc;
    _Atomic bool started;
    bool startAttempted;
    _Atomic bool enabled,canceled;
    _Atomic int fault,faultReason;
    _Atomic uint64_t callbacks,frames,nonzeroFloatFrames,firstCallbackTicks;
    _Atomic double systemValidatedUntil;
    mb_pcm_ring ring;
    float storage[MB_CAPACITY*2];
    bool boundedSystem;
    uint64_t captureTimes[MB_CAPACITY],lastHostTick,lastEmittedNs;
    _Atomic uint64_t skippedRingFrames;
    float scratch[MB_MAX_CALLBACK*2];
} CaptureState;

// Stable numeric reason IDs: 1 format, 2 ring overflow, 3 source identity,
// 4 aggregate not running, 5 finite watchdog, 6 teardown, 7 invalid HAL input timestamp. No sample logging.
static void firstFault(CaptureState *s,int reason){int expected=0;atomic_compare_exchange_strong(&s->faultReason,&expected,reason);}
static BOOL fail(NSError **error,NSString *step,OSStatus status) {
    if(error)*error=[NSError errorWithDomain:@"MacbeeControlledCapture" code:status
                  userInfo:@{NSLocalizedDescriptionKey:[NSString stringWithFormat:@"%@: %d",step,(int)status]}];
    return NO;
}
static OSStatus captureInput(AudioObjectID device,const AudioTimeStamp *now,
 const AudioBufferList *input,const AudioTimeStamp *inputTime,
 AudioBufferList *output,const AudioTimeStamp *outputTime,void *context) {
    (void)device;(void)now;(void)outputTime;
    CaptureState *s=context;
    // This aggregate is input-only. Never write nonzero hardware output.
    if(output)for(UInt32 b=0;b<output->mNumberBuffers;b++)
        if(output->mBuffers[b].mData)memset(output->mBuffers[b].mData,0,output->mBuffers[b].mDataByteSize);
    if(!atomic_load_explicit(&s->enabled,memory_order_acquire) || atomic_load(&s->canceled))return noErr;
    if(atomic_fetch_add_explicit(&s->callbacks,1,memory_order_relaxed)==0)
        atomic_store_explicit(&s->firstCallbackTicks,mach_absolute_time(),memory_order_relaxed);
    const float *samples=NULL;size_t frames=0;
    if(input && input->mNumberBuffers==1 && input->mBuffers[0].mNumberChannels==2) {
        const AudioBuffer *b=&input->mBuffers[0];
        if(b->mData && b->mDataByteSize%(2*sizeof(float))==0) {
            samples=b->mData;frames=b->mDataByteSize/(2*sizeof(float));
        }
    } else if(input && input->mNumberBuffers==2 &&
        input->mBuffers[0].mNumberChannels==1 && input->mBuffers[1].mNumberChannels==1) {
        const AudioBuffer *a=&input->mBuffers[0],*b=&input->mBuffers[1];
        if(a->mData && b->mData && a->mDataByteSize==b->mDataByteSize && a->mDataByteSize%sizeof(float)==0) {
            frames=a->mDataByteSize/sizeof(float);
            if(frames<=MB_MAX_CALLBACK) {
                const float *left=a->mData,*right=b->mData;
                for(size_t i=0;i<frames;i++){s->scratch[i*2]=left[i];s->scratch[i*2+1]=right[i];}
                samples=s->scratch;
            }
        }
    }
    BOOL formatOK=samples && frames && frames<=MB_MAX_CALLBACK;
    uint64_t firstNs=0;
    if(formatOK && s->boundedSystem){
        uint64_t nowTick=mach_absolute_time();
        uint64_t maxFutureSkew=AudioConvertNanosToHostTime(20000000);
        if(!inputTime || !(inputTime->mFlags & kAudioTimeStampHostTimeValid) ||
           !inputTime->mHostTime || inputTime->mHostTime<=s->lastHostTick ||
           (inputTime->mHostTime>nowTick && inputTime->mHostTime-nowTick>maxFutureSkew)){
            firstFault(s,7);atomic_store(&s->fault,1);
            atomic_store_explicit(&s->enabled,false,memory_order_release);return noErr;
        }
        uint64_t clampedTick=inputTime->mHostTime>nowTick?nowTick:inputTime->mHostTime;
        if(!(firstNs=AudioConvertHostTimeToNanos(clampedTick))){
            firstFault(s,7);atomic_store(&s->fault,1);
            atomic_store_explicit(&s->enabled,false,memory_order_release);return noErr;
        }
        if(firstNs<s->lastEmittedNs)firstNs=s->lastEmittedNs;
        s->lastHostTick=inputTime->mHostTime;
        s->lastEmittedNs=firstNs;
    }
    if(!formatOK || !mb_pcm_ring_push_timed(&s->ring,samples,frames,firstNs)) {
        firstFault(s,formatOK?2:1);
        atomic_store(&s->fault,1);atomic_store_explicit(&s->enabled,false,memory_order_release);
        return noErr; // Lifecycle timer stops the tap; callback never blocks or logs PCM.
    }
    uint64_t nonzero=0;for(size_t i=0;i<frames;i++)if(samples[i*2]!=0 || samples[i*2+1]!=0)nonzero++;
    atomic_fetch_add_explicit(&s->frames,frames,memory_order_relaxed);
    atomic_fetch_add_explicit(&s->nonzeroFloatFrames,nonzero,memory_order_relaxed);
    return noErr;
}

@implementation MBControlledCapture
- (void)diagnose:(NSString *)stage status:(OSStatus)status {
    if(!self.diagnosticHandler)return;
    NSMutableDictionary *event=[[self diagnosticSnapshot] mutableCopy];
    event[@"stage"]=stage;event[@"status"]=@(status);event[@"uptime"]=@(NSProcessInfo.processInfo.systemUptime);
    self.diagnosticHandler(event);
}
- (NSDictionary *)diagnosticSnapshot {
    CaptureState *s=_state;if(!s)return @{};
    mach_timebase_info_data_t timebase;mach_timebase_info(&timebase);
    uint64_t ticks=atomic_load_explicit(&s->firstCallbackTicks,memory_order_relaxed);
    uint64_t read=atomic_load_explicit(&s->ring.read_frame,memory_order_relaxed),write=atomic_load_explicit(&s->ring.write_frame,memory_order_relaxed);
    return @{@"callbacks":@(atomic_load(&s->callbacks)),@"frames":@(atomic_load(&s->frames)),
      @"nonzero_float_frames":@(atomic_load(&s->nonzeroFloatFrames)),
      @"first_callback_uptime":@((double)ticks*timebase.numer/timebase.denom/1e9),
      @"ring_dropped_frames":@(atomic_load(&s->skippedRingFrames)),@"queued_frames":@(write>=read?write-read:0),@"fault":@(atomic_load(&s->fault)),@"fault_reason":@(atomic_load(&s->faultReason)),@"health_valid_until":@(atomic_load(&s->systemValidatedUntil)),@"io_started":@(atomic_load(&s->started))};
}
- (instancetype)init {
    self=[super init];if(self){
        CaptureState *s=calloc(1,sizeof(*s));if(!s)return nil;
        _state=s;_readLock=[NSLock new];atomic_init(&s->started,false);atomic_init(&s->canceled,false);atomic_init(&s->enabled,false);atomic_init(&s->fault,0);atomic_init(&s->faultReason,0);atomic_init(&s->callbacks,0);atomic_init(&s->frames,0);atomic_init(&s->nonzeroFloatFrames,0);atomic_init(&s->firstCallbackTicks,0);
        atomic_init(&s->skippedRingFrames,0);atomic_init(&s->systemValidatedUntil,0);mb_pcm_ring_init(&s->ring,s->storage,MB_CAPACITY);
    }return self;
}
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue {
    self=[self init];if(self)_selectedLifecycleQueue=queue;return self;
}
- (void)cancelPendingStart {
    CaptureState *s=_state;if(s){atomic_store(&s->canceled,true);atomic_store(&s->enabled,false);}
}
- (BOOL)checkCancellation:(NSError **)error {
    CaptureState *s=_state;
    return !atomic_load(&s->canceled) || fail(error,@"Selected-app setup canceled",-11);
}
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error {
    if(![NSThread isMainThread])return fail(error,@"Lifecycle must run on the main thread",-1);
    CaptureState *existing=_state;if(existing->tap || existing->aggregate || existing->proc)return fail(error,@"A capture session already owns resources",-2);
    if(!helper.isRunning || helper.processIdentifier<=0)return fail(error,@"Controlled helper is not running",-4);
    pid_t pid=helper.processIdentifier;AudioObjectID process=kAudioObjectUnknown;UInt32 size=sizeof(process);
    AudioObjectPropertyAddress pidAddress={kAudioHardwarePropertyTranslatePIDToProcessObject,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    OSStatus status=AudioObjectGetPropertyData(kAudioObjectSystemObject,&pidAddress,sizeof(pid),&pid,&size,&process);
    if(status || !process)return fail(error,@"Controlled helper has no active audio process",status?:-5);
    _helper=helper;_deferTeardown=NO;
    _isSourceAlive=^BOOL{return helper.isRunning;};
    return [self startProcesses:@[@(process)] name:@"Macbee controlled helper only" error:error];
}
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {
    return [self prepareForSelectedSource:source error:error] && [self activatePreparedSource:error];
}
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {
    if(NSThread.isMainThread || !_selectedLifecycleQueue || !source || ![self checkCancellation:error] ||
       ![source revalidateRequiringOutput:YES error:error])return NO;
    CaptureState *existing=_state;if(existing->tap || existing->aggregate || existing->proc)return fail(error,@"A capture session already owns resources",-2);
    _deferTeardown=YES;_isSourceAlive=^BOOL{return [source revalidateRequiringOutput:NO error:nil];};
    return [self prepareProcesses:source.audioProcesses name:@"Macbee selected application only" error:error];
}
- (BOOL)prepareForSystemAudioSource:(MBSystemAudioSource *)source error:(NSError **)error {
    if(![[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeSystemAudioEnabled"] boolValue])return fail(error,@"System Audio is disabled in this build",-20);
    if(NSThread.isMainThread || !_selectedLifecycleQueue || !source || ![self checkCancellation:error] || ![source revalidate:error])return NO;
    _systemSource=YES;_deferTeardown=YES;_isSourceAlive=^BOOL{return [source revalidate:nil];};
    CATapDescription *description=[source inactiveDescription:error];if(!description)return NO;
    return [self prepareDescription:description error:error];
}
- (BOOL)startProcesses:(NSArray<NSNumber *> *)processes name:(NSString *)name error:(NSError **)error {
    return [self prepareProcesses:processes name:name error:error] && [self activatePreparedSource:error];
}
- (BOOL)prepareProcesses:(NSArray<NSNumber *> *)processes name:(NSString *)name error:(NSError **)error {
    if(!processes.count || processes.count>16 || [processes containsObject:@0] || !_isSourceAlive || !_isSourceAlive())return fail(error,@"Selected process identity is no longer valid",-8);
    CATapDescription *description=[[CATapDescription alloc] initStereoMixdownOfProcesses:processes];
    description.name=name;description.privateTap=YES;description.exclusive=NO;
    if(@available(macOS 26.0,*))description.processRestoreEnabled=NO;
    description.muteBehavior=CATapMutedWhenTapped;
    return [self prepareDescription:description error:error];
}
- (BOOL)prepareDescription:(CATapDescription *)description error:(NSError **)error {
    CaptureState *s=_state;
    if(s->tap || s->aggregate || s->proc)return fail(error,@"A capture session already owns resources",-2);
    NSString *usage=[[NSBundle mainBundle] objectForInfoDictionaryKey:@"NSAudioCaptureUsageDescription"];
    if(!usage.length)return fail(error,@"Capture is not enabled in this app build",-3);
    if(![self checkCancellation:error])return NO;
    OSStatus status=noErr;UInt32 size=0;
    [self diagnose:@"tap_create_enter" status:0];
    status=AudioHardwareCreateProcessTap(description,&s->tap);
    [self diagnose:@"tap_create_return" status:status];
    if(status){[self stopWithError:nil];return fail(error,@"Create controlled tap",status);}
    if(![self checkCancellation:error])return NO;
    AudioStreamBasicDescription format={0};size=sizeof(format);
    AudioObjectPropertyAddress formatAddress={kAudioTapPropertyFormat,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    status=AudioObjectGetPropertyData(s->tap,&formatAddress,0,NULL,&size,&format);
    if(status || format.mFormatID!=kAudioFormatLinearPCM || format.mSampleRate!=48000 ||
       format.mChannelsPerFrame!=2 || format.mBitsPerChannel!=32 || !(format.mFormatFlags&kAudioFormatFlagIsFloat) ||
       (format.mFormatFlags&kAudioFormatFlagIsBigEndian) || format.mFramesPerPacket!=1 ||
       format.mBytesPerFrame!=((format.mFormatFlags&kAudioFormatFlagIsNonInterleaved)?4:8)) {
        [self stopWithError:nil];return fail(error,@"First capture supports only 48 kHz Float32 stereo",status?:-6);
    }
    if(![self checkCancellation:error])return NO;
    CFStringRef uid=NULL;size=sizeof(uid);
    AudioObjectPropertyAddress uidAddress={kAudioTapPropertyUID,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    status=AudioObjectGetPropertyData(s->tap,&uidAddress,0,NULL,&size,&uid);
    if(status || !uid){[self stopWithError:nil];return fail(error,@"Read private tap UID",status?:-7);}
    NSString *tapUID=CFBridgingRelease(uid);
    NSDictionary *aggregate=@{@kAudioAggregateDeviceNameKey:@"Macbee private controlled input",
      @kAudioAggregateDeviceUIDKey:NSUUID.UUID.UUIDString,@kAudioAggregateDeviceIsPrivateKey:@YES,
      @kAudioAggregateDeviceTapAutoStartKey:@NO,
      @kAudioAggregateDeviceTapListKey:@[@{@kAudioSubTapUIDKey:tapUID,@kAudioSubTapDriftCompensationKey:@YES}]};
    if(![self checkCancellation:error])return NO;
    [self diagnose:@"aggregate_create_enter" status:0];
    status=AudioHardwareCreateAggregateDevice((__bridge CFDictionaryRef)aggregate,&s->aggregate);
    [self diagnose:@"aggregate_create_return" status:status];
    if(status){[self stopWithError:nil];return fail(error,@"Create private tap-only aggregate",status);}
    if(![self checkCancellation:error])return NO;
    [self diagnose:@"io_proc_create_enter" status:0];
    status=AudioDeviceCreateIOProcID(s->aggregate,captureInput,s,&s->proc);
    [self diagnose:@"io_proc_create_return" status:status];
    if(status){[self stopWithError:nil];return fail(error,@"Create controlled input callback",status);}
    if(![self checkCancellation:error])return NO;
    s->boundedSystem=_systemSource && [[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeBoundedSystemPacing"] boolValue];
    s->lastHostTick=0;s->lastEmittedNs=0;s->ring.capture_ns=s->boundedSystem?s->captureTimes:NULL;
    s->ring.capacity_frames=s->boundedSystem?MB_CAPACITY:16384;
    atomic_store(&s->skippedRingFrames,0);mb_pcm_ring_clear_stopped(&s->ring);atomic_store(&s->fault,0);atomic_store(&s->faultReason,0);atomic_store(&s->callbacks,0);atomic_store(&s->frames,0);atomic_store(&s->nonzeroFloatFrames,0);atomic_store(&s->firstCallbackTicks,0);
    atomic_store_explicit(&s->enabled,false,memory_order_release);
    [self diagnose:@"capture_prepared_inactive" status:0];return YES;
}
- (BOOL)activatePreparedSource:(NSError **)error {
    CaptureState *s=_state;
    if((_deferTeardown ? NSThread.isMainThread || !_selectedLifecycleQueue : !NSThread.isMainThread) ||
       !s->tap || !s->aggregate || !s->proc || atomic_load(&s->started))
        return fail(error,@"No inactive prepared capture is available",-10);
    if(!_isSourceAlive || !_isSourceAlive()){[self stopWithError:nil];return fail(error,@"Source exited before activation",-9);}
    if(![self checkCancellation:error])return NO;
    // Permission boundary: never invoke until this exact source/scope is approved.
    [self diagnose:@"io_start_enter" status:0];
    atomic_store_explicit(&s->enabled,true,memory_order_release);
    s->startAttempted=_deferTeardown; // A timed-out selected-app Start may still have taken effect.
    OSStatus status=AudioDeviceStart(s->aggregate,s->proc);
    [self diagnose:@"io_start_return" status:status];
    if(status){[self stopWithError:nil];return fail(error,@"Start controlled input (permission may be required)",status);}
    s->startAttempted=true;atomic_store(&s->started,true);
    if(_systemSource)atomic_store(&s->systemValidatedUntil,NSProcessInfo.processInfo.systemUptime+1);
    if(![self checkCancellation:error]){[self suspendReadingKeepingTap];return NO;}
    __weak MBControlledCapture *weak=self;
    BOOL manual=_deferTeardown && [[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeManualSessionEnabled"] boolValue];
    NSTimeInterval end=manual?INFINITY:NSProcessInfo.processInfo.systemUptime+(_deferTeardown ? MBApplicationSessionMaxSeconds+5 : 15);
    if(_deferTeardown){
        // A dispatch worker has no runloop. Keep checks on the same serial HAL
        // owner, never race source validation/teardown from a second timer thread.
        _selectedWatchdog=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,_selectedLifecycleQueue);
        dispatch_source_set_timer(_selectedWatchdog,dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),100*NSEC_PER_MSEC,10*NSEC_PER_MSEC);
        dispatch_source_set_event_handler(_selectedWatchdog,^{
            MBControlledCapture *strong=weak;if(!strong)return;
            CaptureState *state=strong->_state;if(atomic_load(&state->canceled))return;
            BOOL valid=strong->_isSourceAlive && strong->_isSourceAlive();
            if(!valid)firstFault(state,3);
            if(strong->_systemSource && valid){
                UInt32 running=0,size=sizeof(running);AudioObjectPropertyAddress address={kAudioDevicePropertyDeviceIsRunning,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
                valid=AudioObjectGetPropertyData(state->aggregate,&address,0,NULL,&size,&running)==noErr && size==sizeof(running) && running!=0;
                if(valid)atomic_store(&state->systemValidatedUntil,NSProcessInfo.processInfo.systemUptime+1);else firstFault(state,4);
            }
            if(!valid || atomic_load(&state->fault) || NSProcessInfo.processInfo.systemUptime>=end){
                if(NSProcessInfo.processInfo.systemUptime>=end)firstFault(state,5);
                atomic_store(&state->fault,1);[strong suspendReadingKeepingTap];
            }
        });dispatch_resume(_selectedWatchdog);
    }else{
        _watchdog=[NSTimer scheduledTimerWithTimeInterval:.1 repeats:YES block:^(NSTimer *timer){
            MBControlledCapture *strong=weak;if(!strong){[timer invalidate];return;}
            CaptureState *state=strong->_state;
            if(!strong->_isSourceAlive || !strong->_isSourceAlive() || atomic_load(&state->fault) || NSProcessInfo.processInfo.systemUptime>=end)[strong stopWithError:nil];
        }];
    }
    return YES;
}
- (size_t)readStereoFrames:(float *)destination capacity:(size_t)capacity {
    return [self readStereoFrames:destination capacity:capacity captureTime:NULL];
}
- (size_t)readStereoFrames:(float *)destination capacity:(size_t)capacity captureTime:(uint64_t *)timeNs {
    if(timeNs)*timeNs=0;
    [_readLock lock];CaptureState *s=_state;
    if(s->boundedSystem)atomic_fetch_add_explicit(&s->skippedRingFrames,mb_pcm_ring_trim(&s->ring,4096,1920),memory_order_relaxed);
    size_t count=(atomic_load_explicit(&s->enabled,memory_order_acquire) && !atomic_load(&s->canceled))?mb_pcm_ring_pop_timed(&s->ring,destination,capacity,timeNs):0;
    [_readLock unlock];return count;
}
- (size_t)queuedFrames {
    CaptureState *s=_state;if(!s)return 0;
    uint64_t read=atomic_load_explicit(&s->ring.read_frame,memory_order_relaxed);
    uint64_t write=atomic_load_explicit(&s->ring.write_frame,memory_order_acquire);
    return write>=read?(size_t)(write-read):0;
}
- (BOOL)hasFault {CaptureState *s=_state;return s && atomic_load(&s->fault)!=0;}
- (BOOL)systemHealthValid {
    CaptureState *s=_state;return _systemSource && atomic_load(&s->started) && atomic_load(&s->enabled) &&
        !atomic_load(&s->canceled) && !atomic_load(&s->fault) && NSProcessInfo.processInfo.systemUptime<=atomic_load(&s->systemValidatedUntil);
}
- (void)suspendReadingKeepingTap {
    CaptureState *s=_state;atomic_store_explicit(&s->enabled,false,memory_order_release);
    // A callback may already be in flight. Do not reset the SPSC indices until
    // the IOProc has actually stopped; stopWithError clears all storage then.
    // Keep the private aggregate/tap active until owned renderers acknowledge
    // Stop. Never pause or terminate an existing user application.
}
- (BOOL)stopWithError:(NSError **)error {
    CaptureState *s=_state;if(!s)return YES;
    if(_selectedWatchdog){dispatch_source_cancel(_selectedWatchdog);_selectedWatchdog=nil;}
    [_watchdog invalidate];_watchdog=nil;atomic_store_explicit(&s->enabled,false,memory_order_release);
    OSStatus first=noErr,status=noErr;
    if(s->startAttempted){status=AudioDeviceStop(s->aggregate,s->proc);if(status){if(!first)first=status;}else{atomic_store(&s->started,false);s->startAttempted=false;}}
    if(s->proc){status=AudioDeviceDestroyIOProcID(s->aggregate,s->proc);if(status && !first)first=status;else if(!status)s->proc=NULL;}
    if(s->aggregate){status=AudioHardwareDestroyAggregateDevice(s->aggregate);if(status && !first)first=status;else if(!status)s->aggregate=0;}
    if(s->tap){status=AudioHardwareDestroyProcessTap(s->tap);if(status && !first)first=status;else if(!status)s->tap=0;}
    if(!s->proc){[_readLock lock];mb_pcm_ring_clear_stopped(&s->ring);memset(s->scratch,0,sizeof(s->scratch));[_readLock unlock];}
    _helper=nil;_isSourceAlive=nil;
    if(first){firstFault(s,6);atomic_store(&s->fault,2);}
    return first?fail(error,@"Capture teardown requires inspection",first):YES;
}
- (void)dealloc {
    [self stopWithError:nil];CaptureState *s=_state;
    // If HAL unexpectedly retains a callback, leak this tiny state rather than
    // let it access freed memory. Process exit owns the private HAL resources.
    if(s && !s->proc)free(s);
}
@end
