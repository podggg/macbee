#import "ApplicationPreview.h"
#import "../ControlledCapture/ControlledCapture.h"
@interface MBApplicationSession (PreviewHooks)
- (void)renderersReady:(NSUInteger)generation;
- (void)renderersStopped:(BOOL)verified generation:(NSUInteger)generation;
@end
@interface MBPreviewSource : MBApplicationSource
@property NSString *fixtureName;
@end
@implementation MBPreviewSource
- (NSString *)name {return self.fixtureName;}
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error {(void)output;(void)error;return YES;}
@end
NSArray<MBApplicationSource *> *MBPreviewSources(void) {
    MBPreviewSource *music=[MBPreviewSource new];music.fixtureName=@"Example Music App";
    MBPreviewSource *player=[MBPreviewSource new];player.fixtureName=@"Example Video Player";
    return @[music,player];
}
@implementation MBApplicationPreviewSession
- (BOOL)prepareSelectedCapture:(NSError **)error {
    (void)error;
    // Deliberately delayed pure fixture. The controller must run this off-main.
    NSNumber *delay=[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeePreviewRegistrationDelaySeconds"];
    NSTimeInterval seconds=MAX(0,MIN(120,delay.doubleValue));
    if(seconds>0){NSAssert(!NSThread.isMainThread,@"Simulated registration must not block the UI");[NSThread sleepForTimeInterval:seconds];}
    return YES;
}
- (BOOL)prepareRenderers:(NSError **)error {
    (void)error;NSUInteger epoch=[[self valueForKey:@"generation"] unsignedIntegerValue];
    __weak MBApplicationPreviewSession *weak=self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{[weak renderersReady:epoch];});
    return YES;
}
- (BOOL)beginSelectedCapture:(NSError **)error {(void)error;return YES;}
- (void)suspendCapture {}
- (void)stopRenderers {
    NSUInteger epoch=[[self valueForKey:@"generation"] unsignedIntegerValue];
    __weak MBApplicationPreviewSession *weak=self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{[weak renderersStopped:YES generation:epoch];});
}
- (BOOL)releaseCapture {return YES;}
@end
/* Do not link the real tap implementation into the preview. Any accidental
 * invocation fails locally rather than ever asking for capture permission. */
@implementation MBControlledCapture
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue {(void)queue;return [super init];}
- (void)cancelPendingStart {}
- (NSDictionary *)diagnosticSnapshot {return @{};}
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (BOOL)activatePreparedSource:(NSError **)error {(void)error;abort();}
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error {(void)helper;(void)error;abort();}
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error {(void)source;(void)error;abort();}
- (size_t)readStereoFrames:(float *)destination capacity:(size_t)capacity {(void)destination;(void)capacity;abort();}
- (BOOL)hasFault {abort();}
- (void)suspendReadingKeepingTap {abort();}
- (BOOL)stopWithError:(NSError **)error {(void)error;abort();}
@end
