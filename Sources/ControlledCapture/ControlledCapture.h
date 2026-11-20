#import <Foundation/Foundation.h>
#include "../AudioPipeline/pcm_ring.h"

NS_ASSUME_NONNULL_BEGIN
@class MBApplicationSource,MBSystemAudioSource;
/* Legacy selected-app diagnostic cap; explicit manual sessions use renewable
 * liveness instead. Synthetic helper capture remains 15 seconds. */
enum { MBApplicationSessionMaxSeconds = 1800 };
/* Starting any new source is a capture-permission boundary.
 * Frozen diagnostic apps use only their own live NSTask source.
 * The separately prepared selected-app path requires an immutable identity.
 * System Audio is a separate opt-in build and requires owned-renderer exclusion.
 * This interface has no microphone API.
 */
@interface MBControlledCapture : NSObject {
@private
    void *_state;
    NSTask *_helper;
    NSTimer *_watchdog;
    dispatch_queue_t _selectedLifecycleQueue;
    dispatch_source_t _selectedWatchdog;
    NSLock *_readLock;
    BOOL (^_isSourceAlive)(void);
    BOOL _deferTeardown;
    BOOL _systemSource;
}
@property(nonatomic,copy,nullable) void (^diagnosticHandler)(NSDictionary *event);
/* Numeric lifecycle/counter metadata only; never sample buffers. */
- (NSDictionary *)diagnosticSnapshot;
/* Selected-app HAL lifecycle and source checks must use this one serial queue.
 * cancelPendingStart and atomic counters/reading may be called from main. */
- (instancetype)initWithSelectedLifecycleQueue:(dispatch_queue_t)queue;
- (void)cancelPendingStart;
- (BOOL)startForOwnedHelper:(NSTask *)helper error:(NSError **)error;
/* Prepared selected-app path; not used by any frozen synthetic app. */
- (BOOL)startForSelectedSource:(MBApplicationSource *)source error:(NSError **)error;
/* Registration may wait for OS consent. Preparation never starts input IO.
 * Activate only after the exact source and both owned renderers are ready. */
- (BOOL)prepareForSelectedSource:(MBApplicationSource *)source error:(NSError **)error;
/* Explicit future build only. The caller must already own verified renderers. */
- (BOOL)prepareForSystemAudioSource:(MBSystemAudioSource *)source error:(NSError **)error;
- (BOOL)systemHealthValid;
- (BOOL)activatePreparedSource:(NSError **)error;
- (void)suspendReadingKeepingTap;
- (size_t)readStereoFrames:(float *)destination capacity:(size_t)capacity;
- (BOOL)hasFault;
- (BOOL)stopWithError:(NSError **)error;
@end
@interface MBControlledCapture (SystemCaptureTime)
- (size_t)readStereoFrames:(float *)destination capacity:(size_t)capacity captureTime:(uint64_t * _Nullable)firstFrameHostNs;
- (size_t)queuedFrames;
@end
NS_ASSUME_NONNULL_END
