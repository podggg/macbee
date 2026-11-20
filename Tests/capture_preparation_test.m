/* All HAL functions below are inert fakes. No real device/tap/input is opened. */
#import "../Sources/ControlledCapture/ControlledCapture.h"
#import "../Sources/ControlledCapture/ApplicationSource.h"
#import <CoreAudio/CATapDescription.h>
#import <CoreAudio/AudioHardwareTapping.h>
#include <assert.h>
#include <stdatomic.h>
#import <objc/runtime.h>
static unsigned starts=0,stops=0,deleted=0;
static BOOL manualMode=NO;
static _Atomic double fixtureTime;
static double uptime(id object,SEL selector){(void)object;(void)selector;return atomic_load(&fixtureTime);}
static dispatch_semaphore_t registrationEntered,registrationRelease;
@interface PreparedSource : MBApplicationSource
@property BOOL valid;
@end
@implementation PreparedSource
- (NSArray<NSNumber *> *)audioProcesses {return @[@42];}
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error {(void)output;(void)error;assert(!NSThread.isMainThread);return self.valid;}
@end
OSStatus AudioHardwareCreateProcessTap(CATapDescription *d,AudioObjectID *out){assert(!d.exclusive && d.privateTap && d.muteBehavior==CATapMutedWhenTapped);*out=43;return 0;}
OSStatus AudioObjectGetPropertyData(AudioObjectID object,const AudioObjectPropertyAddress *address,UInt32 qualifierSize,const void *qualifier,UInt32 *size,void *data){
 (void)qualifierSize;(void)qualifier;assert(object==43);
 if(address->mSelector==kAudioTapPropertyFormat){AudioStreamBasicDescription f={.mSampleRate=48000,.mFormatID=kAudioFormatLinearPCM,.mFormatFlags=kAudioFormatFlagIsFloat|kAudioFormatFlagIsPacked,.mBytesPerPacket=8,.mFramesPerPacket=1,.mBytesPerFrame=8,.mChannelsPerFrame=2,.mBitsPerChannel=32};assert(*size==sizeof(f));memcpy(data,&f,sizeof(f));}
 else{assert(address->mSelector==kAudioTapPropertyUID);*(CFStringRef *)data=CFRetain(CFSTR("owned-offline-tap"));}
 return 0;
}
OSStatus AudioHardwareCreateAggregateDevice(CFDictionaryRef d,AudioObjectID *out){assert(CFDictionaryGetValue(d,CFSTR(kAudioAggregateDeviceTapAutoStartKey))==kCFBooleanFalse);*out=44;return 0;}
OSStatus AudioDeviceCreateIOProcID(AudioObjectID d,AudioDeviceIOProc callback,void *context,AudioDeviceIOProcID *out){(void)callback;(void)context;assert(!NSThread.isMainThread && d==44);
 if(registrationEntered){dispatch_semaphore_signal(registrationEntered);dispatch_semaphore_wait(registrationRelease,DISPATCH_TIME_FOREVER);}
 *out=(AudioDeviceIOProcID)(uintptr_t)45;return 0;}
OSStatus AudioDeviceStart(AudioObjectID d,AudioDeviceIOProcID p){assert(!NSThread.isMainThread && d==44 && p);starts++;return 0;}
OSStatus AudioDeviceStop(AudioObjectID d,AudioDeviceIOProcID p){assert(!NSThread.isMainThread && d==44 && p);stops++;return 0;}
OSStatus AudioDeviceDestroyIOProcID(AudioObjectID d,AudioDeviceIOProcID p){assert(!NSThread.isMainThread && d==44 && p);deleted++;return 0;}
OSStatus AudioHardwareDestroyAggregateDevice(AudioObjectID d){assert(d==44);deleted++;return 0;}
OSStatus AudioHardwareDestroyProcessTap(AudioObjectID d){assert(d==43);deleted++;return 0;}
static id info(id object,SEL selector,NSString *key){(void)object;(void)selector;if([key isEqual:@"MacbeeManualSessionEnabled"])return @(manualMode);return [key isEqual:@"NSAudioCaptureUsageDescription"]?@"Offline fixture only":nil;}
static void work(dispatch_queue_t queue,dispatch_block_t block){
 dispatch_semaphore_t done=dispatch_semaphore_create(0);
 dispatch_async(queue,^{block();dispatch_semaphore_signal(done);});
 assert(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC))==0);
}
int main(void){@autoreleasepool{
 method_setImplementation(class_getInstanceMethod(NSBundle.class,@selector(objectForInfoDictionaryKey:)),(IMP)info);
 dispatch_queue_t queue=dispatch_queue_create("offline.capture.fixture",DISPATCH_QUEUE_SERIAL);
 MBControlledCapture *c=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];PreparedSource *source=[PreparedSource new];source.valid=YES;
 work(queue,^{
  assert([c prepareForSelectedSource:source error:nil]);assert(starts==0 && ![c.diagnosticSnapshot[@"io_started"] boolValue]);
  assert([c stopWithError:nil]);assert(stops==0 && deleted==3);
  assert([c prepareForSelectedSource:source error:nil]);assert(starts==0);
  assert([c activatePreparedSource:nil]);assert(starts==1 && [c.diagnosticSnapshot[@"io_started"] boolValue]);
  assert(![c activatePreparedSource:nil]);assert(starts==1);
  assert([c stopWithError:nil]);assert(stops==1 && deleted==6);
 });
 // The selected watchdog runs on its serial queue, with no worker runloop.
 MBControlledCapture *watched=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];
 work(queue,^{assert([watched prepareForSelectedSource:source error:nil]);assert([watched activatePreparedSource:nil]);});
 source.valid=NO;double watchEnd=NSProcessInfo.processInfo.systemUptime+1;
 while(!watched.hasFault && NSProcessInfo.processInfo.systemUptime<watchEnd)[NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.002]];
 assert(watched.hasFault);work(queue,^{assert([watched stopWithError:nil]);});source.valid=YES;
 assert(starts==2 && stops==2 && deleted==9);
 // A delayed, inert HAL registration does not stop the main runloop. Cancel
 // during the call must prevent the following activation, even when it returns.
 registrationEntered=dispatch_semaphore_create(0);registrationRelease=dispatch_semaphore_create(0);
 MBControlledCapture *delayed=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];
 __block BOOL complete=NO;__block unsigned beats=0;
 NSTimer *timer=[NSTimer scheduledTimerWithTimeInterval:.002 repeats:YES block:^(NSTimer *t){(void)t;beats++;}];
 dispatch_async(queue,^{assert(![delayed prepareForSelectedSource:source error:nil]);assert([delayed stopWithError:nil]);dispatch_async(dispatch_get_main_queue(),^{complete=YES;});});
 assert(dispatch_semaphore_wait(registrationEntered,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC))==0);
 double end=NSProcessInfo.processInfo.systemUptime+1;
 while(beats<3 && NSProcessInfo.processInfo.systemUptime<end)[NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.002]];
 assert(beats>=3 && !complete);[delayed cancelPendingStart];assert(starts==2);
 dispatch_semaphore_signal(registrationRelease);
 while(!complete && NSProcessInfo.processInfo.systemUptime<end)[NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.002]];
 assert(complete && starts==2 && deleted==12);[timer invalidate];
 // Fake elapsed hours cannot trip manual capture; finite diagnostics still trip.
 registrationEntered=nil;registrationRelease=nil;
 Method clockMethod=class_getInstanceMethod(object_getClass(NSProcessInfo.processInfo),@selector(systemUptime));IMP oldClock=method_setImplementation(clockMethod,(IMP)uptime);
 for(unsigned manual=0;manual<2;manual++){
  manualMode=manual;atomic_store(&fixtureTime,0);
  MBControlledCapture *clocked=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];
  work(queue,^{assert([clocked prepareForSelectedSource:source error:nil]);assert([clocked activatePreparedSource:nil]);});
  atomic_store(&fixtureTime,7200);[NSThread sleepForTimeInterval:.15];
  assert(clocked.hasFault==!manual);work(queue,^{assert([clocked stopWithError:nil]);});
 }
 method_setImplementation(clockMethod,oldClock);
 puts("prepare inactive/activate once/cancel releases fake resources; no real HAL effects");
}return 0;}
