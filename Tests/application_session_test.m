/* Serialized asynchronous lifecycle fixtures. Every HAL/process effect is fake. */
#import "../Sources/ControlledCapture/ApplicationSession.h"
#import "../Sources/ControlledCapture/ControlledCapture.h"
#import <objc/runtime.h>
#include <assert.h>
#include <fcntl.h>
#include <unistd.h>
#import "../Sources/ControlledCapture/SessionStopDiagnostics.h"
@interface MBApplicationSession (TestHooks)
- (void)renderersReady:(NSUInteger)generation;
- (void)renderersStopped:(BOOL)verified generation:(NSUInteger)generation;
- (void)tick;
- (NSTimeInterval)maximumSharingDuration;
@end
static BOOL captureFault=NO,oneFrame=NO;
@implementation MBControlledCapture
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue {(void)queue;return [super init];}
- (void)cancelPendingStart {}
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)prepareForSystemAudioSource:(MBSystemAudioSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)systemHealthValid {return NO;}
- (BOOL)activatePreparedSource:(NSError **)error {(void)error;abort();}
- (NSDictionary *)diagnosticSnapshot {return @{@"callbacks":@0,@"frames":@0};}
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error {(void)helper;(void)error;abort();}
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (size_t)readStereoFrames:(float *)destination capacity:(size_t)capacity {(void)capacity;if(oneFrame){oneFrame=NO;destination[0]=destination[1]=0;return 1;}return 0;}
- (BOOL)hasFault {return captureFault;}
- (void)suspendReadingKeepingTap {}
- (BOOL)stopWithError:(NSError **)error {(void)error;assert(!NSThread.isMainThread);return YES;}
@end
@interface FakeSource : MBApplicationSource
@property BOOL valid;
@end
@implementation FakeSource
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error {(void)output;(void)error;assert(!NSThread.isMainThread);return self.valid;}
- (NSString *)name {return @"Fixture Player";}
@end
@interface FakeSession : MBApplicationSession
@property NSMutableArray<NSString *> *events;
@property double clock,preparationDelay;
@property BOOL captured,failCapture,failRelease,failPrepare,invalidAfterPrepare;
@property BOOL stopDuringBegin,ackDuringBegin;
@property BOOL preparing,activating,releasing;
@property dispatch_semaphore_t registrationGate,activationGate,releaseGate;
@end
@implementation FakeSession
- (instancetype)init {self=[super init];if(self)self.events=[NSMutableArray array];return self;}
- (void)record:(NSString *)event {@synchronized(self.events){[self.events addObject:event];}}
- (NSTimeInterval)now {return self.clock;}
- (BOOL)prepareSelectedCapture:(NSError **)error {
    (void)error;assert(!NSThread.isMainThread);[self record:@"register"];self.preparing=YES;
    [self setValue:[MBControlledCapture new] forKey:@"capture"];
    if(self.registrationGate)dispatch_semaphore_wait(self.registrationGate,DISPATCH_TIME_FOREVER);
    self.clock+=self.preparationDelay;
    if(self.invalidAfterPrepare){FakeSource *source=[self valueForKey:@"source"];source.valid=NO;}
    self.preparing=NO;return YES;
}
- (BOOL)prepareRenderers:(NSError **)error {(void)error;assert(NSThread.isMainThread);[self record:@"prepare"];return !self.failPrepare;}
- (BOOL)beginSelectedCapture:(NSError **)error {
    (void)error;assert(!NSThread.isMainThread);[self record:@"begin"];self.captured=!self.failCapture;self.activating=YES;
    if(self.activationGate)dispatch_semaphore_wait(self.activationGate,DISPATCH_TIME_FOREVER);
    if(self.stopDuringBegin)dispatch_sync(dispatch_get_main_queue(),^{[self stop];if(self.ackDuringBegin)[self renderersStopped:YES generation:1];assert(self.captured);});
    self.activating=NO;return self.captured;
}
- (void)suspendCapture {assert(NSThread.isMainThread);if(self.captured)[self record:@"suspend"];}
- (void)stopRenderers {assert(NSThread.isMainThread);[self record:@"stop_renderers"];}
- (BOOL)releaseCapture {
    assert(!NSThread.isMainThread && !self.preparing && !self.activating);self.releasing=YES;
    if(self.releaseGate)dispatch_semaphore_wait(self.releaseGate,DISPATCH_TIME_FOREVER);
    if(self.captured){[self record:@"release"];if(!self.failRelease)self.captured=NO;}
    if(!self.failRelease)[self setValue:nil forKey:@"capture"];
    self.releasing=NO;return !self.failRelease;
}
@end
static void until(BOOL (^predicate)(void)) {
    double end=NSProcessInfo.processInfo.systemUptime+2;
    while(!predicate() && NSProcessInfo.processInfo.systemUptime<end)[NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.002]];
    assert(predicate());
}
static BOOL pending(FakeSession *s){return [[s valueForKey:@"captureStartInProgress"] boolValue];}
static void registered(FakeSession *s){until(^BOOL{return !pending(s);});}
static FakeSession *start(FakeSource *source){FakeSession *s=[FakeSession new];assert([s startSource:source error:nil]);registered(s);return s;}
static void ready(FakeSession *s,NSUInteger generation){[s renderersReady:generation];registered(s);}
static void ack(FakeSession *s,NSUInteger generation){[s renderersStopped:YES generation:generation];until(^BOOL{return !s.running;});}
static id diagnosticLimit;
static BOOL manualSession=NO;
static id infoValue(id object,SEL command,NSString *key){(void)object;(void)command;if([key isEqual:@"MacbeeManualSessionEnabled"])return @(manualSession);return [key isEqual:@"MacbeeMaximumSharingSeconds"]?diagnosticLimit:nil;}
int main(void){@autoreleasepool{
    Method info=class_getInstanceMethod(NSBundle.class,@selector(objectForInfoDictionaryKey:));IMP oldInfo=method_setImplementation(info,(IMP)infoValue);
    MBApplicationSession *limitSession=[MBApplicationSession new];assert(limitSession.maximumSharingDuration==1800);
    diagnosticLimit=@10;assert(limitSession.maximumSharingDuration==10);
    for(id invalid in @[@0,@1801,@10.5,@"10"]){diagnosticLimit=invalid;assert(limitSession.maximumSharingDuration==1);}
    manualSession=YES;assert(limitSession.maximumSharingDuration==0);manualSession=NO;diagnosticLimit=nil;method_setImplementation(info,oldInfo);
    FakeSource *source=[FakeSource new];source.valid=YES;
    FakeSession *s=start(source);assert(![s startSource:source error:nil]);ready(s,1);assert(s.captured);[s stop];[s stop];
    assert(([s.events isEqual:@[@"register",@"prepare",@"begin",@"suspend",@"stop_renderers"]]));assert(s.captured);ack(s,1);assert(!s.captured);
    assert([s startSource:source error:nil]);registered(s);ready(s,1);assert(!s.captured);ready(s,2);assert(s.captured);
    [s renderersStopped:YES generation:1];assert(s.captured && s.running);[s stop];ack(s,2);
    s=start(source);[s stop];[s renderersReady:1];assert(!s.captured);ack(s,1);
    s=start(source);ready(s,1);[s renderersStopped:NO generation:1];s.clock=11;[s tick];assert(s.captured);s.clock=12;[s tick];until(^BOOL{return !s.running;});assert(!s.captured);
    s=start(source);ready(s,1);captureFault=YES;[s tick];assert(s.captured);ack(s,1);captureFault=NO;
    s=start(source);s.stopDuringBegin=YES;ready(s,1);assert(s.running && s.captured);assert([[s valueForKey:@"phase"] unsignedIntegerValue]==3);ack(s,1);
    s=start(source);s.stopDuringBegin=YES;s.ackDuringBegin=YES;ready(s,1);until(^BOOL{return !s.running;});assert(!s.captured);
    s=[FakeSession new];s.preparationDelay=30;assert([s startSource:source error:nil]);registered(s);s.clock=39;[s tick];assert([[s valueForKey:@"phase"] unsignedIntegerValue]==1);ready(s,1);[s stop];ack(s,1);
    s=[FakeSession new];s.invalidAfterPrepare=YES;assert([s startSource:source error:nil]);until(^BOOL{return !s.running;});assert(![s.events containsObject:@"prepare"]);source.valid=YES;
    s=start(source);s.failCapture=YES;ready(s,1);assert(!s.captured);ack(s,1);
    s=start(source);ready(s,1);s.clock=300;[s tick];assert(s.captured && s.running);s.clock=1801;[s tick];ack(s,1);
    // Explicit manual policy survives many hours and still obeys Stop/lease cleanup.
    method_setImplementation(info,(IMP)infoValue);manualSession=YES;
    s=start(source);ready(s,1);s.clock=7200;[s tick];assert(s.captured && s.running && [[s valueForKey:@"phase"] unsignedIntegerValue]==2);
    [s setValue:@((uint64_t)1800*48000+1) forKey:@"frames"];oneFrame=YES;[s tick];assert(s.running && [[s valueForKey:@"frames"] unsignedLongLongValue]==(uint64_t)1800*48000+2);
    [s stop];ack(s,1);assert(!s.captured);manualSession=NO;method_setImplementation(info,oldInfo);
    s=start(source);ready(s,1);s.clock=1700;[s renderersStopped:NO generation:1];s.clock=1711;[s tick];assert(s.captured);s.clock=1712;[s tick];until(^BOOL{return !s.running;});
    s=start(source);__block NSString *last=@"";s.statusHandler=^(NSString *message){assert(NSThread.isMainThread);last=message;};ready(s,1);s.failRelease=YES;[s stop];[s renderersStopped:YES generation:1];until(^BOOL{return [last containsString:@"Sharing is disabled"];});assert(s.running && ![s startSource:source error:nil]);
    s=[FakeSession new];s.failPrepare=YES;assert([s startSource:source error:nil]);until(^BOOL{return !s.running;});
    source.valid=NO;s=[FakeSession new];assert([s startSource:source error:nil]);until(^BOOL{return !s.running;});assert(![s.events containsObject:@"register"]);source.valid=YES;
    // A fake never-returning registration leaves the UI alive and Stop accepted.
    __block unsigned heartbeats=0;NSTimer *heartbeat=[NSTimer scheduledTimerWithTimeInterval:.002 repeats:YES block:^(NSTimer *t){(void)t;heartbeats++;}];
    s=[FakeSession new];s.registrationGate=dispatch_semaphore_create(0);assert([s startSource:source error:nil]);until(^BOOL{return s.preparing && heartbeats>=3;});
    [s stop];assert(s.running && ![s startSource:source error:nil]);s.clock=100;[s tick];assert(s.running);unsigned before=heartbeats;until(^BOOL{return heartbeats>=before+3;});assert(![s.events containsObject:@"prepare"] && ![s.events containsObject:@"begin"]);
    dispatch_semaphore_signal(s.registrationGate);until(^BOOL{return !s.running;});assert(![s.events containsObject:@"prepare"] && !s.captured);
    // A pending activation cannot be interrupted, and never resurrects Sharing.
    s=start(source);s.activationGate=dispatch_semaphore_create(0);[s renderersReady:1];until(^BOOL{return s.activating;});[s stop];[s renderersStopped:YES generation:1];assert(s.running && s.captured);before=heartbeats;until(^BOOL{return heartbeats>=before+3;});assert(![s.events containsObject:@"release"]);
    dispatch_semaphore_signal(s.activationGate);until(^BOOL{return !s.running;});assert(!s.captured);
    // Teardown also stays off-main, and no new session starts while it is stuck.
    s=start(source);ready(s,1);s.releaseGate=dispatch_semaphore_create(0);[s stop];[s renderersStopped:YES generation:1];until(^BOOL{return s.releasing;});before=heartbeats;until(^BOOL{return heartbeats>=before+3;});assert(s.running && ![s startSource:source error:nil]);
    dispatch_semaphore_signal(s.releaseGate);until(^BOOL{return !s.running;});[heartbeat invalidate];
    // First cause is explicit; an EPIPE must never appear as a normal user Stop.
    s=start(source);ready(s,1);__block NSDictionary *first=nil;
    s.diagnosticHandler=^(NSDictionary *row){if([row[@"stage"] isEqual:@"first_stop"])first=row;};
    NSPipe *broken=[NSPipe pipe];int fd=broken.fileHandleForWriting.fileDescriptor;
    assert(fcntl(fd,F_SETNOSIGPIPE,1)==0);[broken.fileHandleForReading closeFile];
    [s setValue:broken forKey:@"input"];[s setValue:[NSMutableData dataWithBytes:"MBH2" length:4] forKey:@"pending"];
    s.clock=.251;[s tick];assert([first[@"first_cause"] isEqual:@"native_pipe_error"]);
    assert([first[@"pipe_errno"] intValue]==EPIPE && [first[@"max_tick_gap"] doubleValue]>=.25);
    [s stop];assert([first[@"first_cause"] isEqual:@"native_pipe_error"]);ack(s,1);
    // Saturated nonblocking fixture pipe keeps the original 250ms safety bound.
    s=start(source);ready(s,1);first=nil;s.diagnosticHandler=^(NSDictionary *row){if([row[@"stage"] isEqual:@"first_stop"])first=row;};
    NSPipe *full=[NSPipe pipe];fd=full.fileHandleForWriting.fileDescriptor;
    assert(fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK)==0);assert(fcntl(fd,F_SETNOSIGPIPE,1)==0);
    char zero[4096]={0};while(write(fd,zero,sizeof(zero))>0){}assert(errno==EAGAIN);
    [s setValue:full forKey:@"input"];[s setValue:[NSMutableData dataWithBytes:"MBH2" length:4] forKey:@"pending"];
    [s setValue:@0 forKey:@"pendingSince"];s.clock=.251;[s tick];assert([first[@"first_cause"] isEqual:@"native_backpressure"]);ack(s,1);[full.fileHandleForReading closeFile];
    s=start(source);ready(s,1);first=nil;s.diagnosticHandler=^(NSDictionary *row){if([row[@"stage"] isEqual:@"first_stop"])first=row;};
    [s stop];assert([first[@"first_cause"] isEqual:@"user_stop"]);ack(s,1);
    // Only bounded sanitized records in an owned test directory, never the live app path.
    NSString *directory=[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
    assert(mkdir(directory.fileSystemRepresentation,0700)==0);
    assert(MBWriteStopSnapshot(directory,@{@"schema":@1,@"first_stop":first},YES));
    NSDictionary *saved=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:[directory stringByAppendingPathComponent:@"latest-native.json"]] options:0 error:nil];
    assert([saved[@"first_stop"][@"first_cause"] isEqual:@"user_stop"]);
    // A later evidenced user Stop must not erase an earlier unrequested stop.
    NSDictionary *unexpected=@{@"first_stop":@{@"first_cause":@"native_pipe_error",@"pipe_errno":@(EPIPE)}};
    assert(MBWriteStopSnapshot(directory,unexpected,YES));
    NSDictionary *normal=@{@"first_stop":@{@"first_cause":@"user_stop"},@"cleanup_verified":@YES};
    assert(MBWriteStopSnapshot(directory,normal,NO));
    saved=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:[directory stringByAppendingPathComponent:@"last-native-failure.json"]] options:0 error:nil];
    assert([saved isEqual:unexpected]);
    saved=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:[directory stringByAppendingPathComponent:@"latest-native.json"]] options:0 error:nil];
    assert([saved isEqual:normal]);
    assert(!MBWriteStopSnapshot(directory,@{@"oversize":[@"x" stringByPaddingToLength:9000 withString:@"x" startingAtIndex:0]},YES));
    [[NSFileManager defaultManager] removeItemAtPath:directory error:nil];
    puts("serialized HAL ownership/heartbeat/delayed prepare+activate+cleanup/cancel/stale epoch tests passed; effects=none");
}return 0;}
