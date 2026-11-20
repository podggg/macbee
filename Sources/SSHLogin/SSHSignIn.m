#import "SSHSignIn.h"
#import "AskpassWire.h"
#import <libproc.h>
#import <sys/proc_info.h>
#import <sys/stat.h>
#import <fcntl.h>

static NSError *MBAuthError(NSString *text) {
    return [NSError errorWithDomain:@"Macbee.SSH.SignIn" code:1 userInfo:@{NSLocalizedDescriptionKey:text}];
}
static BOOL MBPermittedText(NSString *s) {
    if (!s.length || s.length>MB_PROMPT_MAX) return NO;
    for (NSUInteger n=0;n<s.length;n++) {
        unichar c=[s characterAtIndex:n];
        if ((c<32 && c!='\n' && c!='\t') || (c>=0x7f && c<=0x9f) ||
            (c>=0x202a && c<=0x202e) || (c>=0x2066 && c<=0x2069) || c==0x200e || c==0x200f) return NO;
    }
    return YES;
}
@interface MBSSHSignIn ()
@property(nonatomic, readwrite, nullable) NSTask *task;
@property(nonatomic, readwrite, nullable) NSString *socketPath;
@property(nonatomic, readwrite) BOOL userCancelled, refusedPrompt;
@property(nonatomic, readwrite, copy, nullable) NSString *promptedIdentityPath;
@property(nonatomic, copy) NSString *directory, *helper, *destination;
@property(nonatomic, weak) NSWindow *window;
@property(nonatomic, strong) NSAlert *alert;
@property(nonatomic, strong) NSSecureTextField *field;
@property(nonatomic, strong) dispatch_source_t listener;
@property(nonatomic) int listenerFD, clientFD;
@property(nonatomic) uint64_t generation, request;
@property(nonatomic) BOOL occupied, reading;
@end
@implementation MBSSHSignIn
- (instancetype)init {
    if ((self=[super init])) {_clientFD=-1;_listenerFD=-1;}
    return self;
}
- (BOOL)permitsExecutable:(NSString *)path {
    return [path isEqual:@"/usr/bin/ssh"] || [path isEqual:@"/usr/bin/ssh-add"];
}
- (BOOL)isSSHExecutable:(NSString *)path {
    return [path isEqual:@"/usr/bin/ssh"];
}
+ (NSArray<NSString *> *)argumentsForSystemSSH:(NSArray<NSString *> *)arguments {
    // OpenSSH reads options in order; pin first values ahead of caller or
    // user-config defaults. Do not pass SSH flags to the separate ssh-add tool.
    return [@[@"-o", @"StrictHostKeyChecking=ask", @"-o", @"UpdateHostKeys=no",
              @"-o", @"FingerprintHash=sha256", @"-o", @"BatchMode=no",
              @"-o", @"NumberOfPasswordPrompts=1", @"-o", @"VerifyHostKeyDNS=no"]
            arrayByAddingObjectsFromArray:arguments ?: @[]];
}
- (NSString *)bundledHelper {
    return [NSBundle.mainBundle pathForAuxiliaryExecutable:@"macbee-askpass"];
}
- (BOOL)launchTask:(NSTask *)task forDestination:(NSString *)destination window:(NSWindow *)window error:(NSError **)error {
    NSAssert(NSThread.isMainThread,@"Run prompt lifecycle on the main thread");
    self.userCancelled = NO;
    self.refusedPrompt = NO;
    self.promptedIdentityPath = nil;
    if (self.task || ![self permitsExecutable:task.executableURL.path] || !MBPermittedText(destination) ||
        [destination rangeOfCharacterFromSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].location!=NSNotFound) {
        if(error)*error=MBAuthError(@"Sign-in could not be started.");return NO;
    }
    if ([self isSSHExecutable:task.executableURL.path])
        task.arguments=[MBSSHSignIn argumentsForSystemSSH:task.arguments];
    NSString *helper=[self bundledHelper];
    if(!helper || ![NSFileManager.defaultManager isExecutableFileAtPath:helper]) {
        if(error)*error=MBAuthError(@"The bundled SSH sign-in helper is missing.");return NO;
    }
    char dir[]="/tmp/macbee-auth.XXXXXXXX";char *created=mkdtemp(dir);
    if(!created) {if(error)*error=MBAuthError(@"Cannot create private sign-in directory.");return NO;}
    self.directory=[NSString stringWithUTF8String:created];self.socketPath=[self.directory stringByAppendingPathComponent:@"a"];
    self.window=window;self.destination=destination;self.helper=helper.stringByResolvingSymlinksInPath;
    int fd=socket(AF_UNIX,SOCK_STREAM,0);self.listenerFD=fd;
    struct sockaddr_un a={0};a.sun_family=AF_UNIX;strlcpy(a.sun_path,self.socketPath.fileSystemRepresentation,sizeof(a.sun_path));
    if(fd<0 || fcntl(fd,F_SETFD,FD_CLOEXEC) || fcntl(fd,F_SETFL,O_NONBLOCK) ||
        bind(fd,(struct sockaddr *)&a,sizeof(a)) || chmod(a.sun_path,0600) || listen(fd,2)) {
        [self finishTerminatingTask:NO];if(error)*error=MBAuthError(@"Cannot open the private sign-in channel.");return NO;
    }
    NSMutableDictionary *env=[(task.environment ?: NSProcessInfo.processInfo.environment) mutableCopy];
    env[@"SSH_ASKPASS"]=helper;env[@"SSH_ASKPASS_REQUIRE"]=@"force";env[@"MACBEE_ASKPASS_SOCKET"]=self.socketPath;
    env[@"MACBEE_ASKPASS_OWNER"]=[NSString stringWithFormat:@"%d",getpid()];env[@"LC_ALL"]=@"C";task.environment=env;
    // No terminal fallback: when askpass is refused, the login is cancelled.
    task.standardInput=[NSFileHandle fileHandleWithNullDevice];
    if(![task launchAndReturnError:error]) {[self finishTerminatingTask:NO];return NO;}
    self.task=task;uint64_t gen=++self.generation;
    self.listener=dispatch_source_create(DISPATCH_SOURCE_TYPE_READ,(uintptr_t)fd,0,dispatch_get_main_queue());
    __weak typeof(self) weak=self;
    dispatch_source_set_event_handler(self.listener,^{[weak acceptOne:gen];});
    // Close this captured descriptor only after dispatch has detached it.
    dispatch_source_set_cancel_handler(self.listener,^{close(fd);});
    dispatch_resume(self.listener);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,300*NSEC_PER_SEC),dispatch_get_main_queue(),^{
        if (weak.generation==gen && weak.task) [weak finishTerminatingTask:YES];
    });
    return YES;
}
- (BOOL)ownedPeer:(int)fd {
    pid_t peer=0;struct proc_bsdinfo b={0};char exec[PROC_PIDPATHINFO_MAXSIZE]={0};
    if (mb_peer_pid(fd,&peer) || !self.task.isRunning ||
        proc_pidinfo(peer,PROC_PIDTBSDINFO,0,&b,sizeof(b))!=sizeof(b) ||
        b.pbi_ppid!=(uint32_t)self.task.processIdentifier || !proc_pidpath(peer,exec,sizeof(exec))) return NO;
    NSString *path=[[NSString stringWithUTF8String:exec] stringByResolvingSymlinksInPath];
    return [path isEqual:self.helper];
}
- (void)acceptOne:(uint64_t)gen {
    if (gen!=self.generation || self.listenerFD<0) return;
    int fd=accept(self.listenerFD,NULL,NULL);if(fd<0)return;
    int one=1;
    if (self.occupied || fcntl(fd,F_SETFD,FD_CLOEXEC) || setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof(one)) ||
        ![self ownedPeer:fd]) {close(fd);return;}
    self.occupied=YES;self.reading=YES;self.clientFD=fd;uint64_t req=++self.request;uint64_t deadline=mb_now_ms()+3000;
    __weak typeof(self) weak=self;
    // One worker owns the descriptor; shutdown (but do not close) it on cancel
    // so an older completion can never operate on a reused descriptor.
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{
        uint32_t h[2]={0};char raw[MB_PROMPT_MAX+1]={0};BOOL ok=mb_io(fd,h,sizeof(h),0,deadline)==0;
        unsigned hint=ntohl(h[0]),n=ntohl(h[1]);
        ok=ok && hint<=2 && n>0 && n<=MB_PROMPT_MAX && !mb_io(fd,raw,n,0,deadline);
        NSString *prompt=ok?[[NSString alloc] initWithBytes:raw length:n encoding:NSUTF8StringEncoding]:nil;
        mb_wipe(raw,sizeof(raw));
        dispatch_async(dispatch_get_main_queue(),^{
            MBSSHSignIn *self=weak;
            if (!self || gen!=self.generation || req!=self.request) {close(fd);return;}
            self.reading=NO;
            if (!MBPermittedText(prompt) || ![self ownedPeer:fd]) {[self reply:nil fd:fd gen:gen request:req];return;}
            [self presentPrompt:prompt hint:hint completion:^(NSData *answer){[self reply:answer fd:fd gen:gen request:req];}];
        });
    });
}
- (void)reply:(NSData *)answer fd:(int)fd gen:(uint64_t)gen request:(uint64_t)req {
    if(gen!=self.generation || req!=self.request || self.clientFD!=fd) return;
    if (!answer.length || answer.length>MB_SECRET_MAX || ![self ownedPeer:fd]) {
        // Do not send an empty password. Kill this owned login and invalidate
        // its channel, so OpenSSH cannot advance to another prompt or key.
        if([answer isKindOfClass:NSMutableData.class])[(NSMutableData *)answer resetBytesInRange:NSMakeRange(0,answer.length)];
        [self finishTerminatingTask:YES];return;
    }
    self.occupied=NO;self.clientFD=-1;
    uint32_t length=htonl((uint32_t)answer.length);uint64_t deadline=mb_now_ms()+200;
    if(!mb_io(fd,&length,sizeof(length),1,deadline)) mb_io(fd,(void*)answer.bytes,answer.length,1,deadline);
    if([answer isKindOfClass:NSMutableData.class])[(NSMutableData *)answer resetBytesInRange:NSMakeRange(0,answer.length)];
    close(fd);
}
+ (NSDictionary *)classifyPrompt:(NSString *)prompt hint:(unsigned)hint {
    if(!MBPermittedText(prompt) || hint==2) return nil;
    NSString *lower=prompt.lowercaseString;
    if([lower containsString:@"remote host identification has changed"] ||
       [lower containsString:@"revoked host key"] || [lower containsString:@"offending key"]) return nil;
    if([prompt hasPrefix:@"The authenticity of host '"]) {
        // Only a complete normal system-SSH host-key prompt may offer Trust.
        NSRegularExpression *head=[NSRegularExpression regularExpressionWithPattern:
            @"^The authenticity of host '([^'\\n]{1,300})' can't be established" options:0 error:nil];
        NSRegularExpression *finger=[NSRegularExpression regularExpressionWithPattern:
            @"(?:^|\\n)([A-Z0-9-]{2,32}) key fingerprint is:? (SHA256:[A-Za-z0-9+/]{43}=?)\\.?(?=\\n|$)" options:0 error:nil];
        NSTextCheckingResult *h=[head firstMatchInString:prompt options:0 range:NSMakeRange(0,prompt.length)];
        NSArray *f=[finger matchesInString:prompt options:0 range:NSMakeRange(0,prompt.length)];
        if(!h || f.count!=1 || ![prompt containsString:@"Are you sure you want to continue connecting (yes/no/[fingerprint])?"]) return nil;
        return @{@"kind":@"trust",@"host":[prompt substringWithRange:[h rangeAtIndex:1]],
                 @"type":[prompt substringWithRange:[f.firstObject rangeAtIndex:1]],
                 @"fingerprint":[prompt substringWithRange:[f.firstObject rangeAtIndex:2]]};
    }
    if(hint || [lower containsString:@"(yes/no"] || [lower containsString:@"host key verification"]) return nil;
    return @{@"kind":@"secret"};
}
+ (nullable NSString *)identityPathFromPrompt:(NSString *)prompt {
    if (!MBPermittedText(prompt)) return nil;
    NSRegularExpression *pattern = [NSRegularExpression regularExpressionWithPattern:
        @"^Enter passphrase for key '([^'\\n]{1,512})':\\s*$" options:0 error:nil];
    NSTextCheckingResult *match = [pattern firstMatchInString:prompt options:0 range:NSMakeRange(0, prompt.length)];
    if (!match) return nil;
    NSString *path = [prompt substringWithRange:[match rangeAtIndex:1]];
    if (!path.length || [path hasPrefix:@"-"] || !MBPermittedText(path)) return nil;
    return path;
}
- (void)presentPrompt:(NSString *)prompt hint:(unsigned)hint completion:(void (^)(NSData *))completion {
    NSDictionary *kind=[MBSSHSignIn classifyPrompt:prompt hint:hint];
    if(!kind || !self.window || self.window.attachedSheet) {
        if (!kind) self.refusedPrompt = YES;
        completion(nil);
        return;
    }
    BOOL trust=[kind[@"kind"] isEqual:@"trust"];
    if (!trust) {
        NSString *identity = [MBSSHSignIn identityPathFromPrompt:prompt];
        if (identity.length) self.promptedIdentityPath = identity;
    }
    NSAlert *alert=[NSAlert new];self.alert=alert;
    alert.messageText=trust?@"Trust this SSH host key?":@"Sign in to the selected SSH destination";
    alert.informativeText=trust?
        [NSString stringWithFormat:@"Selected: %@\nSSH host: %@\nKey: %@\nFingerprint: %@\n\nCompare this fingerprint with the receiving Mac before trusting it.",self.destination,kind[@"host"],kind[@"type"],kind[@"fingerprint"]]:
        [NSString stringWithFormat:@"Selected: %@\n\nSSH prompt:\n%@",self.destination,prompt];
    // Cancel is the default for a trust decision. A normal Return key cannot grant trust.
    if(trust){[alert addButtonWithTitle:@"Cancel"];[alert addButtonWithTitle:@"Trust and Continue"];}
    else{[alert addButtonWithTitle:@"Continue"];[alert addButtonWithTitle:@"Cancel"];}
    NSSecureTextField *field=nil;
    if(!trust) {
        field=[[NSSecureTextField alloc] initWithFrame:NSMakeRect(0,0,340,24)];
        field.placeholderString=@"Password, passphrase, or verification response";
        field.allowsCharacterPickerTouchBarItem=NO;
        alert.accessoryView=field;self.field=field;alert.window.initialFirstResponder=field;
    }
    __weak MBSSHSignIn *weak=self;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        NSMutableData *answer=nil;
        if(trust && response==NSAlertSecondButtonReturn)
            answer=[[kind[@"fingerprint"] dataUsingEncoding:NSUTF8StringEncoding] mutableCopy];
        else if(!trust && response==NSAlertFirstButtonReturn)
            answer=[[field.stringValue dataUsingEncoding:NSUTF8StringEncoding] mutableCopy];
        else
            weak.userCancelled = YES;
        field.stringValue=@"";weak.field=nil;weak.alert=nil;
        completion(answer);
    }];
}
- (void)cancel {
    self.userCancelled = YES;
    [self finishTerminatingTask:YES];
}
- (void)finishTerminatingTask:(BOOL)terminate {
    // This invalidates worker callbacks before cancelling their modal sheet.
    ++self.generation;++self.request;
    if(self.clientFD>=0) {
        shutdown(self.clientFD,SHUT_RDWR);
        if(!self.reading) close(self.clientFD);
        self.clientFD=-1;
    }
    self.occupied=NO;self.reading=NO;
    self.field.stringValue=@"";
    if(self.alert.window.sheetParent) [self.alert.window.sheetParent endSheet:self.alert.window returnCode:NSModalResponseCancel];
    self.alert=nil;self.field=nil;
    if(self.listener) { dispatch_source_cancel(self.listener);self.listener=nil; }
    else if(self.listenerFD>=0) close(self.listenerFD);
    self.listenerFD=-1;
    if(self.socketPath) unlink(self.socketPath.fileSystemRepresentation);
    if(self.directory) rmdir(self.directory.fileSystemRepresentation);
    self.socketPath=nil;self.directory=nil;
    if(terminate && self.task.isRunning) [self.task terminate];
    self.task=nil;
}
- (void)dealloc { [self finishTerminatingTask:YES]; }
@end
