#import <Foundation/Foundation.h>
#import "ApplicationSource.h"
NS_ASSUME_NONNULL_BEGIN
/* One explicit selected-app or System Audio session. Owns only our renderers and
 * private tap. It never controls other applications' playback/processes.
 * Ordinary sessions are manual Start/Stop; finite diagnostics remain separate.
 */
@interface MBApplicationSession : NSObject
@property(nonatomic,readonly) BOOL running;
@property(nonatomic,readonly) BOOL outputsConnected;
@property(nonatomic,readonly,copy) NSString *sharingState;
@property(nonatomic,readonly,copy,nullable) NSString *authRequiredReceiverID;
@property(nonatomic,copy,nullable) NSDictionary<NSString *,NSString *> *sshControlPaths;
@property(nonatomic,copy,nullable) void (^stateHandler)(NSString *state);
@property(nonatomic,copy,nullable) void (^diagnosticHandler)(NSDictionary *event);
@property(nonatomic,copy,nullable) void (^statusHandler)(NSString *message);
@property(nonatomic,copy,nullable) void (^outputStateHandler)(NSString *outputID,NSString *state);
/* YES accepts an asynchronous request. Completion/failure uses statusHandler.
 * running remains YES throughout canceled or stuck HAL cleanup. */
- (BOOL)startSource:(MBApplicationSource *)source error:(NSError **)error;
- (BOOL)startSource:(MBApplicationSource *)source outputs:(NSArray<NSString *> *)outputs error:(NSError **)error;
- (BOOL)startSystemAudioWithOutputs:(NSArray<NSString *> *)outputs error:(NSError **)error;
- (void)stop;
- (void)stopForApplicationExit;
@end
NS_ASSUME_NONNULL_END
