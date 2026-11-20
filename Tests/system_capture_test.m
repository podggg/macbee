/* Fake HAL models Apple's include/exclude contract. It never opens a device.
 * This proves descriptor/callback/ring wiring, not real OS/acoustic behavior. */
#import "../Sources/ControlledCapture/ControlledCapture.h"
#import "../Sources/ControlledCapture/ApplicationSource.h"
#import "../Sources/ControlledCapture/SystemAudioSource.h"
#import <CoreAudio/AudioHardwareTapping.h>
#import <objc/runtime.h>
#include <assert.h>
#include <math.h>
#include <CoreAudio/HostTime.h>
#include <mach/mach_time.h>
static CATapDescription *descriptor;
static AudioDeviceIOProc callback;
static void *context;
static BOOL active=NO;
static uint64_t origin,nextFrame;
static AudioTimeStamp inputTime(unsigned frames) {
 AudioTimeStamp s={.mHostTime=origin+AudioConvertNanosToHostTime(nextFrame*1000000000/48000),.mFlags=kAudioTimeStampHostTimeValid};
 nextFrame+=frames;return s;
}
@interface SelectedFixture : MBApplicationSource @end
@implementation SelectedFixture
- (NSArray *)audioProcesses{return @[@41];}
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error{(void)output;(void)error;return YES;}
@end
@interface GlobalFixture : MBSystemAudioSource
@property BOOL valid;
@end
@implementation GlobalFixture
- (BOOL)revalidate:(NSError **)error{(void)error;return self.valid;}
- (CATapDescription *)inactiveDescription:(NSError **)error{
 (void)error;CATapDescription *d=[[CATapDescription alloc] initStereoGlobalTapButExcludeProcesses:@[@77]];
 d.privateTap=YES;d.exclusive=YES;d.muteBehavior=CATapMutedWhenTapped;return d;
}
@end
static id info(id object,SEL selector,NSString *key){(void)object;(void)selector;
 if([key isEqual:@"MacbeeBoundedSystemPacing"])return @YES;
 if([key isEqual:@"MacbeeSystemAudioEnabled"] || [key isEqual:@"MacbeeManualSessionEnabled"])return @YES;
 return [key isEqual:@"NSAudioCaptureUsageDescription"]?@"Inert fixture only":nil;
}
OSStatus AudioHardwareCreateProcessTap(CATapDescription *d,AudioObjectID *out){assert(d.privateTap && d.muteBehavior==CATapMutedWhenTapped);descriptor=d;*out=43;return 0;}
OSStatus AudioObjectGetPropertyData(AudioObjectID object,const AudioObjectPropertyAddress *a,UInt32 qs,const void *q,UInt32 *size,void *data){
 (void)qs;(void)q;
 if(a->mSelector==kAudioDevicePropertyDeviceIsRunning){assert(object==44);*(UInt32 *)data=active;return 0;}
 assert(object==43);
 if(a->mSelector==kAudioTapPropertyFormat){AudioStreamBasicDescription f={.mSampleRate=48000,.mFormatID=kAudioFormatLinearPCM,.mFormatFlags=kAudioFormatFlagIsFloat|kAudioFormatFlagIsPacked,.mBytesPerPacket=8,.mFramesPerPacket=1,.mBytesPerFrame=8,.mChannelsPerFrame=2,.mBitsPerChannel=32};assert(*size==sizeof(f));memcpy(data,&f,sizeof(f));}
 else{assert(a->mSelector==kAudioTapPropertyUID);*(CFStringRef *)data=CFRetain(CFSTR("system-inert-fixture"));}return 0;
}
OSStatus AudioHardwareCreateAggregateDevice(CFDictionaryRef d,AudioObjectID *out){assert(CFDictionaryGetValue(d,CFSTR(kAudioAggregateDeviceTapAutoStartKey))==kCFBooleanFalse);*out=44;return 0;}
OSStatus AudioDeviceCreateIOProcID(AudioObjectID d,AudioDeviceIOProc proc,void *ctx,AudioDeviceIOProcID *out){assert(d==44);callback=proc;context=ctx;*out=(AudioDeviceIOProcID)(uintptr_t)45;return 0;}
OSStatus AudioDeviceStart(AudioObjectID d,AudioDeviceIOProcID p){assert(d==44 && p);active=YES;return 0;}
OSStatus AudioDeviceStop(AudioObjectID d,AudioDeviceIOProcID p){assert(d==44 && p);active=NO;return 0;}
OSStatus AudioDeviceDestroyIOProcID(AudioObjectID d,AudioDeviceIOProcID p){assert(d==44 && p);callback=NULL;context=NULL;return 0;}
OSStatus AudioHardwareDestroyAggregateDevice(AudioObjectID d){assert(d==44);return 0;}
OSStatus AudioHardwareDestroyProcessTap(AudioObjectID d){assert(d==43);descriptor=nil;return 0;}
static void work(dispatch_queue_t queue,dispatch_block_t block){
 dispatch_semaphore_t done=dispatch_semaphore_create(0);dispatch_async(queue,^{block();dispatch_semaphore_signal(done);});
 assert(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC))==0);
}
static void emit(float a,float b,float renderer){
 assert(active && callback);float value=0;NSArray *ids=@[@41,@42,@77];float samples[]={a,b,renderer};
 for(NSUInteger n=0;n<ids.count;n++){
  BOOL listed=[descriptor.processes containsObject:ids[n]];
  if(descriptor.exclusive?!listed:listed)value+=samples[n];
 }
 float pcm[16];for(unsigned n=0;n<16;n++)pcm[n]=value;
 AudioBufferList input={.mNumberBuffers=1,.mBuffers={{.mNumberChannels=2,.mDataByteSize=sizeof(pcm),.mData=pcm}}};
 AudioTimeStamp other={0},stamp=inputTime(8);AudioBufferList output={.mNumberBuffers=0};
 assert(callback(44,&other,&input,&stamp,&output,&other,context)==0);
}
static void expect(MBControlledCapture *c,float value){float out[16];assert([c readStereoFrames:out capacity:8]==8);for(unsigned n=0;n<16;n++)assert(fabsf(out[n]-value)<.000001f);}
// Feed the real registered callback with an ordered 100 ms artificial burst.
static void backlog(MBControlledCapture *capture,BOOL bounded){
 float data[9600],out[8192];for(unsigned i=0;i<4800;i++)data[i*2]=data[i*2+1]=i/8192.f;
 AudioBufferList input={.mNumberBuffers=1,.mBuffers={{.mNumberChannels=2,.mDataByteSize=sizeof(data),.mData=data}}};
 AudioTimeStamp other={0},stamp=inputTime(4800);AudioBufferList output={.mNumberBuffers=0};
 assert(callback(44,&other,&input,&stamp,&output,&other,context)==0);
 // Sleep after capture. Delaying the consumer must not advance the timestamp.
 [NSThread sleepForTimeInterval:.070];
 uint64_t hostNs=0;
 size_t n=bounded?[capture readStereoFrames:out capacity:960 captureTime:&hostNs]:[capture readStereoFrames:out capacity:4096];
 assert(n==(bounded?960U:4096U));
 for(size_t i=0;i<n;i++)assert(out[2*i]==(i+(bounded?2880:0))/8192.f);
 if(bounded){
   uint64_t start=AudioConvertHostTimeToNanos(stamp.mHostTime);
   assert(hostNs==start+60000000);
   size_t got=[capture readStereoFrames:out capacity:4096 captureTime:&hostNs];
   assert(got==960 && hostNs==start+80000000 && out[0]==3840/8192.f);
 }
 assert([capture.diagnosticSnapshot[@"ring_dropped_frames"] unsignedLongLongValue]==(bounded?2880U:0U));
}
int main(void){@autoreleasepool{
 origin=mach_absolute_time()-AudioConvertNanosToHostTime(2000000000);
 method_setImplementation(class_getInstanceMethod(NSBundle.class,@selector(objectForInfoDictionaryKey:)),(IMP)info);
 dispatch_queue_t queue=dispatch_queue_create("offline.system.capture",DISPATCH_QUEUE_SERIAL);
 MBControlledCapture *selected=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];
 work(queue,^{assert([selected prepareForSelectedSource:[SelectedFixture new] error:nil]);assert([selected activatePreparedSource:nil]);});
 emit(.01f,0,.5f);expect(selected,.01f);emit(.01f,.02f,.5f);expect(selected,.01f);emit(.01f,0,.9f);expect(selected,.01f);
 backlog(selected,NO);work(queue,^{assert([selected stopWithError:nil]);});
 GlobalFixture *source=[GlobalFixture new];source.valid=YES;
 MBControlledCapture *global=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];
 work(queue,^{assert([global prepareForSystemAudioSource:source error:nil]);assert([global activatePreparedSource:nil]);});
 assert(global.systemHealthValid);emit(0,0,.5f);expect(global,0);
 emit(.01f,0,.5f);expect(global,.01f);emit(.01f,.02f,.9f);expect(global,.03f);emit(0,.02f,.9f);expect(global,.02f);
 backlog(global,YES);source.valid=NO;double end=NSProcessInfo.processInfo.systemUptime+1;
 while(!global.hasFault && NSProcessInfo.processInfo.systemUptime<end)[NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.005]];
 assert(global.hasFault && !global.systemHealthValid);emit(.01f,.02f,.9f);float out[16];assert([global readStereoFrames:out capacity:8]==0);
 work(queue,^{assert([global stopWithError:nil]);});
 source.valid=YES;
 for(unsigned invalid=0;invalid<5;invalid++){
   MBControlledCapture *fresh=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];
   work(queue,^{assert([fresh prepareForSystemAudioSource:source error:nil]);assert([fresh activatePreparedSource:nil]);});
   float pcm[2]={1,1};AudioBufferList b={.mNumberBuffers=1,.mBuffers={{.mNumberChannels=2,.mDataByteSize=sizeof(pcm),.mData=pcm}}};
   AudioTimeStamp other={0},time=inputTime(1);AudioBufferList output={.mNumberBuffers=0};
   if(invalid==1)time.mFlags=0;
   if(invalid==2)time.mHostTime=0;
   if(invalid==3)time.mHostTime=mach_absolute_time()+AudioConvertNanosToHostTime(1000000000);
   if(invalid==4)time.mHostTime=mach_absolute_time()+AudioConvertNanosToHostTime(50000000);
   assert(callback(44,&other,&b,invalid?&time:NULL,&output,&other,context)==0);
   assert(fresh.hasFault && [fresh.diagnosticSnapshot[@"fault_reason"] intValue]==7);
   work(queue,^{assert([fresh stopWithError:nil]);});
 }
 // Small HAL cycle-projection future skew (<= 20 ms) clamps safely to now.
 {
   MBControlledCapture *skew=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];
   work(queue,^{assert([skew prepareForSystemAudioSource:source error:nil]);assert([skew activatePreparedSource:nil]);});
   float pcm[2]={.25f,-.25f},out[2]={0};
   AudioBufferList b={.mNumberBuffers=1,.mBuffers={{.mNumberChannels=2,.mDataByteSize=sizeof(pcm),.mData=pcm}}};
   AudioTimeStamp other={0},future={.mHostTime=mach_absolute_time()+AudioConvertNanosToHostTime(2000000),.mFlags=kAudioTimeStampHostTimeValid};
   AudioBufferList output={.mNumberBuffers=0};
   assert(callback(44,&other,&b,&future,&output,&other,context)==0);
   assert(!skew.hasFault && skew.queuedFrames==1);
   uint64_t hostNs=0;
   assert([skew readStereoFrames:out capacity:1 captureTime:&hostNs]==1);
   assert(hostNs>0 && hostNs<=AudioConvertHostTimeToNanos(mach_absolute_time()) && skew.queuedFrames==0);
   work(queue,^{assert([skew stopWithError:nil]);});
 }
 puts("synthetic A/B routing and renderer exclusion passed through real callback/ring; HAL is fake");
}return 0;}
