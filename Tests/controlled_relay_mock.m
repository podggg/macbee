/* Test-only link replacement. No Core Audio, sockets, SSH, UI, or capture. */
#import "../Sources/ControlledCapture/ControlledCapture.h"
#import "../Sources/ControlledCapture/ControlledRelay.h"
#include <math.h>
#include <stdlib.h>
#include <stdio.h>
typedef struct {size_t frame;BOOL enabled;} MockState;
@implementation MBControlledCapture
- (BOOL)prepareForSystemAudioSource:(MBSystemAudioSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)systemHealthValid {return NO;}
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue {(void)queue;return [super init];}
- (void)cancelPendingStart {}
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;return YES;}
- (BOOL)activatePreparedSource:(NSError **)error {(void)error;return YES;}
- (NSDictionary *)diagnosticSnapshot {return @{@"callbacks":@1,@"frames":@512};}
- (instancetype)init {self=[super init];if(self)_state=calloc(1,sizeof(MockState));return self;}
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error {
    (void)error;((MockState *)_state)->enabled=helper.isRunning;return helper.isRunning;
}
- (size_t)readStereoFrames:(float *)output capacity:(size_t)capacity {
    MockState *s=_state;if(!s->enabled)return 0;
    size_t n=MIN(capacity,312000-s->frame);
    for(size_t i=0;i<n;i++){
        size_t at=s->frame+i,within=at%48000;
        float value=(at<288000 && within<4800)?(float)(163*sin(2*M_PI*880*within/48000)*pow(sin(M_PI*within/4800),2)/32768):0;
        output[i*2]=output[i*2+1]=value;
    }
    if(n && getenv("MACBEE_MOCK_BAD_AMPLITUDE"))output[0]=1;
    s->frame+=n;return n;
}
- (BOOL)hasFault {return NO;}
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;return NO;}
- (void)suspendReadingKeepingTap {((MockState *)_state)->enabled=NO;}
- (BOOL)stopWithError:(NSError **)error {(void)error;((MockState *)_state)->enabled=NO;return YES;}
- (void)dealloc {free(_state);}
@end

int main(void){@autoreleasepool{
    NSString *mode=NSProcessInfo.processInfo.environment[@"MACBEE_MOCK_CASE"]?:@"normal";
    if([mode hasPrefix:@"speaker_"])mode=[mode substringFromIndex:8];
    MBControlledRelay *relay=[MBControlledRelay new];__block NSString *last=@"";__block BOOL passed=NO;
    relay.statusHandler=^(NSString *text){last=text;if([text containsString:@"relay passed"])passed=YES;puts(text.UTF8String);};
    for(int attempt=0;attempt<([mode isEqual:@"restart"]?2:1);attempt++){
        passed=NO;NSError *error=nil;if(![relay startWithError:&error])return 10;
        if([relay startWithError:nil])return 11; // repeated Start must not launch again
        NSTimeInterval begin=NSProcessInfo.processInfo.systemUptime;
        while(relay.running && NSProcessInfo.processInfo.systemUptime-begin<5){
            [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.01]];
            if([mode isEqual:@"cancel"] && NSProcessInfo.processInfo.systemUptime-begin>.1){[relay stop];[relay stop];}
        }
        // Let final protocol/termination callbacks settle; do not infer success
        // just because subprocesses stopped.
        [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.2]];
        if(relay.running)return 12;
        BOOL expected=[mode isEqual:@"normal"] || [mode isEqual:@"restart"];
        if(passed!=expected || !last.length)return 13;
    }
    puts("mock lifecycle passed; audio_opened=false; network_opened=false");
}return 0;}
