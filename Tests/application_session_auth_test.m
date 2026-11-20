// Exercises real ApplicationSession.m prepareRenderers: MACBEE_SSH_CONTROL_PATHS passing,
// auth_required + authRequiredReceiverID parsing, immediate finish without 12s lease wait,
// and report: firing before setState:. No real HAL or network.
#import "../Sources/ControlledCapture/ApplicationSession.h"
#import "../Sources/ControlledCapture/ControlledCapture.h"
#include <assert.h>
#include <stdio.h>

@implementation MBControlledCapture
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue { (void)queue; return [super init]; }
- (void)cancelPendingStart {}
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error { (void)source; (void)error; return NO; }
- (BOOL)prepareForSystemAudioSource:(MBSystemAudioSource *)source error:(NSError **)error { (void)source; (void)error; return NO; }
- (BOOL)activatePreparedSource:(NSError **)error { (void)error; return NO; }
- (NSDictionary *)diagnosticSnapshot { return @{}; }
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error { (void)helper; (void)error; return NO; }
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error { (void)source; (void)error; return NO; }
- (BOOL)systemHealthValid { return NO; }
- (size_t)readStereoFrames:(float *)output capacity:(size_t)capacity { (void)output; (void)capacity; return 0; }
- (size_t)readStereoFrames:(float *)output capacity:(size_t)capacity captureTime:(uint64_t *)stamp { (void)output; (void)capacity; (void)stamp; return 0; }
- (BOOL)hasFault { return NO; }
- (void)suspendReadingKeepingTap {}
- (BOOL)stopWithError:(NSError **)error { (void)error; return YES; }
@end

int main(void) {
    @autoreleasepool {
        MBApplicationSession *session = [MBApplicationSession new];
        NSMutableArray<NSString *> *order = [NSMutableArray array];
        __block NSString *lastStatus = @"";
        __block NSString *finalState = @"";
        session.statusHandler = ^(NSString *message) {
            lastStatus = message;
            [order addObject:@"status"];
        };
        session.stateHandler = ^(NSString *state) {
            finalState = state;
            if ([state isEqual:@"failed"] || [state isEqual:@"idle"]) {
                [order addObject:@"final_state"];
            }
        };
        session.sshControlPaths = @{@"r-0000000000000001": @"/tmp/macbee-ctl.TEST1234/%C"};
        NSTimeInterval start = NSProcessInfo.processInfo.systemUptime;
        assert(([session startSystemAudioWithOutputs:@[@"local", @"r-0000000000000001"] error:nil]));
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:3.0];
        while (session.running && deadline.timeIntervalSinceNow > 0) {
            [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
        }
        NSTimeInterval elapsed = NSProcessInfo.processInfo.systemUptime - start;
        assert(!session.running);
        assert(elapsed < 2.0); // Proves no 12s MBRestoreTimeout stall on preflight auth_required.
        assert([session.authRequiredReceiverID isEqual:@"r-0000000000000001"]);
        assert([finalState isEqual:@"failed"]);
        assert([lastStatus containsString:@"Authentication required"]);
        NSUInteger lastStatusIdx = [order indexOfObjectWithOptions:NSEnumerationReverse
                                                       passingTest:^BOOL(NSString *obj, NSUInteger idx, BOOL *stop) {
            (void)idx; (void)stop;
            return [obj isEqual:@"status"];
        }];
        NSUInteger finalStateIdx = [order indexOfObject:@"final_state"];
        assert(lastStatusIdx != NSNotFound && finalStateIdx != NSNotFound && lastStatusIdx < finalStateIdx);
        puts("passed: ApplicationSession MACBEE_SSH_CONTROL_PATHS, authRequiredReceiverID, instant finish, report-before-state");
    }
    return 0;
}
