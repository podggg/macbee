/* Real native process/pipe/session integration, fake source and fake tap only. */
#import "../Sources/ControlledCapture/ApplicationSession.h"
#import "../Sources/ControlledCapture/ControlledCapture.h"
#include <assert.h>
static BOOL captured=NO,released=NO;
@implementation MBControlledCapture
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue {(void)queue;return [super init];}
- (void)cancelPendingStart {}
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;return YES;}
- (BOOL)prepareForSystemAudioSource:(MBSystemAudioSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)systemHealthValid {return NO;}
- (BOOL)activatePreparedSource:(NSError **)error {(void)error;captured=YES;return YES;}
- (NSDictionary *)diagnosticSnapshot {return @{@"callbacks":@1,@"frames":@512};}
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error {(void)helper;(void)error;abort();}
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;captured=YES;return YES;}
- (size_t)readStereoFrames:(float *)destination capacity:(size_t)capacity {
    assert(capacity>=512);for(size_t i=0;i<512*2;i++)destination[i]=(i%2)?.5f:-.5f;return 512;
}
- (BOOL)hasFault {return NO;}
- (void)suspendReadingKeepingTap {}
- (BOOL)stopWithError:(NSError **)error {(void)error;released=YES;return YES;}
@end
@interface PipeSource : MBApplicationSource @end
@implementation PipeSource
- (NSString *)name {return @"Owned in-memory fixture";}
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error {(void)output;(void)error;return YES;}
@end
@interface FastSession : MBApplicationSession @end
@implementation FastSession
- (NSTimeInterval)now {return NSProcessInfo.processInfo.systemUptime*20;}
- (NSTimeInterval)maximumSharingDuration {return 10;}
@end
int main(void){@autoreleasepool{
    FastSession *session=[FastSession new];__block NSString *last=@"";
    session.statusHandler=^(NSString *message){last=message;};
    __block NSMutableArray *stages=[NSMutableArray array];
    session.diagnosticHandler=^(NSDictionary *event){@synchronized(stages){[stages addObject:event[@"stage"]];}assert(event[@"uptime"]);};NSError *error=nil;
    assert([session startSource:[PipeSource new] error:&error]);
    NSTimeInterval end=NSProcessInfo.processInfo.systemUptime+5;
    while(session.running && NSProcessInfo.processInfo.systemUptime<end)
        [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.01]];
    assert(!session.running);
    NSString *mode=[NSString stringWithUTF8String:getenv("MACBEE_PIPE_CASE")?:"normal"];
    assert([stages containsObject:@"backend_stdout_received"]);assert([stages containsObject:@"backend_ready_main_dispatch"]);
    if([mode isEqual:@"wrong_sink"]){assert(![stages containsObject:@"capture_start_returned"]);assert(!captured);assert([last containsString:@"Unexpected destinations"]);}
    else {assert(captured && released);assert([stages containsObject:@"capture_start_returned"]);assert([stages containsObject:@"first_pcm_packet"]);assert([stages containsObject:@"first_pipe_write"]);assert([stages containsObject:@"first_nonzero_pcm_packet"]);assert([stages containsObject:@"capture_release"]);}
    if([mode isEqual:@"normal"]){assert([last containsString:@"Both buffered players exited"]);}
    else if([mode isEqual:@"missing_ack"] || [mode isEqual:@"invalid_ack"]){assert([last containsString:@"not fully verified"]);}
    else if([mode isEqual:@"backend_error"] || [mode isEqual:@"failed_unverified"]){assert([last containsString:@"simulated peer loss"]);assert(![last containsString:@"Backend exited without"]);}
    else if([mode isEqual:@"stopping_error"]){assert([last containsString:@"simulated device change"]);assert(![last containsString:@"Backend exited without"]);assert([last containsString:@"not fully verified"]);}
    printf("%s; audio_opened=false; network_opened=false\n",last.UTF8String);
}return 0;}
