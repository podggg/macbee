// In-process synthetic UI answers; no real credentials, dialogs, ssh agent,
// Keychain, known_hosts, network connection, audio device, or paired receiver.
#import "../Sources/SSHLogin/SSHSignIn.h"
#import <CommonCrypto/CommonDigest.h>
#import <sys/stat.h>
@interface MBSSHSignIn (FixtureInterface)
- (BOOL)permitsExecutable:(NSString *)path;
+ (NSArray<NSString *> *)argumentsForSystemSSH:(NSArray<NSString *> *)arguments;
+ (NSDictionary *)classifyPrompt:(NSString *)prompt hint:(unsigned)hint;
@end
// Probe the actual launch boundary, not just the option builder. No process
// is executed: the real NSTask inputs are assembled before this override.
@interface LaunchBoundaryProbe : NSObject
@property(nonatomic,copy) NSURL *executableURL;
@property(nonatomic,copy) NSArray *arguments;
@property(nonatomic,copy) NSDictionary *environment;
@property(nonatomic,strong) NSFileHandle *standardInput;
@property(nonatomic) BOOL reached;
@end
@implementation LaunchBoundaryProbe
- (BOOL)launchAndReturnError:(NSError **)error {(void)error;self.reached=YES;return NO;}
@end
@interface ProductionPathFixture : MBSSHSignIn
@property(nonatomic,copy) NSString *helperURL;
@end
@implementation ProductionPathFixture
- (NSString *)bundledHelper {return self.helperURL;}
@end
@interface LoginFixture : MBSSHSignIn
@property(nonatomic, copy) NSString *fakeSSH, *helperURL, *syntheticSecret;
@property(nonatomic) BOOL cancel; @property(nonatomic) NSUInteger prompts;
@end
@implementation LoginFixture
- (BOOL)permitsExecutable:(NSString *)path { return [path isEqual:self.fakeSSH]; }
- (NSString *)bundledHelper { return self.helperURL; }
- (void)presentPrompt:(NSString *)prompt hint:(unsigned)hint completion:(void (^)(NSData *))complete {
    self.prompts++;
    NSDictionary *k=[MBSSHSignIn classifyPrompt:prompt hint:hint];
    NSString *answer=!k||self.cancel?nil:([k[@"kind"] isEqual:@"trust"]?k[@"fingerprint"]:self.syntheticSecret);
    complete([[answer dataUsingEncoding:NSUTF8StringEncoding] mutableCopy]);
}
@end
static NSString *digest(NSString *s) {
    NSData *data=[s dataUsingEncoding:NSUTF8StringEncoding];unsigned char h[32];CC_SHA256(data.bytes,(CC_LONG)data.length,h);
    NSMutableString *v=[NSMutableString string];for(unsigned i=0;i<sizeof(h);i++)[v appendFormat:@"%02x",h[i]];return v;
}
static NSTask *task(NSString *binary, NSString *prompt, NSString *expected, NSString *home) {
    NSTask *t=[NSTask new];t.executableURL=[NSURL fileURLWithPath:binary];t.arguments=@[prompt];
    t.environment=@{@"HOME":home,@"TMPDIR":home,@"PATH":@"/usr/bin:/bin",@"FIXTURE_EXPECTED_SHA256":expected};
    t.standardOutput=NSFileHandle.fileHandleWithNullDevice;t.standardError=NSFileHandle.fileHandleWithNullDevice;return t;
}
static void go(LoginFixture *f, NSString *p, NSString *expected, NSString *home) {
    NSError *e=nil;NSTask *t=task(f.fakeSSH,p,expected,home);
    NSCAssert([f launchTask:t forDestination:@"fixture.invalid" window:(id)[NSObject new] error:&e],@"Cannot start approved fixture task");
    NSString *sock=f.socketPath;NSString *dir=sock.stringByDeletingLastPathComponent;struct stat st;
    NSCAssert(!stat(dir.fileSystemRepresentation,&st) && (st.st_mode&0777)==0700,@"Directory mode");
    NSCAssert(!stat(sock.fileSystemRepresentation,&st) && (st.st_mode&0777)==0600 && S_ISSOCK(st.st_mode),@"Socket mode");
    NSDate *until=[NSDate dateWithTimeIntervalSinceNow:6];
    while(t.isRunning && until.timeIntervalSinceNow>0)[NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.01]];
    if([expected isEqual:@"CANCEL"]){
        NSCAssert(!t.isRunning && t.terminationReason==NSTaskTerminationReasonUncaughtSignal && t.terminationStatus==SIGTERM,@"Cancelled login was not terminated");
        NSCAssert(f.task==nil && f.socketPath==nil,@"Cancelled login retains authority");
    } else NSCAssert(!t.isRunning && t.terminationStatus==0,@"Synthetic reply digest mismatch");
    [f finishTerminatingTask:NO];NSCAssert(![NSFileManager.defaultManager fileExistsAtPath:dir],@"Private channel left over");
}
static void rejectUnownedHelper(LoginFixture *f,NSString *home) {
    NSTask *owner=task(f.fakeSSH,@"Fixture password: ",digest(f.syntheticSecret),home);
    NSMutableDictionary *env=owner.environment.mutableCopy;env[@"FIXTURE_DELAY"]=@"yes";owner.environment=env;
    NSCAssert([f launchTask:owner forDestination:@"fixture.invalid" window:(id)[NSObject new] error:nil],@"Owner");
    NSTask *rogue=task(f.fakeSSH,@"Fixture password: ",@"CANCEL",home);
    env=owner.environment.mutableCopy;[env removeObjectForKey:@"FIXTURE_DELAY"];
    env[@"FIXTURE_EXPECTED_SHA256"]=@"CANCEL";rogue.environment=env;
    NSCAssert([rogue launchAndReturnError:nil],@"Cannot start local negative control");
    NSDate *until=[NSDate dateWithTimeIntervalSinceNow:7];
    while((owner.isRunning||rogue.isRunning) && until.timeIntervalSinceNow>0)
        [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.01]];
    NSCAssert(!owner.isRunning && !rogue.isRunning && owner.terminationStatus==0 && rogue.terminationStatus==0,@"Unowned helper accepted, or owner failed");
    [f finishTerminatingTask:NO];
}
int main(int argc,char **argv) {@autoreleasepool { @try {
    NSCAssert(argc==4,@"Supply the two fixture binaries and isolated home");
    LoginFixture *f=[LoginFixture new];f.fakeSSH=[NSString stringWithUTF8String:argv[1]];
    f.helperURL=[NSString stringWithUTF8String:argv[2]];NSString *home=[NSString stringWithUTF8String:argv[3]];
    f.syntheticSecret=[@"only-this-synthetic-fixture-" stringByAppendingString:NSUUID.UUID.UUIDString];
    go(f,@"Fixture password: ",digest(f.syntheticSecret),home);
    NSString *fp=@"SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    NSString *prompt=[NSString stringWithFormat:@"The authenticity of host 'fixture.invalid (127.0.0.1)' can't be established.\nED25519 key fingerprint is %@.\nAre you sure you want to continue connecting (yes/no/[fingerprint])? ",fp];
    go(f,prompt,digest(fp),home);
    NSString *duplicate=[prompt stringByReplacingOccurrencesOfString:@"Are you sure" withString:
        [NSString stringWithFormat:@"RSA key fingerprint is %@.\nAre you sure",fp]];
    NSCAssert(![MBSSHSignIn classifyPrompt:duplicate hint:1],@"A trust decision must contain exactly one fingerprint");
    NSArray *pinned=[MBSSHSignIn argumentsForSystemSSH:@[@"-o",@"StrictHostKeyChecking=accept-new",@"--",@"test.invalid"]];
    NSArray *required=@[@"-o",@"StrictHostKeyChecking=ask",@"-o",@"UpdateHostKeys=no",@"-o",@"FingerprintHash=sha256",
                        @"-o",@"BatchMode=no",@"-o",@"NumberOfPasswordPrompts=1",@"-o",@"VerifyHostKeyDNS=no"];
    NSCAssert([pinned count]>=required.count && [[pinned subarrayWithRange:NSMakeRange(0,required.count)] isEqual:required],@"Interactive security policy not pinned first");
    f.cancel=YES;go(f,@"Enter passphrase for test key: ",@"CANCEL",home);f.cancel=NO;
    go(f,@"WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!",@"CANCEL",home);
    NSCAssert(![MBSSHSignIn classifyPrompt:@"Are you sure (yes/no)?" hint:0],@"Unidentified trust prompts must fail closed");
    NSCAssert(![MBSSHSignIn classifyPrompt:@"Confirm something" hint:1],@"Generic confirms are not secrets");
    NSCAssert(![MBSSHSignIn classifyPrompt:@"untrusted\x1b[31m" hint:0],@"Reject control sequences");
    NSCAssert(![MBSSHSignIn classifyPrompt:@"notification" hint:2],@"Notifications must not prompt");
    NSTask *unowned=task(f.fakeSSH,@"x",@"CANCEL",home);NSError *err=nil;
    ProductionPathFixture *production=[ProductionPathFixture new];production.helperURL=f.helperURL;
    NSCAssert([production permitsExecutable:@"/usr/bin/ssh"] && [production permitsExecutable:@"/usr/bin/ssh-add"] &&
              ![production permitsExecutable:f.fakeSSH] && ![production permitsExecutable:@"/bin/false"],@"Exact executable allowlist");
    NSCAssert(![production launchTask:unowned forDestination:@"fixture.invalid" window:(id)[NSObject new] error:&err],@"Production rejects non-system binaries");
    LaunchBoundaryProbe *probe=[LaunchBoundaryProbe new];
    probe.executableURL=[NSURL fileURLWithPath:@"/usr/bin/ssh"];
    probe.arguments=@[@"-o",@"StrictHostKeyChecking=accept-new",@"--",@"test.invalid"];
    NSCAssert(![production launchTask:(id)probe forDestination:@"test.invalid" window:(id)[NSObject new] error:nil] && probe.reached,@"Stopped before inert launch boundary");
    NSCAssert([probe.arguments isEqual:pinned],@"System SSH launch boundary lost pinned options");
    rejectUnownedHelper(f,home);
    NSCAssert(f.prompts==5,@"An unowned child was prompted");
    NSData *needle=[f.syntheticSecret dataUsingEncoding:NSUTF8StringEncoding];
    for(NSString *name in [NSFileManager.defaultManager enumeratorAtPath:home]) {
        NSString *path=[home stringByAppendingPathComponent:name];BOOL dir=NO;
        if(![NSFileManager.defaultManager fileExistsAtPath:path isDirectory:&dir] || dir)continue;
        NSData *d=[NSData dataWithContentsOfFile:path];NSCAssert(d && [d rangeOfData:needle options:0 range:NSMakeRange(0,d.length)].location==NSNotFound,@"Synthetic secret persisted");
    }
    puts("passed: owned child, prompt framing, fingerprint, secure response, cancel, changed-key refusal, private file modes and cleanup");
} @catch(NSException *e){fputs("askpass fixture failed (no prompt contents logged)\n",stderr);return 1;} }return 0;}
