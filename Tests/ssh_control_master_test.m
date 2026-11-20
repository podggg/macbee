// Headless acceptance test for A3 ControlMaster lifecycle, A4 AddMacSheet UX/layout,
// and A5 Keychain opt-in using fake ssh/ssh-add + real macbee-askpass. No real network or Keychain.
#import <Cocoa/Cocoa.h>
#import <CommonCrypto/CommonDigest.h>
#import "../Sources/MacbeeApp/AddMacSheet.h"
#import "../Sources/SSHLogin/SSHControlMaster.h"
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <sys/stat.h>

@interface MBAddMacSheet (TestAccess)
@property(nonatomic, readonly) NSView *usernameSection;
@property(nonatomic, readonly) NSView *addressSection;
@property(nonatomic, readonly) NSView *targetSection;
@end

static NSColor *resolveSRGBColor(NSColor *color, NSAppearanceName appearanceName) {
    NSAppearance *appearance = [NSAppearance appearanceNamed:appearanceName];
    __block NSColor *resolved = nil;
    [appearance performAsCurrentDrawingAppearance:^{
        resolved = [color colorUsingColorSpace:NSColorSpace.sRGBColorSpace];
    }];
    return resolved;
}

@interface MBSSHSignIn (TestSubclass)
@property(nonatomic, weak) NSWindow *window;
+ (NSDictionary *)classifyPrompt:(NSString *)prompt hint:(unsigned)hint;
- (BOOL)permitsExecutable:(NSString *)path;
- (BOOL)isSSHExecutable:(NSString *)path;
- (NSString *)bundledHelper;
- (void)presentPrompt:(NSString *)prompt hint:(unsigned)hint completion:(void (^)(NSData *))completion;
@end

@interface CtlTestSignIn : MBSSHSignIn
@property(nonatomic, copy) NSString *fakeSSH, *fakeSSHAdd, *helperURL, *syntheticSecret;
@property(nonatomic) BOOL simulateCancel;
@property(nonatomic) NSUInteger promptsSeen;
@property(nonatomic, strong) NSMutableArray<NSWindow *> *promptWindows;
@end

@implementation CtlTestSignIn
- (instancetype)init {
    if ((self = [super init])) _promptWindows = [NSMutableArray array];
    return self;
}
- (BOOL)permitsExecutable:(NSString *)path {
    return [path isEqual:self.fakeSSH] || [path isEqual:self.fakeSSHAdd];
}
- (BOOL)isSSHExecutable:(NSString *)path {
    return [path isEqual:self.fakeSSH];
}
- (NSString *)bundledHelper { return self.helperURL; }
- (void)presentPrompt:(NSString *)prompt hint:(unsigned)hint completion:(void (^)(NSData *))completion {
    self.promptsSeen++;
    if (self.window) [self.promptWindows addObject:self.window];
    NSDictionary *kind = [MBSSHSignIn classifyPrompt:prompt hint:hint];
    if (!kind || self.simulateCancel) {
        [self setValue:@YES forKey:@"userCancelled"];
        completion(nil);
        return;
    }
    if ([kind[@"kind"] isEqual:@"secret"]) {
        NSString *identity = [MBSSHSignIn identityPathFromPrompt:prompt];
        if (identity.length) [self setValue:identity forKey:@"promptedIdentityPath"];
        completion([[self.syntheticSecret dataUsingEncoding:NSUTF8StringEncoding] mutableCopy]);
    } else {
        completion([[kind[@"fingerprint"] dataUsingEncoding:NSUTF8StringEncoding] mutableCopy]);
    }
}
@end

@interface CtlTestMaster : MBSSHControlMaster
@property(nonatomic, copy) NSString *fakeSSH, *fakeSSHAdd, *helperURL, *syntheticSecret;
@property(nonatomic) BOOL simulateCancel;
@property(nonatomic, strong, nullable) CtlTestSignIn *lastSignIn;
@property(nonatomic) NSUInteger totalPrompts;
@property(nonatomic, strong) NSMutableArray<NSWindow *> *seenWindows;
@end

@implementation CtlTestMaster
- (instancetype)init {
    if ((self = [super init])) _seenWindows = [NSMutableArray array];
    return self;
}
- (NSString *)sshExecutablePath { return self.fakeSSH; }
- (NSString *)sshAddExecutablePath { return self.fakeSSHAdd; }
- (MBSSHSignIn *)newSignIn {
    CtlTestSignIn *s = [CtlTestSignIn new];
    s.fakeSSH = self.fakeSSH;
    s.fakeSSHAdd = self.fakeSSHAdd;
    s.helperURL = self.helperURL;
    s.syntheticSecret = self.syntheticSecret;
    s.simulateCancel = self.simulateCancel;
    self.lastSignIn = s;
    return s;
}
@end

@interface FakeBrowserDouble : NSObject
@property(nonatomic, weak) id delegate;
@property(nonatomic) BOOL started, stopped;
@end
@implementation FakeBrowserDouble
- (void)searchForServicesOfType:(NSString *)s inDomain:(NSString *)d {
    assert([s isEqual:@"_ssh._tcp."] && [d isEqual:@"local."]);
    self.started = YES;
}
- (void)stop { self.stopped = YES; }
@end

@interface FakeBrowseController : MBSSHBrowse
@property(nonatomic, strong) FakeBrowserDouble *fake;
@property(nonatomic) NSUInteger starts, stops;
@end
@implementation FakeBrowseController
- (NSNetServiceBrowser *)newBrowser {
    self.fake = [FakeBrowserDouble new];
    return (id)self.fake;
}
- (void)startBrowsing { self.starts++; [super startBrowsing]; }
- (void)stopBrowsing { self.stops++; [super stopBrowsing]; }
@end

static NSString *sha256Hex(NSString *s) {
    NSData *d = [s dataUsingEncoding:NSUTF8StringEncoding];
    unsigned char h[32];
    CC_SHA256(d.bytes, (CC_LONG)d.length, h);
    NSMutableString *out = [NSMutableString stringWithCapacity:64];
    for (unsigned i = 0; i < 32; i++) [out appendFormat:@"%02x", h[i]];
    return out;
}

static void pumpUntil(NSTimeInterval timeout, BOOL (^pred)(void)) {
    NSDate *until = [NSDate dateWithTimeIntervalSinceNow:timeout];
    while (!pred() && until.timeIntervalSinceNow > 0) {
        [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
    }
    assert(pred());
}

static BOOL containsAllTexts(NSView *root, NSString *needle) {
    if ([root isKindOfClass:NSTextField.class]) {
        NSTextField *tf = (NSTextField *)root;
        if ([tf.stringValue containsString:needle] || [tf.placeholderString containsString:needle]) return YES;
    }
    for (NSView *sub in root.subviews) {
        if (containsAllTexts(sub, needle)) return YES;
    }
    return NO;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        assert(argc == 5);
        NSString *fakeSSH = [NSString stringWithUTF8String:argv[1]];
        NSString *fakeSSHAdd = [NSString stringWithUTF8String:argv[2]];
        NSString *askpassHelper = [NSString stringWithUTF8String:argv[3]];
        NSString *scratch = [NSString stringWithUTF8String:argv[4]];

        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];

        NSString *secret = [@"synthetic-ctl-secret-" stringByAppendingString:NSUUID.UUID.UUIDString];
        NSString *expectedHex = sha256Hex(secret);
        NSString *keyFile = [scratch stringByAppendingPathComponent:@"id_ed25519_fixture"];
        assert([@"PRIVATE" writeToFile:keyFile atomically:YES encoding:NSUTF8StringEncoding error:nil]);
        NSString *argvLog = [scratch stringByAppendingPathComponent:@"argv.log"];
        NSString *ctlLog = [scratch stringByAppendingPathComponent:@"ctl.log"];
        NSString *addLog = [scratch stringByAppendingPathComponent:@"ssh_add.log"];
        NSString *pidFile = [scratch stringByAppendingPathComponent:@"pids.log"];
        NSString *sshDir = [scratch stringByAppendingPathComponent:@".ssh"];
        assert([NSFileManager.defaultManager createDirectoryAtPath:sshDir
                                       withIntermediateDirectories:YES
                                                        attributes:@{NSFilePosixPermissions: @0700}
                                                             error:nil]);
        NSString *knownHosts = [sshDir stringByAppendingPathComponent:@"known_hosts"];
        assert([@"existing.example ssh-ed25519 AAAA\n" writeToFile:knownHosts atomically:YES encoding:NSUTF8StringEncoding error:nil]);

        setenv("HOME", scratch.fileSystemRepresentation, 1);
        setenv("FIXTURE_EXPECTED_SHA256", expectedHex.UTF8String, 1);
        setenv("FIXTURE_KEY_PATH", keyFile.UTF8String, 1);
        setenv("FIXTURE_ARGV_LOG", argvLog.UTF8String, 1);
        setenv("FIXTURE_CTL_LOG", ctlLog.UTF8String, 1);
        setenv("FIXTURE_SSH_ADD_LOG", addLog.UTF8String, 1);
        setenv("FIXTURE_PID_FILE", pidFile.UTF8String, 1);
        setenv("FIXTURE_FP_SHA256", sha256Hex(@"SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB").UTF8String, 1);

        // 1. Endpoint normalization & ssh prefix stripping tests.
        BOOL stripped = NO;
        assert([[MBSSHControlMaster normalizeManualAddress:@"  ssh  alex@192.168.1.20  " strippedSSHPrefix:&stripped]
                isEqual:@"alex@192.168.1.20"] && stripped);
        assert(![MBSSHControlMaster sshArgumentsForEndpoint:[MBSSHControlMaster normalizeManualAddress:@"ssh -oProxyCommand=bad host" strippedSSHPrefix:&stripped] canonical:nil error:nil] && !stripped);
        assert(![MBSSHControlMaster sshArgumentsForEndpoint:[MBSSHControlMaster normalizeManualAddress:@"ssh user@host extra" strippedSSHPrefix:&stripped] canonical:nil error:nil] && !stripped);
        NSString *canon = nil;
        NSArray *v6args = [MBSSHControlMaster sshArgumentsForEndpoint:@"alex@[2001:0db8::1]:2222" canonical:&canon error:nil];
        assert(([canon isEqual:@"alex@[2001:db8::1]:2222"] && [v6args isEqual:@[@"-p", @"2222", @"--", @"alex@2001:db8::1"]]));

        NSWindow *parentWindow = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 440, 520)
                                                             styleMask:NSWindowStyleMaskTitled
                                                               backing:NSBackingStoreBuffered
                                                                 defer:NO];
        NSWindow *sheetHostWindow = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 480, 560)
                                                                styleMask:NSWindowStyleMaskTitled
                                                                  backing:NSBackingStoreBuffered
                                                                    defer:NO];

        // 2. Passphrase + adoption + -O check + -O exit lifecycle.
        setenv("FIXTURE_MODE", "passphrase", 1);
        CtlTestMaster *m1 = [CtlTestMaster new];
        m1.fakeSSH = fakeSSH; m1.fakeSSHAdd = fakeSSHAdd; m1.helperURL = askpassHelper; m1.syntheticSecret = secret;
        __block BOOL done = NO, gotOK = NO, gotCancel = NO;
        __block NSError *gotErr = nil;
        [m1 openForDestination:@"alex@192.168.1.20" window:sheetHostWindow completion:^(BOOL ok, BOOL cancelled, NSError *err) {
            gotOK = ok; gotCancel = cancelled; gotErr = err; done = YES;
        }];
        pumpUntil(8.0, ^BOOL{ return done; });
        assert(gotOK && !gotCancel && !gotErr);
        assert(m1.active && m1.controlDirectory.length && m1.controlPath.length);
        assert(strlen(m1.controlPath.fileSystemRepresentation) + 40 - 2 <= 86); // <dir>/ + 40-char %C
        struct stat dst;
        assert(lstat(m1.controlDirectory.fileSystemRepresentation, &dst) == 0 && (dst.st_mode & 0777) == 0700);
        assert([m1.promptedIdentityPath isEqual:keyFile]);
        assert([m1 isAliveSync]);
        NSString *ctlDirSaved = m1.controlDirectory;
        NSString *argvText = [NSString stringWithContentsOfFile:argvLog encoding:NSUTF8StringEncoding error:nil];
        assert([argvText containsString:@"-o StrictHostKeyChecking=ask"]);
        assert([argvText containsString:@"-o UpdateHostKeys=no"]);
        assert([argvText containsString:@"-o FingerprintHash=sha256"]);
        assert([argvText containsString:@"-o BatchMode=no"]);
        assert([argvText containsString:@"-o ControlMaster=yes"]);
        assert([argvText containsString:@"-o ControlPersist=no"]);
        assert([argvText containsString:@"-T -N -- alex@192.168.1.20"]);

        NSString *pidsText = [NSString stringWithContentsOfFile:pidFile encoding:NSUTF8StringEncoding error:nil];
        pid_t m1Pid = (pid_t)[[pidsText componentsSeparatedByCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].firstObject intValue];
        assert(m1Pid > 1 && kill(m1Pid, 0) == 0);

        [m1 closeSynchronously:YES];
        assert(!m1.active);
        assert(![NSFileManager.defaultManager fileExistsAtPath:ctlDirSaved]);
        assert(kill(m1Pid, 0) != 0 && errno == ESRCH);
        NSString *ctlText = [NSString stringWithContentsOfFile:ctlLog encoding:NSUTF8StringEncoding error:nil];
        assert([ctlText containsString:@"-O check"]);
        assert([ctlText containsString:@"-O exit"]);

        // 3. Wrong passphrase rejected cleanly.
        setenv("FIXTURE_EXPECTED_SHA256", "0000000000000000000000000000000000000000000000000000000000000000", 1);
        done = NO; gotOK = NO; gotCancel = NO; gotErr = nil;
        [m1 openForDestination:@"alex@192.168.1.20" window:sheetHostWindow completion:^(BOOL ok, BOOL cancelled, NSError *err) {
            gotOK = ok; gotCancel = cancelled; gotErr = err; done = YES;
        }];
        pumpUntil(8.0, ^BOOL{ return done; });
        assert(!gotOK && !gotCancel && gotErr && [gotErr.localizedDescription containsString:@"refused"]);
        assert(!m1.active && !m1.controlDirectory);

        // 4. Host-key trust + passphrase.
        setenv("FIXTURE_EXPECTED_SHA256", expectedHex.UTF8String, 1);
        setenv("FIXTURE_MODE", "trust_then_passphrase", 1);
        done = NO; gotOK = NO; gotCancel = NO; gotErr = nil;
        [m1 openForDestination:@"fixture.local" window:sheetHostWindow completion:^(BOOL ok, BOOL cancelled, NSError *err) {
            gotOK = ok; gotCancel = cancelled; gotErr = err; done = YES;
        }];
        pumpUntil(8.0, ^BOOL{ return done; });
        assert(gotOK && !gotCancel && !gotErr && m1.lastSignIn.promptsSeen == 2);
        [m1 closeSynchronously:YES];

        // 5. Changed host key refused without askpass and known_hosts untouched.
        setenv("FIXTURE_MODE", "changed_key", 1);
        done = NO; gotOK = NO; gotCancel = NO; gotErr = nil;
        [m1 openForDestination:@"fixture.local" window:sheetHostWindow completion:^(BOOL ok, BOOL cancelled, NSError *err) {
            gotOK = ok; gotCancel = cancelled; gotErr = err; done = YES;
        }];
        pumpUntil(8.0, ^BOOL{ return done; });
        assert(!gotOK && !gotCancel && gotErr && [gotErr.localizedDescription containsString:@"changed"]);
        assert(m1.lastSignIn.promptsSeen == 0);
        assert([[NSString stringWithContentsOfFile:knownHosts encoding:NSUTF8StringEncoding error:nil]
                isEqual:@"existing.example ssh-ed25519 AAAA\n"]);

        // 6. Cancel cleans up task and control directory.
        setenv("FIXTURE_MODE", "cancel", 1);
        m1.simulateCancel = YES;
        done = NO; gotOK = NO; gotCancel = NO; gotErr = nil;
        [m1 openForDestination:@"fixture.local" window:sheetHostWindow completion:^(BOOL ok, BOOL cancelled, NSError *err) {
            gotOK = ok; gotCancel = cancelled; gotErr = err; done = YES;
        }];
        pumpUntil(8.0, ^BOOL{ return done; });
        assert(!gotOK && gotCancel && !gotErr && !m1.active && !m1.controlDirectory);
        m1.simulateCancel = NO;

        // 7. AddMacSheet headless layout, UX, Bonjour, Aliases, Added badge, denied fallback & Keychain opt-in.
        setenv("FIXTURE_MODE", "passphrase", 1);
        FakeBrowseController *fakeBrowse = [FakeBrowseController new];
        assert(fakeBrowse.starts == 0 && fakeBrowse.fake == nil);

        NSMutableArray<NSArray *> *setupCalls = [NSMutableArray array];
        __block MBSSHControlMaster *capturedMasterFromSheet = nil;
        MBAddMacSheet *sheet = [[MBAddMacSheet alloc] initWithSetupRunner:^(NSArray<NSString *> *args, void (^cb)(NSDictionary *)) {
            [setupCalls addObject:args];
            if ([args.firstObject isEqual:@"--discover"]) {
                cb(@{
                    @"ok": @YES,
                    @"result": @{
                        @"aliases": @[
                            @{@"alias": @"ConfiguredAlias", @"host": @"10.0.0.10", @"user": @"alex", @"port": @22, @"configured": @YES, @"preview": @"static"},
                            @{@"alias": @"OfficeAlias", @"host": @"10.0.0.20", @"user": @"alex", @"port": @2222, @"configured": @NO, @"preview": @"static"}
                        ],
                        @"configured_keys": @[
                            @{@"kind": @"address", @"host": @"10.0.0.10", @"port": @22},
                            @{@"kind": @"address", @"host": @"living.local", @"port": @22}
                        ],
                        @"warnings": @[]
                    }
                });
            } else if ([args.firstObject isEqual:@"--add"]) {
                cb(@{@"ok": @YES, @"result": @{@"id": @"r-1234567890abcdef", @"name": @"Studio", @"selected": @NO, @"availability": @"available"}});
            }
        }];
        sheet.browserFactory = ^MBSSHBrowse *{ return fakeBrowse; };
        sheet.controlMasterFactory = ^MBSSHControlMaster *{
            CtlTestMaster *cm = [CtlTestMaster new];
            cm.fakeSSH = fakeSSH; cm.fakeSSHAdd = fakeSSHAdd; cm.helperURL = askpassHelper; cm.syntheticSecret = secret;
            capturedMasterFromSheet = cm;
            return cm;
        };

        [sheet buildSheet];
        assert(!sheet.sheetWindow.visible);
        assert([sheet.sheetWindow.backgroundColor isEqual:NSColor.windowBackgroundColor]);
        assert(sheet.rememberKeychain.state == NSControlStateValueOff);
        assert([sheet.usernameField.placeholderString isEqual:@"alex"]);
        assert([sheet.addressField.placeholderString containsString:@"username@192.168.1.20"]);
        assert(containsAllTexts(sheet.sheetWindow.contentView, @"Format: user@host"));
        assert(containsAllTexts(sheet.sheetWindow.contentView, @"alex@192.168.1.20"));

        // Present on parentWindow? Keep headless without displaying visible window by testing discovery + Bonjour + layout directly,
        // and verify presentOnWindow: sets sheetWindow (NOT parentWindow!) for MBSSHSignIn prompts.
        [sheet applyDiscoveryResult:@{
            @"ok": @YES,
            @"result": @{
                @"aliases": @[
                    @{@"alias": @"ConfiguredAlias", @"host": @"10.0.0.10", @"user": @"alex", @"port": @22, @"configured": @YES, @"preview": @"static"},
                    @{@"alias": @"OfficeAlias", @"host": @"10.0.0.20", @"user": @"alex", @"port": @2222, @"configured": @NO, @"preview": @"static"}
                ],
                @"configured_keys": @[
                    @{@"kind": @"address", @"host": @"10.0.0.10", @"port": @22},
                    @{@"kind": @"address", @"host": @"living.local", @"port": @22}
                ],
                @"warnings": @[]
            }
        }];
        sheet.localHostName = @"ThisBox.local";
        [sheet applyBonjourRows:@[
            @{@"name": @"This Mac Name", @"host": @"thisbox.local.", @"port": @22, @"user": NSUserName()},
            @{@"name": @"Living Room", @"host": @"living.local.", @"port": @22, @"user": NSUserName()},
            @{@"name": @"Studio Mac", @"host": @"studio.local.", @"port": @22, @"user": NSUserName()}
        ] status:@""];

        // Items: 3 Bonjour (This Mac + Added + Studio Mac) + 2 Aliases + 1 Manual = 6 items.
        assert(sheet.items.count == 6);
        assert(sheet.items[0].isThisMac == YES);  // This Mac -> non-selectable!
        assert(sheet.items[1].configured == YES); // Living Room (living.local) -> Added!
        assert(sheet.items[2].configured == NO);  // Studio Mac (auto-selected!)
        assert(sheet.items[3].configured == YES); // ConfiguredAlias -> Added!
        assert(sheet.items[4].configured == NO);  // OfficeAlias
        assert(sheet.items[5].kind == MBAddMacItemManual);
        assert(sheet.tableView.selectedRow == 2); // Auto-selection skips This Mac and Added!

        // This Mac and Added rows cannot be selected.
        assert(![sheet selectItemAtIndex:0]);
        assert(![sheet tableView:sheet.tableView shouldSelectRow:0]);
        assert(![sheet selectItemAtIndex:1]);
        assert(![sheet tableView:sheet.tableView shouldSelectRow:1]);
        assert(![sheet selectItemAtIndex:3]);
        assert(![sheet tableView:sheet.tableView shouldSelectRow:3]);

        NSView *thisMacCell = [sheet tableView:sheet.tableView viewForTableColumn:sheet.tableView.tableColumns.firstObject row:0];
        assert(containsAllTexts(thisMacCell, @"This Mac"));
        assert(!containsAllTexts(thisMacCell, @"Added"));
        NSView *addedCell = [sheet tableView:sheet.tableView viewForTableColumn:sheet.tableView.tableColumns.firstObject row:1];
        assert(containsAllTexts(addedCell, @"Added"));

        // Verify selected-row contrast: NSBackgroundStyleEmphasized switches title & subtitle to high-contrast white
        // and NSBackgroundStyleNormal restores semantic Light/Dark colors cleanly.
        MBAddMacRowCellView *studioCell = (MBAddMacRowCellView *)[sheet tableView:sheet.tableView viewForTableColumn:sheet.tableView.tableColumns.firstObject row:2];
        assert([studioCell isKindOfClass:MBAddMacRowCellView.class]);
        [studioCell setBackgroundStyle:NSBackgroundStyleEmphasized];
        assert([studioCell.titleLabel.textColor isEqual:NSColor.alternateSelectedControlTextColor]);
        for (NSAppearanceName appName in @[NSAppearanceNameAqua, NSAppearanceNameDarkAqua]) {
            NSColor *emphTitle = resolveSRGBColor(studioCell.titleLabel.textColor, appName);
            NSColor *emphSub = resolveSRGBColor(studioCell.subtitleLabel.textColor, appName);
            assert(emphTitle.redComponent > 0.85 && emphTitle.greenComponent > 0.85 && emphTitle.blueComponent > 0.85 && emphTitle.alphaComponent > 0.95);
            assert(emphSub.redComponent > 0.85 && emphSub.greenComponent > 0.85 && emphSub.blueComponent > 0.85 && emphSub.alphaComponent >= 0.80);
        }
        [studioCell setBackgroundStyle:NSBackgroundStyleNormal];
        assert([studioCell.titleLabel.textColor isEqual:NSColor.labelColor]);
        assert([studioCell.subtitleLabel.textColor isEqual:NSColor.secondaryLabelColor]);
        NSColor *aquaTitle = resolveSRGBColor(studioCell.titleLabel.textColor, NSAppearanceNameAqua);
        NSColor *darkTitle = resolveSRGBColor(studioCell.titleLabel.textColor, NSAppearanceNameDarkAqua);
        assert(aquaTitle.redComponent < 0.25 && darkTitle.redComponent > 0.75);

        MBAddMacRowCellView *disabledCell = (MBAddMacRowCellView *)thisMacCell;
        assert([disabledCell isKindOfClass:MBAddMacRowCellView.class] && disabledCell.rowDisabled);
        assert([disabledCell.titleLabel.textColor isEqual:NSColor.secondaryLabelColor]);
        assert([disabledCell.subtitleLabel.textColor isEqual:NSColor.tertiaryLabelColor]);
        [disabledCell setBackgroundStyle:NSBackgroundStyleEmphasized];
        assert([disabledCell.badgeLabel.textColor isEqual:NSColor.alternateSelectedControlTextColor]);
        [disabledCell setBackgroundStyle:NSBackgroundStyleNormal];
        assert([disabledCell.badgeLabel.textColor isEqual:NSColor.secondaryLabelColor]);

        // Also test default NSProcessInfo.processInfo.hostName detection + bare hostname matching.
        MBAddMacSheet *selfHostSheet = [[MBAddMacSheet alloc] initWithSetupRunner:^(NSArray<NSString *> *args, void (^cb)(NSDictionary *)) {
            (void)args; cb(@{@"ok": @YES, @"result": @{@"aliases": @[], @"configured_keys": @[], @"warnings": @[]}});
        }];
        [selfHostSheet buildSheet];
        NSString *realLocal = NSProcessInfo.processInfo.hostName;
        if (realLocal.length) {
            NSString *bonjourSelf = [realLocal hasSuffix:@"."] ? realLocal : [realLocal stringByAppendingString:@"."];
            [selfHostSheet applyBonjourRows:@[@{@"name": @"Local Self", @"host": bonjourSelf.uppercaseString, @"port": @22, @"user": NSUserName()}] status:@""];
            assert(selfHostSheet.items.count == 2);
            assert(selfHostSheet.items[0].isThisMac == YES);
            assert(selfHostSheet.tableView.selectedRow == 1); // Falls back to Enter address manually…!
            assert(![selfHostSheet selectItemAtIndex:0]);
        }
        selfHostSheet.localHostName = @"BareBox";
        [selfHostSheet applyBonjourRows:@[@{@"name": @"Bare Self", @"host": @"barebox.local.", @"port": @22, @"user": NSUserName()}] status:@""];
        assert(selfHostSheet.items[0].isThisMac == YES);

        // Select Bonjour row -> NSUserName() prefilled & editable, Device name filled, target preview updates.
        assert([sheet selectItemAtIndex:2]);
        assert([sheet.usernameField.stringValue isEqual:NSUserName()]);
        assert([sheet.nameField.stringValue isEqual:@"Studio Mac"]);
        assert(!sheet.usernameSection.hidden && sheet.addressSection.hidden && !sheet.targetSection.hidden);
        sheet.usernameField.stringValue = @"alex";
        [sheet refreshControls:nil];
        assert([sheet.targetPreviewLabel.stringValue isEqual:@"alex@studio.local"]);

        // Select Alias row -> resolved target shown.
        assert([sheet selectItemAtIndex:4]);
        assert([sheet.nameField.stringValue isEqual:@"OfficeAlias"]);
        assert(sheet.usernameSection.hidden && sheet.addressSection.hidden && !sheet.targetSection.hidden);
        assert([sheet.targetPreviewLabel.stringValue isEqual:@"OfficeAlias → alex@10.0.0.20:2222"]);

        // Local-network permission denied fallback keeps aliases + manual entry working.
        [sheet applyBonjourRows:@[] status:@"Local network browsing is unavailable. SSH aliases and manual entry still work."];
        assert([sheet.discoveryStatusLabel.stringValue containsString:@"Local network browsing is unavailable"]);
        assert(sheet.items.count == 3); // 2 Aliases + Manual
        assert([sheet selectItemAtIndex:1]); // OfficeAlias still selectable!

        // Manual entry + layout check at minimum size (440x520).
        assert([sheet selectItemAtIndex:2]);
        assert(sheet.usernameSection.hidden && !sheet.addressSection.hidden && sheet.targetSection.hidden);
        [sheet.sheetWindow setContentSize:NSMakeSize(440, 520)];
        [sheet.sheetWindow.contentView layoutSubtreeIfNeeded];
        NSView *cv = sheet.sheetWindow.contentView;
        assert(NSContainsRect(cv.bounds, [sheet.tableView.enclosingScrollView convertRect:sheet.tableView.enclosingScrollView.bounds toView:cv]));
        assert(NSContainsRect(cv.bounds, [sheet.nameField convertRect:sheet.nameField.bounds toView:cv]));
        assert(NSContainsRect(cv.bounds, [sheet.addressField convertRect:sheet.addressField.bounds toView:cv]));
        assert(NSContainsRect(cv.bounds, [sheet.rememberKeychain convertRect:sheet.rememberKeychain.bounds toView:cv]));
        assert(NSContainsRect(cv.bounds, [sheet.cancelButton convertRect:sheet.cancelButton.bounds toView:cv]));
        assert(NSContainsRect(cv.bounds, [sheet.connectButton convertRect:sheet.connectButton.bounds toView:cv]));
        assert(!sheet.sheetWindow.visible && !parentWindow.visible);

        // 8. Connect & Add via Manual with `ssh ` prefix stripped and Keychain OFF -> ssh-add NOT invoked!
        __block NSDictionary *addedResult = nil;
        __block MBSSHControlMaster *returnedMaster = nil;
        __block BOOL sheetDone = NO;
        [sheet presentOnWindow:parentWindow completion:^(NSDictionary *row, MBSSHControlMaster *master, NSString *notice) {
            (void)notice;
            addedResult = row;
            returnedMaster = master;
            sheetDone = YES;
        }];
        assert(!sheet.sheetWindow.visible && !parentWindow.visible);
        assert(fakeBrowse.starts == 1);
        // Initial --discover auto-selects first unconfigured item (OfficeAlias at index 1).
        assert(sheet.tableView.selectedRow == 1 && [sheet.items[1].title isEqual:@"OfficeAlias"]);
        assert([sheet selectItemAtIndex:2]); // User explicitly picks Manual -> preserved across Bonjour updates!
        [sheet applyBonjourRows:@[@{@"name": @"Studio Mac", @"host": @"studio.local.", @"port": @22, @"user": NSUserName()}] status:@""];
        assert(sheet.tableView.selectedRow == 3 && sheet.items[3].kind == MBAddMacItemManual);
        sheet.nameField.stringValue = @"Studio";
        sheet.addressField.stringValue = @"ssh alex@192.168.1.20";
        [sheet refreshControls:nil];
        assert(sheet.rememberKeychain.state == NSControlStateValueOff);
        [sheet connectAndAdd:nil];
        pumpUntil(8.0, ^BOOL{ return sheetDone; });
        assert(addedResult && [addedResult[@"selected"] isEqual:@NO]);
        assert(returnedMaster && returnedMaster.active);
        assert([((CtlTestMaster *)returnedMaster).lastSignIn.promptWindows.firstObject isEqual:sheet.sheetWindow]);
        assert(![((CtlTestMaster *)returnedMaster).lastSignIn.promptWindows.firstObject isEqual:parentWindow]);
        assert(fakeBrowse.stops >= 1 && fakeBrowse.fake.stopped);
        assert(![NSFileManager.defaultManager fileExistsAtPath:addLog]); // Keychain OFF -> ssh-add NOT called!
        [returnedMaster closeSynchronously:YES];

        // 8b. Bonjour Connect & Add with edited username + --add failure closes master and keeps sheet open, then Cancel cleans up!
        __block BOOL failNextAdd = YES;
        __block NSArray *lastAddArgs = nil;
        MBAddMacSheet *sheetB = [[MBAddMacSheet alloc] initWithSetupRunner:^(NSArray<NSString *> *args, void (^cb)(NSDictionary *)) {
            if ([args.firstObject isEqual:@"--discover"]) {
                cb(@{@"ok": @YES, @"result": @{@"aliases": @[], @"configured_keys": @[], @"warnings": @[]}});
            } else if ([args.firstObject isEqual:@"--add"]) {
                lastAddArgs = args;
                if (failNextAdd) {
                    cb(@{@"ok": @NO, @"error": @"The receiver needs Apple Silicon."});
                } else {
                    cb(@{@"ok": @YES, @"result": @{@"id": @"r-bonjour00000001", @"name": @"Studio Mac", @"selected": @NO, @"availability": @"available"}});
                }
            }
        }];
        FakeBrowseController *fakeBrowseB = [FakeBrowseController new];
        sheetB.browserFactory = ^MBSSHBrowse *{ return fakeBrowseB; };
        __block CtlTestMaster *masterB = nil;
        sheetB.controlMasterFactory = ^MBSSHControlMaster *{
            masterB = [CtlTestMaster new];
            masterB.fakeSSH = fakeSSH; masterB.fakeSSHAdd = fakeSSHAdd; masterB.helperURL = askpassHelper; masterB.syntheticSecret = secret;
            return masterB;
        };
        __block BOOL sheetBDone = NO;
        __block NSDictionary *sheetBRow = nil;
        [sheetB presentOnWindow:parentWindow completion:^(NSDictionary *row, MBSSHControlMaster *master, NSString *notice) {
            (void)master; (void)notice;
            sheetBRow = row;
            sheetBDone = YES;
        }];
        [sheetB applyBonjourRows:@[@{@"name": @"Studio Mac", @"host": @"studio.local.", @"port": @22, @"user": NSUserName()}] status:@""];
        assert(sheetB.tableView.selectedRow == 0);
        sheetB.usernameField.stringValue = @"alice";
        [sheetB refreshControls:nil];
        [sheetB connectAndAdd:nil];
        pumpUntil(8.0, ^BOOL{ return !masterB.opening && [sheetB.statusLabel.stringValue containsString:@"Apple Silicon"]; });
        assert(!sheetBDone && !masterB.active && [lastAddArgs containsObject:@"alice@studio.local"]);
        [sheetB cancel:nil];
        assert(sheetBDone && sheetBRow == nil && fakeBrowseB.stops >= 1);

        // 9. Connect & Add with Keychain checkbox ON -> ssh-add --apple-use-keychain -- <key> invoked!
        MBAddMacSheet *sheetKC = [[MBAddMacSheet alloc] initWithSetupRunner:^(NSArray<NSString *> *args, void (^cb)(NSDictionary *)) {
            if ([args.firstObject isEqual:@"--discover"]) {
                cb(@{@"ok": @YES, @"result": @{@"aliases": @[], @"configured_keys": @[], @"warnings": @[]}});
            } else if ([args.firstObject isEqual:@"--add"]) {
                cb(@{@"ok": @YES, @"result": @{@"id": @"r-abcdef0123456789", @"name": @"KeyMac", @"selected": @NO, @"availability": @"available"}});
            }
        }];
        FakeBrowseController *fakeBrowse2 = [FakeBrowseController new];
        sheetKC.browserFactory = ^MBSSHBrowse *{ return fakeBrowse2; };
        sheetKC.controlMasterFactory = ^MBSSHControlMaster *{
            CtlTestMaster *cm = [CtlTestMaster new];
            cm.fakeSSH = fakeSSH; cm.fakeSSHAdd = fakeSSHAdd; cm.helperURL = askpassHelper; cm.syntheticSecret = secret;
            return cm;
        };
        sheetDone = NO; addedResult = nil; returnedMaster = nil;
        __block NSString *kcNotice = nil;
        [sheetKC presentOnWindow:parentWindow completion:^(NSDictionary *row, MBSSHControlMaster *master, NSString *notice) {
            addedResult = row; returnedMaster = master; kcNotice = notice; sheetDone = YES;
        }];
        sheetKC.nameField.stringValue = @"KeyMac";
        sheetKC.addressField.stringValue = @"alex@192.168.1.30";
        sheetKC.rememberKeychain.state = NSControlStateValueOn;
        [sheetKC refreshControls:nil];
        [sheetKC connectAndAdd:nil];
        pumpUntil(8.0, ^BOOL{ return sheetDone; });
        assert(addedResult && returnedMaster && [kcNotice containsString:@"Keychain"]);
        NSString *addLogText = [NSString stringWithContentsOfFile:addLog encoding:NSUTF8StringEncoding error:nil];
        assert([addLogText containsString:@"--apple-use-keychain"]);
        assert([addLogText containsString:keyFile]);

        // 10. Pool adopts master and closes all synchronously (Zero lingering processes).
        MBSSHControlMasterPool *pool = [MBSSHControlMasterPool new];
        [pool adoptMaster:returnedMaster forReceiverID:addedResult[@"id"]];
        NSDictionary *paths = [pool controlPathsForReceiverIDs:@[@"local", addedResult[@"id"]]];
        assert(paths.count == 1 && [paths[addedResult[@"id"]] isEqual:returnedMaster.controlPath]);
        [pool closeAllSynchronously:YES];
        assert(!returnedMaster.active);

        // Verify ALL spawned fake-ssh master PIDs exited.
        NSString *allPids = [NSString stringWithContentsOfFile:pidFile encoding:NSUTF8StringEncoding error:nil] ?: @"";
        for (NSString *line in [allPids componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]) {
            pid_t p = (pid_t)line.intValue;
            if (p > 1) assert(kill(p, 0) != 0 && errno == ESRCH);
        }

        // Verify NO secret leaked into any file in scratch directory.
        NSData *needle = [secret dataUsingEncoding:NSUTF8StringEncoding];
        for (NSString *rel in [NSFileManager.defaultManager enumeratorAtPath:scratch]) {
            NSString *full = [scratch stringByAppendingPathComponent:rel];
            BOOL isDir = NO;
            if (![NSFileManager.defaultManager fileExistsAtPath:full isDirectory:&isDir] || isDir) continue;
            NSData *data = [NSData dataWithContentsOfFile:full];
            assert(!data || [data rangeOfData:needle options:0 range:NSMakeRange(0, data.length)].location == NSNotFound);
        }

        puts("passed: A3 ControlMaster lifecycle, A4 AddMacSheet UX/layout, A5 Keychain opt-in, zero secret leaks, zero lingering ssh");
    }
    return 0;
}
