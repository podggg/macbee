#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
/// Bonjour is browse-only, opt-in per visible Add Mac sheet. SSH services are
/// candidates, not evidence that the receiver is a Mac or has granted access.
@interface MBSSHBrowse : NSObject <NSNetServiceBrowserDelegate, NSNetServiceDelegate>
@property(nonatomic, copy, nullable) void (^updated)(NSArray<NSDictionary *> *rows, NSString *status);
- (void)startBrowsing;
- (void)stopBrowsing;
@end
NS_ASSUME_NONNULL_END
