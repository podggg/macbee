/* Interpose the only attempted HAL mutation: inspect the descriptor, then fail.
 * No tap/device is created and no real app or samples are inspected. */
#import "../Sources/ControlledCapture/ApplicationSource.h"
#import "../Sources/ControlledCapture/ControlledCapture.h"
#import <CoreAudio/CATapDescription.h>
#import <CoreAudio/AudioHardwareTapping.h>
#include <assert.h>
static unsigned attempts=0;
@interface FixedSource : MBApplicationSource @end
@implementation FixedSource
- (NSArray<NSNumber *> *)audioProcesses {return @[@42,@46];}
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error {(void)output;(void)error;return YES;}
@end
OSStatus AudioHardwareCreateProcessTap(CATapDescription *description,AudioObjectID *tap){
    (void)tap;attempts++;
    assert(([description.processes isEqual:@[@42,@46]]));
    assert(!description.exclusive && description.privateTap);
    assert(description.muteBehavior==CATapMutedWhenTapped);
    if(@available(macOS 26.0,*))assert(!description.processRestoreEnabled);
    return -999;
}
OSStatus AudioDeviceStart(AudioObjectID device,AudioDeviceIOProcID proc){(void)device;(void)proc;abort();}
static void work(dispatch_queue_t queue,dispatch_block_t block){
 dispatch_semaphore_t done=dispatch_semaphore_create(0);
 dispatch_async(queue,^{block();dispatch_semaphore_signal(done);});
 assert(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC))==0);
}
int main(void){@autoreleasepool{
    dispatch_queue_t queue=dispatch_queue_create("offline.tap.fixture",DISPATCH_QUEUE_SERIAL);
    MBControlledCapture *capture=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:queue];__block NSError *error=nil;
    NSMutableArray *events=[NSMutableArray array];capture.diagnosticHandler=^(NSDictionary *event){[events addObject:event];};
    assert([capture.diagnosticSnapshot[@"callbacks"] unsignedLongLongValue]==0);
    work(queue,^{assert(![capture startForSelectedSource:[FixedSource new] error:&error]);});
    assert(attempts==1 && error.code==-999);
    assert(events.count==2);assert([events[0][@"stage"] isEqual:@"tap_create_enter"]);
    assert([events[1][@"stage"] isEqual:@"tap_create_return"] && [events[1][@"status"] intValue]==-999);work(queue,^{assert([capture stopWithError:nil]);});
    puts("exact include-only descriptor verified; no live metadata or capture");
}return 0;}
