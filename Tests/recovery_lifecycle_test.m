/* The real Stop and presentation paths, with inert Foundation objects and no GUI. */
#define MACBEE_SYSTEM_UI_TEST 1
#import "../Sources/MacbeeApp/SystemMain.m"
#import "../Sources/ControlledCapture/ControlledCapture.h"
#include <assert.h>
#include <stdatomic.h>
#include <stdio.h>

@interface MBApplicationSession (RecoveryFixture)
- (void)startAudioPumpWithLimit:(NSTimeInterval)limit;
- (void)stopAudioPump;
- (void)audioTurnForSystem:(BOOL)system bounded:(BOOL)bounded sharing:(BOOL)sharing limit:(NSTimeInterval)limit;
@end

@interface TextReceiver : NSObject
@property(copy) NSString *stringValue;
@end
@implementation TextReceiver
@end

@interface TimerReceiver : NSObject
@property BOOL invalidated;
- (void)invalidate;
@end
@implementation TimerReceiver
- (void)invalidate {assert(NSThread.isMainThread);self.invalidated=YES;}
@end

static dispatch_semaphore_t entered, leave;
static atomic_bool completed;
static void initGate(void) {
    entered=dispatch_semaphore_create(0);leave=dispatch_semaphore_create(0);atomic_store(&completed,false);
}

@interface HeldTurnSession : MBApplicationSession @end
@implementation HeldTurnSession
- (void)audioTurnForSystem:(BOOL)system bounded:(BOOL)bounded sharing:(BOOL)sharing limit:(NSTimeInterval)limit {
    (void)system;(void)bounded;(void)sharing;(void)limit;
    if(atomic_load(&completed))return;
    assert(!NSThread.isMainThread);
    dispatch_semaphore_signal(entered);
    assert(dispatch_semaphore_wait(leave,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC))==0);
    // An in-flight turn owns pending data and the final evidence clocks.
    [self setValue:@123.5 forKey:@"lastPCMRead"];
    atomic_store(&completed,true);
}
@end

@interface StopFixture : HeldTurnSession @end
@implementation StopFixture
- (void)stopAudioPump {
    if(!atomic_load(&completed))
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),
            dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{dispatch_semaphore_signal(leave);});
    [super stopAudioPump];
}
@end

static void waitFor(BOOL (^predicate)(void)) {
    NSTimeInterval end=NSProcessInfo.processInfo.systemUptime+2;
    while(!predicate() && NSProcessInfo.processInfo.systemUptime<end)
        [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.005]];
    assert(predicate());
}

static void assertStopDrainsTurn(void) {
    initGate();
    StopFixture *session=[StopFixture new];
    // Use only the controller's real Stop path; no renderer, tap or device.
    [session setValue:@2 forKey:@"phase"];
    __block BOOL sawFirstStop=NO;
    session.diagnosticHandler=^(NSDictionary *row){
        if(![row[@"stage"] isEqual:@"first_stop"])return;
        assert(atomic_load(&completed));
        assert([row[@"last_pcm_read"] doubleValue]==123.5);
        sawFirstStop=YES;
    };
    [session startAudioPumpWithLimit:0];
    assert(dispatch_semaphore_wait(entered,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC))==0);
    assert(!atomic_load(&completed));
    [session stop];
    assert(sawFirstStop);
    waitFor(^BOOL{return !session.running;});
}

static void assertTimerInvalidationIsOnMain(void) {
    initGate();
    HeldTurnSession *session=[HeldTurnSession new];
    TimerReceiver *timer=[TimerReceiver new];
    [session setValue:timer forKey:@"timer"];
    [session startAudioPumpWithLimit:0];
    assert(dispatch_semaphore_wait(entered,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC))==0);
    assert(!timer.invalidated);
    // Drop the owner while a real dispatch source's callback holds the object.
    session=nil;
    assert(!timer.invalidated);
    dispatch_semaphore_signal(leave);
    waitFor(^BOOL{return timer.invalidated;});
    assert(atomic_load(&completed));
}

@interface CapturedBlock : NSObject
@property BOOL delivered;
@end
@implementation CapturedBlock
- (BOOL)hasFault{return NO;}
- (NSDictionary *)diagnosticSnapshot{return @{};}
- (size_t)readStereoFrames:(float *)out capacity:(size_t)capacity captureTime:(uint64_t *)first {
    if(self.delivered)return 0;assert(capacity>=1);self.delivered=YES;
    out[0]=.25f;out[1]=-.25f;*first=193400012345ULL;return 1;
}
@end

static void assertNativePacketCarriesOriginalBlockTime(void) {
    // Clock oracle belongs to the source fixture, not to either wake of the pump.
    MBApplicationSession *session=[MBApplicationSession new];NSPipe *pipe=[NSPipe pipe];
    [session setValue:[CapturedBlock new] forKey:@"capture"];
    [session setValue:pipe forKey:@"input"];[session setValue:[NSMutableData data] forKey:@"pending"];
    [session audioTurnForSystem:YES bounded:YES sharing:YES limit:0];
    [NSThread sleepForTimeInterval:.06];
    [session audioTurnForSystem:YES bounded:YES sharing:YES limit:0];
    NSData *packet=[pipe.fileHandleForReading readDataOfLength:20];assert(packet.length==20);
    const uint8_t *bytes=packet.bytes;assert(memcmp(bytes,"MBP4",4)==0 && bytes[4]==1);
    uint64_t ns=0;for(unsigned i=0;i<8;i++)ns|=(uint64_t)bytes[8+i]<<(8*i);
    assert(ns==193400012345ULL);
    [session setValue:nil forKey:@"capture"];
}

static void assertRecoveryLabelUsesPresentedState(void) {
    assert(NSApp==nil);
    MBSystemApp *controller=[MBSystemApp new];
    controller.session=[MBApplicationSession new];
    TextReceiver *status=[TextReceiver new],*hint=[TextReceiver new];
    controller.status=(NSTextField *)(id)status;
    controller.hint=(NSTextField *)(id)hint;
    [controller.session setValue:@2 forKey:@"phase"];
    [controller.session setValue:@"degraded" forKey:@"sharingState"];
    [controller refresh:nil];
    assert([status.stringValue isEqual:@"Recovering audio"]);
    assert([hint.stringValue containsString:@"still connected"]);
    [controller.session setValue:@"playing" forKey:@"sharingState"];
    [controller refresh:nil];
    assert([status.stringValue isEqual:@"Playing"]);
    assert(![hint.stringValue containsString:@"recovering"]);
    assert(NSApp==nil && controller.window==nil);
}

@interface JitterySmallCallbacks : NSObject
@property NSUInteger remaining;
@end
@implementation JitterySmallCallbacks
- (BOOL)hasFault{return NO;}
- (NSDictionary *)diagnosticSnapshot{return @{@"callbacks":@4};}
- (size_t)queuedFrames{return self.remaining*128;}
- (size_t)readStereoFrames:(float *)out capacity:(size_t)capacity captureTime:(uint64_t *)first {
    if(!self.remaining)return 0;
    assert(capacity>=128);
    NSUInteger index=4-self.remaining;self.remaining--;
    for(size_t i=0;i<256;i++)out[i]=(i%2)?-.125f:.125f;
    // Simulate 128-frame callbacks split by >=30 us drift-compensation jitter.
    *first=200000000000ULL+(uint64_t)index*(2666667ULL+30000ULL);
    return 128;
}
@end

static void assertJitterySmallCallbacksDrainInOneTurn(void) {
    MBApplicationSession *session=[MBApplicationSession new];NSPipe *pipe=[NSPipe pipe];
    JitterySmallCallbacks *cap=[JitterySmallCallbacks new];cap.remaining=4;
    [session setValue:cap forKey:@"capture"];
    [session setValue:pipe forKey:@"input"];[session setValue:[NSMutableData data] forKey:@"pending"];
    [session audioTurnForSystem:YES bounded:YES sharing:YES limit:0];
    assert(cap.remaining==0);
    // All four 128-frame MBP4 packets (4 * (16 + 512) = 2112 bytes) flushed immediately.
    NSData *packets=[pipe.fileHandleForReading readDataOfLength:4*(16+128*4)];
    assert(packets.length==4*(16+128*4));
    const uint8_t *bytes=packets.bytes;
    for(NSUInteger i=0;i<4;i++)assert(memcmp(bytes+i*(16+512),"MBP4",4)==0);
    [session setValue:nil forKey:@"capture"];
}

int main(void){@autoreleasepool{
    assertNativePacketCarriesOriginalBlockTime();
    assertJitterySmallCallbacksDrainInOneTurn();
    assertStopDrainsTurn();
    assertTimerInvalidationIsOnMain();
    assertRecoveryLabelUsesPresentedState();
    puts("Stop evidence follows the in-flight turn; timer cleanup is on main; recovery is presented without GUI");
}return 0;}
