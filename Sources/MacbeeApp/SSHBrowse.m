#import "SSHBrowse.h"

@interface MBSSHBrowse ()
@property(nonatomic, strong) NSNetServiceBrowser *browser;
@property(nonatomic, strong) NSMutableDictionary<NSArray *, NSNetService *> *services;
@property(nonatomic, strong) NSMutableDictionary<NSArray *, NSDictionary *> *rows;
@end

@implementation MBSSHBrowse
- (instancetype)init {
    if ((self = [super init])) {
        _services = [NSMutableDictionary dictionary];
        _rows = [NSMutableDictionary dictionary];
    }
    return self;
}
- (void)emit:(NSString *)status {
    if (!self.updated) return;
    NSSortDescriptor *byName = [NSSortDescriptor sortDescriptorWithKey:@"name"
        ascending:YES selector:@selector(localizedCaseInsensitiveCompare:)];
    self.updated([self.rows.allValues sortedArrayUsingDescriptors:@[byName]], status);
}
- (NSNetServiceBrowser *)newBrowser { return [NSNetServiceBrowser new]; }
- (void)startBrowsing {
    [self stopBrowsing];
    self.browser = [self newBrowser];
    self.browser.delegate = self;
    [self.browser searchForServicesOfType:@"_ssh._tcp." inDomain:@"local."];
}
- (void)stopBrowsing {
    self.browser.delegate = nil;
    [self.browser stop];
    self.browser = nil;
    for (NSNetService *service in self.services.allValues) {
        service.delegate = nil;
        [service stop];
    }
    [self.services removeAllObjects];
    [self.rows removeAllObjects];
}
- (void)netServiceBrowser:(NSNetServiceBrowser *)browser didNotSearch:(NSDictionary *)error {
    (void)error;
    if (browser != self.browser) return;
    [self stopBrowsing];
    [self emit:@"Local network browsing is unavailable. SSH aliases and manual entry still work."];
}
- (NSArray *)key:(NSNetService *)service {
    return @[service.domain ?: @"", service.type ?: @"", service.name ?: @""];
}
- (void)netServiceBrowser:(NSNetServiceBrowser *)browser didFindService:(NSNetService *)service moreComing:(BOOL)more {
    (void)more;
    if (browser != self.browser || self.services.count >= 64 ||
        [service.type caseInsensitiveCompare:@"_ssh._tcp."] != NSOrderedSame ||
        [service.domain caseInsensitiveCompare:@"local."] != NSOrderedSame) return;
    NSArray *key = [self key:service];
    if (self.services[key]) return;
    self.services[key] = service;
    service.delegate = self;
    // Resolve the advertised SRV record only. Never open an SSH connection.
    [service resolveWithTimeout:3.0];
}
- (void)netServiceDidResolveAddress:(NSNetService *)service {
    NSArray *key = [self key:service];
    if (self.services[key] != service || !service.hostName.length ||
        service.hostName.length > 253 || service.port < 1 || service.port > 65535) return;
    static NSCharacterSet *bad;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        bad = [[NSCharacterSet characterSetWithCharactersInString:
            @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_"] invertedSet];
    });
    NSString *host = service.hostName.lowercaseString;
    // Names only, never interpret the display name as a host, command or option.
    if ([host hasPrefix:@"-"] || [host hasPrefix:@"."] || [host containsString:@".."] ||
        [host rangeOfCharacterFromSet:bad].location != NSNotFound) return;
    NSMutableCharacterSet *noControls = [NSCharacterSet.controlCharacterSet mutableCopy];
    NSString *name = [[service.name componentsSeparatedByCharactersInSet:noControls] componentsJoinedByString:@" "];
    if (!name.length || name.length > 120) name = host;
    self.rows[key] = @{@"name": name, @"host": host, @"port": @(service.port), @"user": NSUserName()};
    [self emit:@""];
}
- (void)netService:(NSNetService *)service didNotResolve:(NSDictionary *)error {
    (void)error;
    // Retain the bounded service entry until removal rather than retrying.
}
- (void)netServiceBrowser:(NSNetServiceBrowser *)browser didRemoveService:(NSNetService *)service moreComing:(BOOL)more {
    (void)more;
    if (browser != self.browser) return;
    NSArray *key = [self key:service];
    NSNetService *saved = self.services[key];
    saved.delegate = nil;
    [saved stop];
    [self.services removeObjectForKey:key];
    [self.rows removeObjectForKey:key];
    [self emit:@""];
}
- (void)dealloc {
    [self stopBrowsing];
}
@end
