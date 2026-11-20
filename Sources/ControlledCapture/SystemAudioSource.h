#import <Foundation/Foundation.h>
#import <CoreAudio/CATapDescription.h>
#import <CoreAudio/CoreAudio.h>

NS_ASSUME_NONNULL_BEGIN
/* Future System Audio input policy. No tap, IOProc, player or listener is
 * created here. The app must obtain these PIDs from its own session children,
 * not from a name match or discovered receiver. Not wired to the release UI. */
@interface MBSystemAudioSource : NSObject
@property(nonatomic,readonly) AudioObjectID excludedRenderer;
- (nullable instancetype)initWithoutLocalRendererForOwnedBackendPID:(pid_t)backend error:(NSError **)error;
- (nullable instancetype)initWithOwnedBackendPID:(pid_t)backend
                                    guardianPID:(pid_t)guardian
                                    rendererPID:(pid_t)renderer
                             rendererExecutable:(NSString *)executable
                                          error:(NSError **)error;
/* A restart invalidates this snapshot. Do not replace it during sharing. */
- (BOOL)revalidate:(NSError **)error;
/* Constructs metadata only. The caller still owns explicit permission,
 * asynchronous preparation/activation and renderer-first Stop. */
- (nullable CATapDescription *)inactiveDescription:(NSError **)error;
@end
NS_ASSUME_NONNULL_END
