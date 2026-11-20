#import "SSHControlMaster.h"
#import <arpa/inet.h>
#import <dirent.h>
#import <signal.h>
#import <stdlib.h>
#import <string.h>
#import <sys/stat.h>
#import <unistd.h>

static NSError *MBMasterError(NSString *message) {
    return [NSError errorWithDomain:@"Macbee.SSH.ControlMaster"
                               code:1
                           userInfo:@{NSLocalizedDescriptionKey: message ?: @"SSH connection failed."}];
}

static BOOL MBMatchesPattern(NSString *value, NSString *pattern) {
    if (!value.length) return NO;
    NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
    return [re numberOfMatchesInString:value options:0 range:NSMakeRange(0, value.length)] == 1;
}

static BOOL MBIsASCIIOnly(NSString *value) {
    for (NSUInteger i = 0; i < value.length; i++) {
        unichar c = [value characterAtIndex:i];
        if (c < 32 || c >= 127) return NO;
    }
    return YES;
}

static void MBCleanupDirectoryAtPath(NSString *dirPath) {
    if (!dirPath.length) return;
    const char *dirC = dirPath.fileSystemRepresentation;
    DIR *dir = opendir(dirC);
    if (dir) {
        struct dirent *entry = NULL;
        while ((entry = readdir(dir)) != NULL) {
            if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
            NSString *child = [dirPath stringByAppendingPathComponent:[NSString stringWithUTF8String:entry->d_name]];
            unlink(child.fileSystemRepresentation);
        }
        closedir(dir);
    }
    rmdir(dirC);
}

static void MBWaitAndForceStopTask(NSTask *task, NSString *sshExec, NSString *controlPath,
                                   NSArray<NSString *> *sshArgs, NSString *controlDir) {
    if (controlPath.length && sshArgs.count && sshExec.length) {
        NSTask *exitTask = [NSTask new];
        exitTask.executableURL = [NSURL fileURLWithPath:sshExec];
        exitTask.arguments = [@[@"-o", @"BatchMode=yes", @"-o", @"ConnectTimeout=2",
                                @"-O", @"exit", @"-S", controlPath]
                              arrayByAddingObjectsFromArray:sshArgs];
        exitTask.standardInput = [NSFileHandle fileHandleWithNullDevice];
        exitTask.standardOutput = [NSFileHandle fileHandleWithNullDevice];
        exitTask.standardError = [NSFileHandle fileHandleWithNullDevice];
        if ([exitTask launchAndReturnError:nil]) {
            NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:1.5];
            while (exitTask.isRunning && deadline.timeIntervalSinceNow > 0) {
                [NSThread sleepForTimeInterval:0.02];
            }
            if (exitTask.isRunning) [exitTask terminate];
        }
    }
    if (task && task.isRunning) {
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:1.0];
        while (task.isRunning && deadline.timeIntervalSinceNow > 0) {
            [NSThread sleepForTimeInterval:0.02];
        }
        if (task.isRunning) {
            [task terminate];
            NSDate *termDeadline = [NSDate dateWithTimeIntervalSinceNow:0.8];
            while (task.isRunning && termDeadline.timeIntervalSinceNow > 0) {
                [NSThread sleepForTimeInterval:0.02];
            }
            if (task.isRunning && task.processIdentifier > 1) {
                kill(task.processIdentifier, SIGKILL);
            }
        }
    }
    MBCleanupDirectoryAtPath(controlDir);
}

@interface MBSSHControlMaster ()
@property(nonatomic, readwrite, copy, nullable) NSString *controlDirectory;
@property(nonatomic, readwrite, copy, nullable) NSString *controlPath;
@property(nonatomic, readwrite, copy, nullable) NSString *destination;
@property(nonatomic, readwrite, copy, nullable) NSArray<NSString *> *sshArguments;
@property(nonatomic, readwrite, copy, nullable) NSString *promptedIdentityPath;
@property(nonatomic, readwrite) BOOL active;
@property(nonatomic, readwrite) BOOL opening;
@property(nonatomic, strong, nullable) MBSSHSignIn *signIn;
@property(nonatomic, strong, nullable) NSTask *adoptedTask;
@property(nonatomic, strong, nullable) NSPipe *stderrPipe;
@property(nonatomic, strong, nullable) NSMutableData *stderrData;
@property(nonatomic, strong, nullable) NSTimer *pollTimer;
@property(nonatomic, copy, nullable) void (^openCompletion)(BOOL ok, BOOL cancelled, NSError *_Nullable error);
@property(nonatomic) uint64_t generation;
@property(nonatomic) BOOL checkingControl, taskEnded, stderrEOF, cancelled;
@property(nonatomic) int exitStatus;
@property(nonatomic, strong, nullable) MBSSHSignIn *keychainSignIn;
@property(nonatomic, copy, nullable) void (^keychainCompletion)(BOOL saved, BOOL cancelled, NSError *_Nullable error);
@property(nonatomic, strong) dispatch_group_t closeGroup;
@property(nonatomic, strong) dispatch_queue_t closeQueue;
@end

@implementation MBSSHControlMaster

- (instancetype)init {
    if ((self = [super init])) {
        _closeGroup = dispatch_group_create();
        _closeQueue = dispatch_queue_create("macbee.ssh.controlmaster.close", DISPATCH_QUEUE_SERIAL);
    }
    return self;
}

- (NSString *)sshExecutablePath { return @"/usr/bin/ssh"; }
- (NSString *)sshAddExecutablePath { return @"/usr/bin/ssh-add"; }
- (MBSSHSignIn *)newSignIn { return [MBSSHSignIn new]; }

+ (NSString *)normalizeManualAddress:(NSString *)raw strippedSSHPrefix:(BOOL *)strippedOut {
    if (strippedOut) *strippedOut = NO;
    if (![raw isKindOfClass:NSString.class]) return @"";
    NSString *trimmed = [raw stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (trimmed.length >= 4) {
        NSString *head = [trimmed substringToIndex:3];
        unichar sep = [trimmed characterAtIndex:3];
        if ([head caseInsensitiveCompare:@"ssh"] == NSOrderedSame &&
            (sep == ' ' || sep == '\t')) {
            NSString *rest = [[trimmed substringFromIndex:3]
                stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
            if (rest.length > 0 &&
                [rest rangeOfCharacterFromSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].location == NSNotFound &&
                ![rest hasPrefix:@"-"]) {
                if (strippedOut) *strippedOut = YES;
                return rest;
            }
        }
    }
    return trimmed;
}

+ (nullable NSArray<NSString *> *)sshArgumentsForEndpoint:(NSString *)value
                                                canonical:(NSString *_Nullable *_Nullable)canonicalOut
                                                    error:(NSError *_Nullable *_Nullable)error {
    static NSString *const kInvalid = @"Enter an SSH alias or [user@]host[:port]; bracket IPv6 addresses.";
    if (![value isKindOfClass:NSString.class] || value.length < 1 || value.length > 336 || !MBIsASCIIOnly(value)) {
        if (error) *error = MBMasterError(kInvalid);
        return nil;
    }
    NSString *user = @"";
    NSString *rest = value;
    NSRange at = [value rangeOfString:@"@" options:NSBackwardsSearch];
    if (at.location != NSNotFound) {
        user = [value substringToIndex:at.location];
        rest = [value substringFromIndex:at.location + 1];
        if (!MBMatchesPattern(user, @"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")) {
            if (error) *error = MBMasterError(kInvalid);
            return nil;
        }
    }
    NSString *host = @"";
    NSString *rawPort = nil;
    BOOL isIPv6 = NO;
    if ([rest hasPrefix:@"["]) {
        NSRegularExpression *v6 = [NSRegularExpression regularExpressionWithPattern:
            @"^\\[([A-Fa-f0-9:.]+)\\](?::([0-9]{1,5}))?$" options:0 error:nil];
        NSTextCheckingResult *m = [v6 firstMatchInString:rest options:0 range:NSMakeRange(0, rest.length)];
        if (!m) {
            if (error) *error = MBMasterError(kInvalid);
            return nil;
        }
        NSString *inner = [rest substringWithRange:[m rangeAtIndex:1]];
        struct in6_addr addr;
        char normalized[INET6_ADDRSTRLEN] = {0};
        if (inet_pton(AF_INET6, inner.UTF8String, &addr) != 1 ||
            !inet_ntop(AF_INET6, &addr, normalized, sizeof(normalized))) {
            if (error) *error = MBMasterError(kInvalid);
            return nil;
        }
        host = [NSString stringWithUTF8String:normalized];
        isIPv6 = YES;
        if ([m rangeAtIndex:2].location != NSNotFound) {
            rawPort = [rest substringWithRange:[m rangeAtIndex:2]];
        }
    } else {
        NSRange colon = [rest rangeOfString:@":"];
        if (colon.location != NSNotFound) {
            host = [rest substringToIndex:colon.location];
            rawPort = [rest substringFromIndex:colon.location + 1];
            if (!rawPort.length) {
                if (error) *error = MBMasterError(kInvalid);
                return nil;
            }
        } else {
            host = rest;
        }
        if (!MBMatchesPattern(host, @"^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$") ||
            [host containsString:@".."]) {
            if (error) *error = MBMasterError(kInvalid);
            return nil;
        }
    }
    NSInteger port = 0;
    if (rawPort != nil) {
        if (!MBMatchesPattern(rawPort, @"^[0-9]{1,5}$")) {
            if (error) *error = MBMasterError(kInvalid);
            return nil;
        }
        port = rawPort.integerValue;
        if (port < 1 || port > 65535) {
            if (error) *error = MBMasterError(kInvalid);
            return nil;
        }
    }
    if (canonicalOut) {
        NSString *formattedHost = isIPv6 ? [NSString stringWithFormat:@"[%@]", host] : host;
        NSMutableString *canon = [NSMutableString string];
        if (user.length) [canon appendFormat:@"%@@", user];
        [canon appendString:formattedHost];
        if (port > 0) [canon appendFormat:@":%ld", (long)port];
        *canonicalOut = canon;
    }
    NSString *target = user.length ? [NSString stringWithFormat:@"%@@%@", user, host] : host;
    if (port > 0) {
        return @[@"-p", [NSString stringWithFormat:@"%ld", (long)port], @"--", target];
    }
    return @[@"--", target];
}

+ (nullable NSDictionary *)destinationKeyForEndpoint:(NSString *)value
                                             aliases:(nullable NSArray<NSDictionary *> *)aliases {
    NSString *canonical = nil;
    NSArray<NSString *> *argv = [self sshArgumentsForEndpoint:value canonical:&canonical error:nil];
    if (!argv || !canonical) return nil;
    NSString *target = argv.lastObject;
    NSRange at = [target rangeOfString:@"@" options:NSBackwardsSearch];
    NSString *epHost = (at.location != NSNotFound) ? [target substringFromIndex:at.location + 1] : target;
    NSInteger epPort = ([argv.firstObject isEqual:@"-p"] && argv.count >= 2) ? argv[1].integerValue : 0;

    NSDictionary *matchedRow = nil;
    for (NSDictionary *row in aliases) {
        if ([row[@"alias"] isEqual:epHost]) {
            matchedRow = row;
            break;
        }
    }
    if (matchedRow && ![matchedRow[@"preview"] isEqual:@"static"]) {
        return @{@"kind": @"unresolved", @"canonical": canonical};
    }
    NSString *name = matchedRow ? (matchedRow[@"host"] ?: epHost) : epHost;
    NSInteger port = epPort ?: (matchedRow && [matchedRow[@"port"] isKindOfClass:NSNumber.class]
                                ? [matchedRow[@"port"] integerValue] : 22);
    while ([name hasSuffix:@"."]) {
        name = [name substringToIndex:name.length - 1];
    }
    return @{@"kind": @"address", @"host": name.lowercaseString, @"port": @(port)};
}

+ (BOOL)isDestination:(NSString *)value
     configuredInKeys:(NSArray<NSDictionary *> *)configuredKeys
              aliases:(nullable NSArray<NSDictionary *> *)aliases {
    NSDictionary *key = [self destinationKeyForEndpoint:value aliases:aliases];
    if (!key) return NO;
    return [configuredKeys containsObject:key];
}

+ (NSString *)friendlyErrorFromSSHStderr:(NSString *)raw {
    NSString *lower = raw.lowercaseString ?: @"";
    if ([lower containsString:@"remote host identification has changed"] ||
        [lower containsString:@"revoked host key"] ||
        [lower containsString:@"offending "] ||
        [lower containsString:@"host key verification failed"]) {
        return @"SSH host identity changed or could not be verified. Macbee will not modify known_hosts.";
    }
    if ([lower containsString:@"permission denied"] ||
        [lower containsString:@"authentication failed"] ||
        [lower containsString:@"too many authentication failures"]) {
        return @"SSH authentication was refused. Check your username, passphrase, or password.";
    }
    if ([lower containsString:@"could not resolve hostname"] ||
        [lower containsString:@"nodename nor servname"] ||
        [lower containsString:@"name or service not known"]) {
        return @"Could not find that Mac on the network. Check the host name or IP address.";
    }
    if ([lower containsString:@"connection refused"]) {
        return @"SSH connection was refused. Make sure Remote Login is turned on on that Mac.";
    }
    if ([lower containsString:@"operation timed out"] ||
        [lower containsString:@"connection timed out"] ||
        [lower containsString:@"no route to host"] ||
        [lower containsString:@"network is unreachable"]) {
        return @"Could not reach that Mac over SSH. Check the network connection and address.";
    }
    for (NSString *line in [raw componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]) {
        NSString *trimmed = [line stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (!trimmed.length) continue;
        NSMutableString *clean = [NSMutableString stringWithCapacity:MIN(trimmed.length, (NSUInteger)200)];
        for (NSUInteger i = 0; i < trimmed.length && clean.length < 200; i++) {
            unichar c = [trimmed characterAtIndex:i];
            if (c >= 32 && c != 127) [clean appendFormat:@"%C", c];
        }
        if (clean.length) return clean;
    }
    return @"SSH sign-in could not connect.";
}

- (BOOL)createControlDirectoryError:(NSError **)error {
    [self cleanupDirectory];
    char tmpl[] = "/tmp/macbee-ctl.XXXXXXXX";
    char *created = mkdtemp(tmpl);
    if (!created) {
        if (error) *error = MBMasterError(@"Cannot create private SSH control directory.");
        return NO;
    }
    struct stat st;
    if (lstat(created, &st) != 0 || !S_ISDIR(st.st_mode) || st.st_uid != getuid() || (st.st_mode & 0777) != 0700) {
        rmdir(created);
        if (error) *error = MBMasterError(@"Private SSH control directory failed ownership check.");
        return NO;
    }
    // Enforce sun_path < 104 even with 40-char %C and OpenSSH's 17-byte bind temp suffix.
    if (strlen(created) + 1 + 40 > 86) {
        rmdir(created);
        if (error) *error = MBMasterError(@"SSH control socket path exceeds macOS limit.");
        return NO;
    }
    self.controlDirectory = [NSString stringWithUTF8String:created];
    self.controlPath = [self.controlDirectory stringByAppendingPathComponent:@"%C"];
    return YES;
}

- (void)cleanupDirectory {
    NSString *dir = self.controlDirectory;
    self.controlDirectory = nil;
    self.controlPath = nil;
    MBCleanupDirectoryAtPath(dir);
}

- (BOOL)hasSocketInControlDirectory {
    if (!self.controlDirectory.length) return NO;
    const char *dirC = self.controlDirectory.fileSystemRepresentation;
    DIR *dir = opendir(dirC);
    if (!dir) return NO;
    BOOL found = NO;
    struct dirent *entry = NULL;
    while ((entry = readdir(dir)) != NULL) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        NSString *candidate = [self.controlDirectory stringByAppendingPathComponent:
            [NSString stringWithUTF8String:entry->d_name]];
        struct stat st;
        if (lstat(candidate.fileSystemRepresentation, &st) == 0 &&
            S_ISSOCK(st.st_mode) && st.st_uid == getuid()) {
            found = YES;
            break;
        }
    }
    closedir(dir);
    return found;
}

- (BOOL)runControlCommand:(NSString *)subcommand timeout:(NSTimeInterval)timeout {
    if (!self.controlPath.length || !self.sshArguments.count) return NO;
    NSTask *cmd = [NSTask new];
    cmd.executableURL = [NSURL fileURLWithPath:[self sshExecutablePath]];
    cmd.arguments = [@[@"-o", @"BatchMode=yes", @"-o", @"ConnectTimeout=2",
                       @"-O", subcommand, @"-S", self.controlPath]
                     arrayByAddingObjectsFromArray:self.sshArguments];
    cmd.standardInput = [NSFileHandle fileHandleWithNullDevice];
    cmd.standardOutput = [NSFileHandle fileHandleWithNullDevice];
    cmd.standardError = [NSFileHandle fileHandleWithNullDevice];
    if (![cmd launchAndReturnError:nil]) return NO;
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:timeout];
    while (cmd.isRunning && deadline.timeIntervalSinceNow > 0) {
        [NSThread sleepForTimeInterval:0.01];
    }
    if (cmd.isRunning) {
        [cmd terminate];
        return NO;
    }
    return cmd.terminationStatus == 0;
}

- (BOOL)isAliveSync {
    if (!self.active || !self.controlPath.length) return NO;
    if (self.adoptedTask && !self.adoptedTask.isRunning) {
        self.active = NO;
        return NO;
    }
    BOOL alive = [self runControlCommand:@"check" timeout:2.0];
    if (!alive) self.active = NO;
    return alive;
}

- (void)openForDestination:(NSString *)destination
                    window:(NSWindow *)window
                completion:(void (^)(BOOL ok, BOOL cancelled, NSError *_Nullable error))completion {
    NSAssert(NSThread.isMainThread, @"MBSSHControlMaster must be driven on the main thread");
    [self closeSynchronously:YES];

    NSError *parseError = nil;
    NSString *canonical = nil;
    NSArray<NSString *> *sshArgs = [MBSSHControlMaster sshArgumentsForEndpoint:destination
                                                                     canonical:&canonical
                                                                         error:&parseError];
    if (!sshArgs || !canonical) {
        completion(NO, NO, parseError ?: MBMasterError(@"Invalid SSH destination."));
        return;
    }
    NSError *dirError = nil;
    if (![self createControlDirectoryError:&dirError]) {
        completion(NO, NO, dirError);
        return;
    }

    self.destination = canonical;
    self.sshArguments = sshArgs;
    self.promptedIdentityPath = nil;
    self.active = NO;
    self.opening = YES;
    self.cancelled = NO;
    self.checkingControl = NO;
    self.taskEnded = NO;
    self.stderrEOF = NO;
    self.exitStatus = 0;
    self.stderrData = [NSMutableData data];
    self.openCompletion = completion;

    uint64_t gen = ++self.generation;
    MBSSHSignIn *signIn = [self newSignIn];
    self.signIn = signIn;

    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:[self sshExecutablePath]];
    NSArray<NSString *> *masterArgs = @[
        @"-o", @"ControlMaster=yes",
        @"-o", @"ControlPersist=no",
        @"-o", [NSString stringWithFormat:@"ControlPath=%@", self.controlPath],
        @"-o", @"ConnectTimeout=10",
        @"-o", @"ServerAliveInterval=15",
        @"-o", @"ServerAliveCountMax=3",
        @"-o", @"RemoteCommand=none",
        @"-o", @"RequestTTY=no",
        @"-T",
        @"-N"
    ];
    task.arguments = [masterArgs arrayByAddingObjectsFromArray:sshArgs];
    task.standardOutput = [NSFileHandle fileHandleWithNullDevice];
    NSPipe *stderrPipe = [NSPipe pipe];
    task.standardError = stderrPipe;
    self.stderrPipe = stderrPipe;

    __weak typeof(self) weak = self;
    stderrPipe.fileHandleForReading.readabilityHandler = ^(NSFileHandle *handle) {
        NSData *chunk = handle.availableData;
        if (!chunk.length) {
            handle.readabilityHandler = nil;
            dispatch_async(dispatch_get_main_queue(), ^{
                MBSSHControlMaster *strong = weak;
                if (!strong || strong.generation != gen) return;
                strong.stderrEOF = YES;
                [strong finalizeFailedOpenIfReadyForGeneration:gen];
            });
            return;
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            MBSSHControlMaster *strong = weak;
            if (!strong || strong.generation != gen || !strong.stderrData) return;
            NSUInteger room = 16384 - MIN(strong.stderrData.length, (NSUInteger)16384);
            if (room > 0) {
                [strong.stderrData appendBytes:chunk.bytes length:MIN(chunk.length, room)];
            }
        });
    };

    task.terminationHandler = ^(NSTask *ended) {
        int code = ended.terminationStatus;
        dispatch_async(dispatch_get_main_queue(), ^{
            MBSSHControlMaster *strong = weak;
            if (!strong || strong.generation != gen) return;
            if (strong.active && strong.adoptedTask == ended) {
                strong.active = NO;
                strong.adoptedTask = nil;
                [strong cleanupDirectory];
                return;
            }
            if (!strong.opening) return;
            strong.taskEnded = YES;
            strong.exitStatus = code;
            [strong finalizeFailedOpenIfReadyForGeneration:gen];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 150 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
                MBSSHControlMaster *fallback = weak;
                if (!fallback || fallback.generation != gen || !fallback.opening) return;
                fallback.stderrEOF = YES;
                fallback.stderrPipe.fileHandleForReading.readabilityHandler = nil;
                [fallback finalizeFailedOpenIfReadyForGeneration:gen];
            });
        });
    };

    NSError *launchError = nil;
    if (![signIn launchTask:task forDestination:canonical window:window error:&launchError]) {
        stderrPipe.fileHandleForReading.readabilityHandler = nil;
        self.stderrPipe = nil;
        self.signIn = nil;
        self.opening = NO;
        self.openCompletion = nil;
        [self cleanupDirectory];
        completion(NO, NO, launchError ?: MBMasterError(@"Could not launch SSH sign-in."));
        return;
    }
    self.adoptedTask = task;

    self.pollTimer = [NSTimer scheduledTimerWithTimeInterval:0.08 repeats:YES block:^(NSTimer *timer) {
        MBSSHControlMaster *strong = weak;
        if (!strong || strong.generation != gen || !strong.opening) {
            [timer invalidate];
            return;
        }
        [strong pollControlSocketForGeneration:gen];
    }];
}

- (void)pollControlSocketForGeneration:(uint64_t)gen {
    if (gen != self.generation || !self.opening || self.checkingControl || self.cancelled) return;
    if (![self hasSocketInControlDirectory]) return;

    self.checkingControl = YES;
    NSString *execPath = [self sshExecutablePath];
    NSString *ctlPath = self.controlPath;
    NSArray<NSString *> *sshArgs = self.sshArguments;
    __weak typeof(self) weak = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSTask *check = [NSTask new];
        check.executableURL = [NSURL fileURLWithPath:execPath];
        check.arguments = [@[@"-o", @"BatchMode=yes", @"-o", @"ConnectTimeout=2",
                             @"-O", @"check", @"-S", ctlPath]
                           arrayByAddingObjectsFromArray:sshArgs];
        check.standardInput = [NSFileHandle fileHandleWithNullDevice];
        check.standardOutput = [NSFileHandle fileHandleWithNullDevice];
        check.standardError = [NSFileHandle fileHandleWithNullDevice];
        BOOL ok = NO;
        if ([check launchAndReturnError:nil]) {
            NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:3.0];
            while (check.isRunning && deadline.timeIntervalSinceNow > 0) {
                [NSThread sleepForTimeInterval:0.01];
            }
            if (check.isRunning) [check terminate];
            else ok = (check.terminationStatus == 0);
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            MBSSHControlMaster *strong = weak;
            if (!strong || strong.generation != gen) return;
            strong.checkingControl = NO;
            if (!strong.opening || strong.cancelled) return;
            if (ok && strong.adoptedTask.isRunning) {
                [strong.pollTimer invalidate];
                strong.pollTimer = nil;
                strong.stderrPipe.fileHandleForReading.readabilityHandler = nil;
                strong.stderrPipe = nil;
                strong.stderrData = nil;
                strong.promptedIdentityPath = strong.signIn.promptedIdentityPath;
                [strong.signIn finishTerminatingTask:NO];
                strong.signIn = nil;
                strong.opening = NO;
                strong.active = YES;
                void (^done)(BOOL, BOOL, NSError *) = strong.openCompletion;
                strong.openCompletion = nil;
                if (done) done(YES, NO, nil);
            }
        });
    });
}

- (void)finalizeFailedOpenIfReadyForGeneration:(uint64_t)gen {
    if (gen != self.generation || !self.opening || !self.taskEnded || !self.stderrEOF) return;
    [self.pollTimer invalidate];
    self.pollTimer = nil;
    self.stderrPipe.fileHandleForReading.readabilityHandler = nil;
    self.stderrPipe = nil;

    BOOL cancelled = self.cancelled || self.signIn.userCancelled;
    BOOL refused = self.signIn.refusedPrompt;
    NSString *rawStderr = [[NSString alloc] initWithData:self.stderrData ?: [NSData data]
                                                encoding:NSUTF8StringEncoding] ?: @"";
    self.stderrData = nil;
    [self.signIn finishTerminatingTask:YES];
    self.signIn = nil;
    self.adoptedTask = nil;
    self.opening = NO;
    [self cleanupDirectory];

    void (^done)(BOOL, BOOL, NSError *) = self.openCompletion;
    self.openCompletion = nil;
    if (!done) return;
    if (cancelled) {
        done(NO, YES, nil);
    } else if (refused) {
        done(NO, NO, MBMasterError(@"SSH trust confirmation was refused."));
    } else {
        NSString *msg = [MBSSHControlMaster friendlyErrorFromSSHStderr:rawStderr];
        done(NO, NO, MBMasterError(msg));
    }
}

- (void)closeSynchronously:(BOOL)sync {
    self.generation++;
    [self.pollTimer invalidate];
    self.pollTimer = nil;
    self.stderrPipe.fileHandleForReading.readabilityHandler = nil;
    self.stderrPipe = nil;
    self.stderrData = nil;

    if (self.keychainSignIn) {
        [self.keychainSignIn cancel];
        self.keychainSignIn = nil;
        void (^kcDone)(BOOL, BOOL, NSError *) = self.keychainCompletion;
        self.keychainCompletion = nil;
        if (kcDone) kcDone(NO, YES, nil);
    }

    if (self.opening) {
        self.cancelled = YES;
        self.opening = NO;
        [self.signIn cancel];
        self.signIn = nil;
        void (^done)(BOOL, BOOL, NSError *) = self.openCompletion;
        self.openCompletion = nil;
        if (done) done(NO, YES, nil);
    } else if (self.signIn) {
        [self.signIn finishTerminatingTask:YES];
        self.signIn = nil;
    }

    NSTask *task = self.adoptedTask;
    NSString *ctlPath = self.controlPath;
    NSString *ctlDir = self.controlDirectory;
    NSArray<NSString *> *sshArgs = self.sshArguments;
    NSString *sshExec = [self sshExecutablePath];
    self.adoptedTask = nil;
    self.active = NO;
    self.controlPath = nil;
    self.controlDirectory = nil;
    self.sshArguments = nil;

    if (task || ctlPath.length || ctlDir.length) {
        if (sync) {
            dispatch_group_wait(self.closeGroup, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
            MBWaitAndForceStopTask(task, sshExec, ctlPath, sshArgs, ctlDir);
        } else {
            dispatch_group_async(self.closeGroup, self.closeQueue, ^{
                MBWaitAndForceStopTask(task, sshExec, ctlPath, sshArgs, ctlDir);
            });
        }
    } else if (sync) {
        dispatch_group_wait(self.closeGroup, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
    }
}

- (void)waitForPendingClose {
    dispatch_group_wait(self.closeGroup, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
}

- (void)saveIdentityToKeychain:(NSString *)identityPath
                        window:(NSWindow *)window
                    completion:(void (^)(BOOL saved, BOOL cancelled, NSError *_Nullable error))completion {
    NSAssert(NSThread.isMainThread, @"Keychain sign-in must run on the main thread");
    BOOL isDir = NO;
    if (!identityPath.length || [identityPath hasPrefix:@"-"] ||
        ![NSFileManager.defaultManager fileExistsAtPath:identityPath isDirectory:&isDir] || isDir) {
        completion(NO, NO, MBMasterError(@"SSH identity file is unavailable for Keychain."));
        return;
    }
    MBSSHSignIn *signIn = [self newSignIn];
    self.keychainSignIn = signIn;
    self.keychainCompletion = completion;

    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:[self sshAddExecutablePath]];
    task.arguments = @[@"--apple-use-keychain", @"--", identityPath];
    task.standardOutput = [NSFileHandle fileHandleWithNullDevice];
    task.standardError = [NSFileHandle fileHandleWithNullDevice];

    __weak typeof(self) weak = self;
    task.terminationHandler = ^(NSTask *ended) {
        int code = ended.terminationStatus;
        dispatch_async(dispatch_get_main_queue(), ^{
            MBSSHControlMaster *strong = weak;
            if (!strong) return;
            BOOL cancelled = signIn.userCancelled;
            [signIn finishTerminatingTask:NO];
            if (strong.keychainSignIn == signIn) strong.keychainSignIn = nil;
            void (^done)(BOOL, BOOL, NSError *) = strong.keychainCompletion;
            strong.keychainCompletion = nil;
            if (!done) return;
            if (cancelled) {
                done(NO, YES, nil);
            } else if (code == 0) {
                done(YES, NO, nil);
            } else {
                done(NO, NO, MBMasterError(@"Could not save the SSH passphrase in Keychain."));
            }
        });
    };

    NSError *err = nil;
    if (![signIn launchTask:task forDestination:identityPath.lastPathComponent window:window error:&err]) {
        self.keychainSignIn = nil;
        void (^done)(BOOL, BOOL, NSError *) = self.keychainCompletion;
        self.keychainCompletion = nil;
        if (done) done(NO, NO, err ?: MBMasterError(@"Could not start ssh-add for Keychain."));
    }
}

- (void)dealloc {
    [self closeSynchronously:YES];
}

@end

@interface MBSSHControlMasterPool ()
@property(nonatomic, strong) NSMutableDictionary<NSString *, MBSSHControlMaster *> *masters;
@property(nonatomic, strong) NSMutableSet<MBSSHControlMaster *> *closing;
@end

@implementation MBSSHControlMasterPool

- (instancetype)init {
    if ((self = [super init])) {
        _masters = [NSMutableDictionary dictionary];
        _closing = [NSMutableSet set];
    }
    return self;
}

- (void)retireMaster:(MBSSHControlMaster *)master synchronously:(BOOL)sync {
    if (!master) return;
    if (sync) {
        [master closeSynchronously:YES];
        [self.closing removeObject:master];
        return;
    }
    [self.closing addObject:master];
    [master closeSynchronously:NO];
    __weak typeof(self) weak = self;
    dispatch_group_notify(master.closeGroup, dispatch_get_main_queue(), ^{
        [weak.closing removeObject:master];
    });
}

- (nullable MBSSHControlMaster *)liveMasterForReceiverID:(NSString *)receiverID {
    if (!receiverID.length) return nil;
    MBSSHControlMaster *master = self.masters[receiverID];
    if (!master) return nil;
    if (![master isAliveSync]) {
        [self.masters removeObjectForKey:receiverID];
        [self retireMaster:master synchronously:NO];
        return nil;
    }
    return master;
}

- (void)adoptMaster:(MBSSHControlMaster *)master forReceiverID:(NSString *)receiverID {
    if (!master || !receiverID.length) return;
    MBSSHControlMaster *prior = self.masters[receiverID];
    if (prior && prior != master) {
        [self retireMaster:prior synchronously:NO];
    }
    self.masters[receiverID] = master;
}

- (NSDictionary<NSString *, NSString *> *)controlPathsForReceiverIDs:(NSArray<NSString *> *)receiverIDs {
    NSMutableDictionary<NSString *, NSString *> *paths = [NSMutableDictionary dictionary];
    for (NSString *receiverID in receiverIDs) {
        if ([receiverID isEqual:@"local"]) continue;
        MBSSHControlMaster *master = [self liveMasterForReceiverID:receiverID];
        if (master.controlPath.length) {
            paths[receiverID] = master.controlPath;
        }
    }
    return paths;
}

- (void)closeMasterForReceiverID:(NSString *)receiverID synchronously:(BOOL)sync {
    if (!receiverID.length) return;
    MBSSHControlMaster *master = self.masters[receiverID];
    if (!master) return;
    [self.masters removeObjectForKey:receiverID];
    [self retireMaster:master synchronously:sync];
}

- (void)closeAllSynchronously:(BOOL)sync {
    NSArray<MBSSHControlMaster *> *all = [self.masters.allValues copy];
    [self.masters removeAllObjects];
    for (MBSSHControlMaster *master in all) {
        [self retireMaster:master synchronously:sync];
    }
    if (sync && self.closing.count) {
        NSArray<MBSSHControlMaster *> *pending = [self.closing.allObjects copy];
        for (MBSSHControlMaster *master in pending) {
            [master waitForPendingClose];
        }
        [self.closing removeAllObjects];
    }
}

- (void)dealloc {
    [self closeAllSynchronously:YES];
}

@end
