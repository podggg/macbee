// Fully in memory: never browse the network, request permission or open a socket.
#import "../Sources/MacbeeApp/SSHBrowse.h"
#import <Foundation/Foundation.h>
@interface BrowserDouble : NSObject
@property(nonatomic, weak) id delegate;
@property(nonatomic, strong) NSString *type;
@property(nonatomic, strong) NSString *domain;
@property(nonatomic) BOOL stopped;
@end
@implementation BrowserDouble
- (void)searchForServicesOfType:(NSString *)s inDomain:(NSString *)d { self.type=s; self.domain=d; }
- (void)stop { self.stopped=YES; }
@end
@interface BrowseFixture : MBSSHBrowse
@property(nonatomic, strong) BrowserDouble *fake;
@end
@implementation BrowseFixture
- (NSNetServiceBrowser *)newBrowser { self.fake=[BrowserDouble new]; return (id)self.fake; }
@end
@interface ServiceDouble : NSObject
@property(nonatomic, weak) id delegate;
@property(nonatomic, strong) NSString *type, *domain, *name, *hostName;
@property(nonatomic) NSInteger port;
@property(nonatomic) BOOL resolved, stopped;
@end
@implementation ServiceDouble
- (void)resolveWithTimeout:(NSTimeInterval)timeout { self.resolved=timeout>0; }
- (void)stop { self.stopped=YES; }
@end
int main(void) { @autoreleasepool {
    BrowseFixture *b=[BrowseFixture new]; NSCAssert(b.fake==nil,@"Must not browse on construction");
    __block NSArray *rows=nil; __block NSString *status=nil;
    b.updated=^(NSArray *r,NSString *s){ rows=r;status=s; };
    [b startBrowsing]; NSCAssert([b.fake.type isEqual:@"_ssh._tcp."] && [b.fake.domain isEqual:@"local."],@"Wrong service");
    ServiceDouble *s=[ServiceDouble new];s.name=@"Fixture";s.type=@"_ssh._tcp.";s.domain=@"local.";s.hostName=@"Receiver.local.";s.port=2222;
    [b netServiceBrowser:(id)b.fake didFindService:(id)s moreComing:NO];
    NSCAssert(s.resolved,@"Must resolve the SRV advertisement");
    [b netServiceDidResolveAddress:(id)s];
    NSCAssert(rows.count==1 && [rows[0][@"host"] isEqual:@"receiver.local."] && [rows[0][@"port"] integerValue]==2222,@"Bad resolved row");
    [b netServiceBrowser:(id)b.fake didRemoveService:(id)s moreComing:NO];
    NSCAssert(!rows.count && s.stopped,@"Stale service not removed");
    [b netServiceBrowser:(id)b.fake didNotSearch:@{}];
    NSCAssert(b.fake.stopped && [status containsString:@"manual entry"],@"Permission fallback");
    NSLog(@"passed: synthetic Bonjour lifecycle; no network");
} return 0; }
