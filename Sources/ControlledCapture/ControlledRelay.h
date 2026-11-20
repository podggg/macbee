#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
/* Owned-helper diagnostic only; no arbitrary PID/app/global capture API.
 * C2 null mode passed in its frozen app. C3 speaker-mode source preparation is
 * not yet deployed or authorized to run. The bundle chooses the diagnostic;
 * output must be described honestly in its UI and separately approved.
 */
@interface MBControlledRelay : NSObject
@property(nonatomic,copy,nullable) void (^statusHandler)(NSString *message);
@property(nonatomic,readonly) BOOL running;
- (BOOL)startWithError:(NSError **)error;
- (void)stop;
@end
NS_ASSUME_NONNULL_END
