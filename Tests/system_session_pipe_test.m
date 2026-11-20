/* Real native controller and Python stdin/stdout. Every HAL operation is fake. */
#import "../Sources/ControlledCapture/ApplicationSession.h"
#import "../Sources/ControlledCapture/ControlledCapture.h"
#include <assert.h>
#include <sys/qos.h>
#include <stdatomic.h>
static _Atomic bool captured=false,released=false,healthy=true;
static NSTimeInterval began=0;
static _Atomic bool preparationEntered;
static uint64_t captureEpoch;
static NSUInteger activitiesBegun=0,activitiesEnded=0;
static dispatch_semaphore_t preparationGate;
static BOOL mainStalls=NO;static size_t generated=0;
@implementation MBControlledCapture
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue {(void)queue;return [super init];}
- (void)cancelPendingStart {}
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)prepareForSystemAudioSource:(MBSystemAudioSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)activatePreparedSource:(NSError **)error {(void)error;captured=YES;captureEpoch=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);began=NSProcessInfo.processInfo.systemUptime;return YES;}
- (NSDictionary *)diagnosticSnapshot {return @{@"callbacks":@1,@"frames":@480};}
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error {(void)helper;(void)error;abort();}
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)systemHealthValid {return captured && healthy;}
- (size_t)readStereoFrames:(float *)output capacity:(size_t)capacity {
    assert(capacity>=480);NSTimeInterval elapsed=NSProcessInfo.processInfo.systemUptime-began;
    if(mainStalls){
        assert(!NSThread.isMainThread && qos_class_self()==QOS_CLASS_USER_INTERACTIVE);
        size_t ready=(size_t)(elapsed*100)*480;
        size_t frames=MIN(capacity,ready-generated);
        for(size_t i=0;i<frames;i++)output[2*i]=output[2*i+1]=(float)(1+(generated+i)/480)/32768.f;
        generated+=frames;return frames;
    }
    if(elapsed<.9)return 0; // Eighteen simulated controller seconds of silence.
    float sample=(elapsed>1.2 && elapsed<1.6)?.03f:.01f; // A, then A+B, then A.
    for(size_t i=0;i<960;i++)output[i]=sample;return 480;
}
- (size_t)readStereoFrames:(float *)out capacity:(size_t)capacity captureTime:(uint64_t *)stamp {
    size_t first=generated;
    size_t count=[self readStereoFrames:out capacity:capacity];
    *stamp=mainStalls?captureEpoch+(uint64_t)first*1000000000/48000:clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    return count;
}
- (BOOL)hasFault {return NO;}
- (void)suspendReadingKeepingTap {}
- (BOOL)stopWithError:(NSError **)error {(void)error;assert(!NSThread.isMainThread);released=YES;return YES;}
@end
@interface SystemFixtureSession : MBApplicationSession @end
@implementation SystemFixtureSession
- (id)beginPresentationActivity {assert(NSThread.isMainThread && activitiesBegun==0);activitiesBegun++;return @"synthetic-activity-token";}
- (void)endPresentationActivity:(id)activity {assert([activity isEqual:@"synthetic-activity-token"] && activitiesEnded==0);activitiesEnded++;}
- (NSTimeInterval)now {return NSProcessInfo.processInfo.systemUptime*((mainStalls || strcmp(getenv("MACBEE_SYSTEM_CASE")?:"normal","native_pressure")==0)?1:20);}
- (BOOL)prepareSystemCapture:(NSError **)error {
    (void)error;assert(!NSThread.isMainThread);[self setValue:[MBControlledCapture new] forKey:@"capture"];
    if(strcmp(getenv("MACBEE_SYSTEM_CASE")?:"normal","cancel_prepare")==0){
        atomic_store(&preparationEntered,true);
        assert(dispatch_semaphore_wait(preparationGate,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC))==0);
    }
    return YES;
}
@end
int main(void){@autoreleasepool{
    NSString *mode=[NSString stringWithUTF8String:getenv("MACBEE_SYSTEM_CASE")?:"normal"];
    mainStalls=[mode isEqual:@"main_stalls"];
    BOOL longOwner=[mode isEqual:@"owner_hung"];unsigned mainPauses=0;const double gaps[]={.05,.06,.07,.10,.15,.20,.06,.06,.06,.06,.06,.06,.06};
    atomic_init(&preparationEntered,false);preparationGate=dispatch_semaphore_create(0);
    SystemFixtureSession *session=[SystemFixtureSession new];__block NSString *last=@"";NSMutableArray *states=[NSMutableArray array];
    session.statusHandler=^(NSString *text){last=text;};session.stateHandler=^(NSString *state){[states addObject:state];};
    __block NSUInteger losses=0;session.outputStateHandler=^(NSString *output,NSString *state){assert([output isEqual:@"remote"] && [state isEqual:@"offline"]);losses++;};
    NSArray *outputs=[mode isEqual:@"configured_four"]?@[@"local",@"r-0000000000000001",@"r-0000000000000002",@"r-0000000000000003"]:@[@"local",@"remote"];
    assert(([session startSystemAudioWithOutputs:outputs error:nil]));
    NSTimeInterval start=NSProcessInfo.processInfo.systemUptime;BOOL requested=NO;NSUInteger heartbeats=0;
    while(session.running && NSProcessInfo.processInfo.systemUptime-start<6){
        [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.005]];heartbeats++;
        NSTimeInterval elapsed=NSProcessInfo.processInfo.systemUptime-start;
        if(longOwner && !mainPauses && captured && elapsed>.3){
            mainPauses++;[NSThread sleepForTimeInterval:2.25];
        }
        if(mainStalls && mainPauses<sizeof(gaps)/sizeof(gaps[0]) && elapsed>.6+mainPauses*.26){
            [NSThread sleepForTimeInterval:gaps[mainPauses++]];
        }
        if([mode isEqual:@"health_loss"] && captured && elapsed>1.1)healthy=NO;
        BOOL cancel=[mode isEqual:@"cancel_prepare"] && atomic_load(&preparationEntered) && !captured;
        if(!longOwner && !requested && (cancel || elapsed>(mainStalls?4.35:([mode isEqual:@"native_pressure"]?4.05:2.2)))){requested=YES;[session stop];if(cancel)dispatch_semaphore_signal(preparationGate);}
    }
    if(session.running)fprintf(stderr,"session did not finish: %s; %s\n",last.UTF8String,[[session valueForKey:@"failureReason"] UTF8String]);
    assert(!session.running && heartbeats>2);
    assert(activitiesBegun==activitiesEnded && activitiesBegun==(captured?1U:0U));
    if(mainStalls){assert(mainPauses==13 && [session.sharingState isEqual:@"idle"]);}
    if([mode isEqual:@"wrong_outputs"]){assert(!captured);assert([last containsString:@"Unexpected destinations"]);}
    else if([mode isEqual:@"cancel_prepare"]){assert(!captured && released);}
    else{
        assert(captured && released);assert([states containsObject:@"waiting"]);
        if(mainStalls || [mode isEqual:@"qos"] || [mode isEqual:@"normal"] || [mode isEqual:@"native_pressure"] || [mode isEqual:@"configured_four"] || [mode isEqual:@"output_loss_clean"]){assert([states containsObject:@"playing"]);assert([last containsString:@"Selected outputs exited"]);}
        else if(longOwner){assert([[session valueForKey:@"firstStopCode"] isEqual:@"native_owner_unresponsive"]);}
        else if([mode hasSuffix:@"_limit"]){
            NSString *code=[[mode componentsSeparatedByString:@"_"][0] stringByAppendingString:@"_pacing_recovery_limit"];
            assert([[session valueForKey:@"firstBackendCode"] isEqual:code]);
        }else assert([last containsString:@"health could not be verified"]);
        if([mode isEqual:@"native_pressure"])assert([states containsObject:@"degraded"]);
    }
    if([mode isEqual:@"output_loss_clean"]){
        assert(losses==1 && [session.sharingState isEqual:@"idle"]);
        assert([[session valueForKey:@"firstStopCode"] isEqual:@"user_stop"]);
        assert(![[session valueForKey:@"failureReason"] length] && [[session valueForKey:@"cleanupVerified"] boolValue]);
    }
    puts("system native/pipe integration passed; no live metadata, capture, output or network");
}return 0;}
