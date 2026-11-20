#import <Cocoa/Cocoa.h>
#import "SSHSignIn.h"

NS_ASSUME_NONNULL_BEGIN

/// Per-receiver foreground SSH ControlMaster owner and endpoint validation helpers.
/// Uses a private 0700 socket directory under /tmp (< 104 bytes sun_path) and
/// closes the master deterministically on Stop and Quit via `ssh -O exit`.
@interface MBSSHControlMaster : NSObject

@property(nonatomic, readonly, copy, nullable) NSString *controlDirectory;
@property(nonatomic, readonly, copy, nullable) NSString *controlPath;
@property(nonatomic, readonly, copy, nullable) NSString *destination;
@property(nonatomic, readonly, copy, nullable) NSArray<NSString *> *sshArguments;
@property(nonatomic, readonly, copy, nullable) NSString *promptedIdentityPath;
@property(nonatomic, readonly) BOOL active;
@property(nonatomic, readonly) BOOL opening;

/// Normalizes a manual SSH destination input. If the user pasted `ssh <dest>`
/// with a single non-flag token, strips the leading `ssh ` prefix safely.
+ (NSString *)normalizeManualAddress:(NSString *)raw strippedSSHPrefix:(BOOL *_Nullable)strippedOut;

/// Parses and validates `[user@]host[:port]`, IPv6 `user@[addr]:port`, or SSH alias
/// with parity to `tools/ssh_discovery.py:endpoint`.
+ (nullable NSArray<NSString *> *)sshArgumentsForEndpoint:(NSString *)value
                                                canonical:(NSString *_Nullable *_Nullable)canonicalOut
                                                    error:(NSError *_Nullable *_Nullable)error;

/// Computes `destination_key` parity dictionary (`address` or `unresolved`).
+ (nullable NSDictionary *)destinationKeyForEndpoint:(NSString *)value
                                             aliases:(nullable NSArray<NSDictionary *> *)aliases;

+ (BOOL)isDestination:(NSString *)value
     configuredInKeys:(NSArray<NSDictionary *> *)configuredKeys
              aliases:(nullable NSArray<NSDictionary *> *)aliases;

/// Subclass hooks for test doubles (production always uses system /usr/bin/ssh & ssh-add).
- (NSString *)sshExecutablePath;
- (NSString *)sshAddExecutablePath;
- (MBSSHSignIn *)newSignIn;

/// Starts a foreground SSH ControlMaster (`-N -o ControlMaster=yes -o ControlPersist=no`)
/// through MBSSHSignIn, presenting any prompts on `window`.
- (void)openForDestination:(NSString *)destination
                    window:(NSWindow *)window
                completion:(void (^)(BOOL ok, BOOL cancelled, NSError *_Nullable error))completion;

/// Checks whether the ControlMaster is currently alive (`ssh -O check`).
- (BOOL)isAliveSync;

/// Cancels any in-progress open or closes an active ControlMaster (`ssh -O exit`).
- (void)closeSynchronously:(BOOL)sync;
- (void)waitForPendingClose;

/// Opt-in A5 Keychain storage via `/usr/bin/ssh-add --apple-use-keychain -- <identity>`.
/// Only called when the user explicitly checks "Remember passphrase in Keychain".
- (void)saveIdentityToKeychain:(NSString *)identityPath
                        window:(NSWindow *)window
                    completion:(void (^)(BOOL saved, BOOL cancelled, NSError *_Nullable error))completion;

@end

/// Tracks active ControlMasters keyed by receiver ID (`r-...` or `remote`)
/// plus an optional pending master created during Add Mac before ID assignment.
@interface MBSSHControlMasterPool : NSObject

- (nullable MBSSHControlMaster *)liveMasterForReceiverID:(NSString *)receiverID;
- (void)adoptMaster:(MBSSHControlMaster *)master forReceiverID:(NSString *)receiverID;
- (NSDictionary<NSString *, NSString *> *)controlPathsForReceiverIDs:(NSArray<NSString *> *)receiverIDs;
- (void)closeMasterForReceiverID:(NSString *)receiverID synchronously:(BOOL)sync;
- (void)closeAllSynchronously:(BOOL)sync;

@end

NS_ASSUME_NONNULL_END
