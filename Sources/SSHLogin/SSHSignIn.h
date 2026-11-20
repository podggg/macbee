#import <Cocoa/Cocoa.h>
NS_ASSUME_NONNULL_BEGIN
/// One bounded user-initiated interactive process, not a background credential service.
/// The owner retains this object until the task exits or is cancelled.
@interface MBSSHSignIn : NSObject
@property(nonatomic, readonly, nullable) NSTask *task;
@property(nonatomic, readonly, nullable) NSString *socketPath;
@property(nonatomic, readonly) BOOL userCancelled;
@property(nonatomic, readonly) BOOL refusedPrompt;
@property(nonatomic, readonly, copy, nullable) NSString *promptedIdentityPath;
+ (nullable NSString *)identityPathFromPrompt:(NSString *)prompt;
+ (NSArray<NSString *> *)argumentsForSystemSSH:(NSArray<NSString *> *)arguments;
- (BOOL)permitsExecutable:(NSString *)path;
- (BOOL)isSSHExecutable:(NSString *)path;
/// The task URL must be /usr/bin/ssh or /usr/bin/ssh-add. Its streams are set
/// by the caller; no password is passed in its arguments or environment.
- (BOOL)launchTask:(NSTask *)task forDestination:(NSString *)destination
            window:(NSWindow *)window error:(NSError **)error;
/// Cancels the active sign-in attempt as a user cancellation and terminates the task.
- (void)cancel;
/// On authenticated ControlMaster readiness call this with NO immediately:
/// invalidates the authentication deadline without terminating the verified master.
/// Pass the active Add Mac sheet window, not its sheet-occupied parent.
/// Indirect interactive ProxyJump helpers are not authorized by this channel.
/// On cancel or failed preflight pass YES to terminate this owned task.
- (void)finishTerminatingTask:(BOOL)terminate;
@end
NS_ASSUME_NONNULL_END
