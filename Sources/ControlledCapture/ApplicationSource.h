#import <Cocoa/Cocoa.h>
#import <CoreAudio/CoreAudio.h>
NS_ASSUME_NONNULL_BEGIN
/* Public process metadata only. No tap, microphone, media or window contents.
 * Immutable selection. Bundled applications include only verified, fixed direct-child
 * audio service identities. Never dynamically expands or selects tabs.
 */
@interface MBApplicationSource : NSObject
@property(nonatomic,readonly) pid_t pid;
@property(nonatomic,readonly) AudioObjectID audioProcess; // Compatibility: first included object.
@property(nonatomic,readonly,copy) NSArray<NSNumber *> *audioProcesses;
@property(nonatomic,readonly,copy) NSString *bundleID;
@property(nonatomic,readonly,copy) NSString *name;
@property(nonatomic,readonly,copy) NSDate *launchDate;
+ (NSArray<MBApplicationSource *> *)availableSourcesWithError:(NSError **)error;
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error;
@end
NS_ASSUME_NONNULL_END
