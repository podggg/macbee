#import "ApplicationSession.h"
#import "ControlledCapture.h"
#import "SystemAudioSource.h"
#import "SessionStopDiagnostics.h"
#include "../AudioPipeline/application_pcm.h"
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <math.h>

// 3s renderer lease + in-flight 2s inventory + 4s exit + 2s postflight + margin.
static const NSTimeInterval MBRestoreTimeout=12;

typedef NS_ENUM(NSUInteger,Phase){Idle,Preparing,Sharing,Stopping};
@interface MBApplicationSession ()
@property Phase phase;
@property NSUInteger generation;
@property MBApplicationSource *source;
@property(atomic,strong) MBControlledCapture *capture;
@property(nonatomic,strong) dispatch_queue_t captureQueue;
@property(atomic) BOOL cancelRequested;
@property BOOL renderersLaunched,cleanupFailed;
@property NSTask *backend;
@property NSPipe *input;
@property NSPipe *output;
@property NSMutableData *lines;
@property NSMutableData *pending;
@property NSTimer *timer; // Main thread owns UI/lifecycle and prepare/stop deadlines only while the pump runs.
@property(nonatomic,strong) dispatch_queue_t pumpQueue;
@property(nonatomic,strong) dispatch_source_t pumpTimer;
@property(atomic) NSTimeInterval mainHeartbeat;
@property NSTimeInterval lastPumpTurn,maxPumpGap;
@property BOOL pumpFailed; // Accessed by the current pipe owner only.
@property NSTimeInterval deadline;
@property NSTimeInterval leaseDeadline;
@property NSTimeInterval pendingSince;
@property BOOL backendReady;
@property BOOL backendFinalReceived;
@property BOOL cleanupVerified;
@property uint64_t frames;
@property BOOL clippingNoted;
@property BOOL callbackNoted,packetNoted,writeNoted,nonzeroNoted;
@property(nonatomic,strong) id presentationActivity;
@property BOOL captureStartInProgress;
@property BOOL releaseInProgress;
@property NSString *failureReason;
@property(nonatomic,readwrite,copy) NSString *sharingState;
@property(nonatomic,readwrite,copy,nullable) NSString *authRequiredReceiverID;
@property NSArray<NSString *> *systemOutputs,*applicationOutputs;
@property NSDictionary *systemRenderer;
@property MBSystemAudioSource *systemSource;
@property NSTimeInterval nextSourceHealth;
@property NSString *diagnosticSession,*firstStopCode,*firstBackendCode;
@property NSDictionary *firstStopSnapshot;
@property NSTimeInterval sessionStarted,lastTick,maxTickGap,lastPipeWrite,lastPCMRead,lastHealthQueued;
@property int lastPipeErrno;
- (void)stopWithReason:(NSString *)reason;
- (void)persistStopEvidence;
@end
@implementation MBApplicationSession
- (instancetype)init {
    self=[super init];
    if(self){
        self.captureQueue=dispatch_queue_create("macbee.selected-app.lifecycle",
            dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_USER_INITIATED,0));
    }
    return self;
}
- (id)beginPresentationActivity {
    return [NSProcessInfo.processInfo beginActivityWithOptions:(NSActivityUserInitiated|NSActivityLatencyCritical)
        reason:@"Keep the active Macbee system audio session responsive"];
}
- (void)endPresentationActivity:(id)activity {[NSProcessInfo.processInfo endActivity:activity];}
- (void)releasePresentationActivity {
    id activity=self.presentationActivity;
    self.presentationActivity=nil;
    if(activity)[self endPresentationActivity:activity];
}
- (BOOL)running {return self.phase!=Idle;}
- (BOOL)outputsConnected {return self.backendReady && (self.phase==Preparing || self.phase==Sharing);}
- (void)setState:(NSString *)state {self.sharingState=state;if(self.stateHandler)self.stateHandler(state);}
- (NSTimeInterval)now {return NSProcessInfo.processInfo.systemUptime;}
- (NSTimeInterval)maximumSharingDuration {
    if([[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeManualSessionEnabled"] boolValue])return 0;
    id limit=[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeMaximumSharingSeconds"];
    if(!limit)return MBApplicationSessionMaxSeconds;
    // A diagnostic bundle may narrow, never enlarge, the finite session limit.
    if(![limit isKindOfClass:NSNumber.class] || [limit doubleValue]<1 ||
       [limit doubleValue]>MBApplicationSessionMaxSeconds || floor([limit doubleValue])!=[limit doubleValue])return 1;
    return [limit doubleValue];
}
- (void)report:(NSString *)text {if(self.statusHandler)self.statusHandler(text);}
- (void)diagnose:(NSString *)stage fields:(NSDictionary *)fields {
    NSMutableDictionary *event=[fields mutableCopy]?:[NSMutableDictionary dictionary];
    event[@"stage"]=stage;event[@"generation"]=@(self.generation);event[@"phase"]=@(self.phase);
    event[@"uptime"]=@(NSProcessInfo.processInfo.systemUptime);
    if(self.diagnosticHandler)self.diagnosticHandler(event);
}
- (void)diagnoseWorkerStage:(NSString *)stage generation:(NSUInteger)generation fields:(NSDictionary *)fields {
    NSMutableDictionary *event=[fields mutableCopy]?:[NSMutableDictionary dictionary];
    event[@"stage"]=stage;event[@"generation"]=@(generation);event[@"uptime"]=@(NSProcessInfo.processInfo.systemUptime);
    dispatch_async(dispatch_get_main_queue(),^{if(self.generation==generation && self.diagnosticHandler)self.diagnosticHandler(event);});
}
- (NSDictionary *)stopSnapshot {
    NSMutableDictionary *row=[@{@"schema":@1,@"session":self.diagnosticSession?:@"unlinked",
      @"generation":@(self.generation),@"phase":@(self.phase),@"uptime":@(self.now),
      @"elapsed":@(MAX(0,self.now-self.sessionStarted)),@"first_cause":self.firstStopCode?:@"none",
      @"first_backend_cause":self.firstBackendCode?:@"none",@"last_tick":@(self.lastTick),
      @"max_tick_gap":@(self.maxTickGap),@"max_pump_gap":@(self.maxPumpGap),@"last_pipe_write":@(self.lastPipeWrite),
      @"last_pcm_read":@(self.lastPCMRead),@"last_health_queued":@(self.lastHealthQueued),
      @"pending_bytes":@(self.pending.length),@"pipe_errno":@(self.lastPipeErrno)} mutableCopy];
    NSDictionary *capture=[self.capture diagnosticSnapshot];
    for(NSString *key in @[@"fault",@"fault_reason",@"io_started",@"queued_frames",@"health_valid_until"])
        if([capture[key] isKindOfClass:NSNumber.class])row[key]=capture[key];
    return row;
}
- (void)persistStopEvidence {
    if(![[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeUserLaunchableCandidate"] boolValue])return;
    // Snapshot scalars on the owner; disk IO never delays Stop or the audio timer.
    NSDictionary *record=@{@"first_stop":self.firstStopSnapshot?:[self stopSnapshot],
      @"latest":[self stopSnapshot],@"cleanup_verified":@(self.cleanupVerified)};
    BOOL failure=self.failureReason.length>0 || ![@[@"user_stop",@"app_exit"] containsObject:self.firstStopCode?:@""];
    NSString *parent=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Macbee"];
    static dispatch_queue_t writer;static dispatch_once_t once;
    dispatch_once(&once,^{writer=dispatch_queue_create("macbee.stop-evidence",DISPATCH_QUEUE_SERIAL);});
    dispatch_async(writer,^{
        // No caller-supplied paths, content, PIDs, app names, device names or raw errors.
        int fd=open(parent.fileSystemRepresentation,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(fd<0)return;
        struct stat info;if(fstat(fd,&info)!=0 || info.st_uid!=getuid()){close(fd);return;}
        if(mkdirat(fd,"diagnostics",0700)!=0 && errno!=EEXIST){close(fd);return;}close(fd);
        MBWriteStopSnapshot([parent stringByAppendingPathComponent:@"diagnostics"],record,failure);
    });
}
- (void)rememberBackendReason:(NSDictionary *)event {
    NSString *receiverID=event[@"auth_required_receiver"];
    if([receiverID isKindOfClass:NSString.class] && !self.authRequiredReceiverID.length){
        NSRegularExpression *pattern=[NSRegularExpression regularExpressionWithPattern:@"^(remote|r-[a-f0-9]{16})$" options:0 error:nil];
        if([pattern numberOfMatchesInString:receiverID options:0 range:NSMakeRange(0,receiverID.length)]==1)
            self.authRequiredReceiverID=receiverID;
    }
    if(self.firstBackendCode.length)return;
    NSString *value=event[@"reason_code"];
    NSSet *allowed=[NSSet setWithArray:@[@"source_health_timeout",@"source_prepare_timeout",@"output_pacing_stall",@"server_media_stale",@"server_read_timeout",@"server_read_invalid",@"server_read_unavailable",
      @"sender_pacing_recovery_limit",@"server_pacing_recovery_limit",@"source_backlog",@"pcm_backpressure",@"transport_peer_exit",@"renderer_heartbeat_write",@"renderer_heartbeat_partial",
      @"runtime_capacity",@"truncated_source_packet",@"source_end",@"source_eof",@"signal_term",@"signal_hup",@"signal_int",
      @"interrupt",@"transport_check_failed",@"source_read_failed",@"protocol_failed",@"pcm_write_failed",@"startup_failed",@"backend_failed",@"selected_source_timeout",@"auth_required"]];
    self.firstBackendCode=[value isKindOfClass:NSString.class] && [allowed containsObject:value]?value:@"backend_unclassified";
}
- (BOOL)startSource:(MBApplicationSource *)source error:(NSError **)error {
    if(self.running)return NO;self.systemOutputs=nil;self.applicationOutputs=nil;return [self startRequestSource:source error:error];
}
- (NSArray<NSString *> *)validatedOutputIDs:(NSArray<NSString *> *)outputs {
    BOOL configured=[[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeConfiguredOutputsEnabled"] boolValue];
    if(![outputs isKindOfClass:NSArray.class] || !outputs.count || outputs.count>(configured?16:2) || [NSSet setWithArray:outputs].count!=outputs.count)return nil;
    NSRegularExpression *pattern=[NSRegularExpression regularExpressionWithPattern:@"^(local|remote|r-[a-f0-9]{16})$" options:0 error:nil];
    for(id item in outputs)if(![item isKindOfClass:NSString.class] ||
      (configured?[pattern numberOfMatchesInString:item options:0 range:NSMakeRange(0,[item length])]!=1:(![item isEqual:@"local"] && ![item isEqual:@"remote"])))return nil;
    return outputs;
}
- (BOOL)startSource:(MBApplicationSource *)source outputs:(NSArray<NSString *> *)outputs error:(NSError **)error {
    if(self.running || ![[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeConfiguredOutputsEnabled"] boolValue])return NO;
    NSArray *valid=[self validatedOutputIDs:outputs];if(!valid)return NO;
    self.systemOutputs=nil;self.applicationOutputs=valid;return [self startRequestSource:source error:error];
}
- (BOOL)startSystemAudioWithOutputs:(NSArray<NSString *> *)outputs error:(NSError **)error {
    if(self.running || ![[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeSystemAudioEnabled"] boolValue])return NO;
    NSArray *valid=[self validatedOutputIDs:outputs];if(!valid)return NO;
    self.applicationOutputs=nil;self.systemOutputs=valid;return [self startRequestSource:nil error:error];
}
- (BOOL)startRequestSource:(MBApplicationSource *)source error:(NSError **)error {
    (void)error;
    if(!NSThread.isMainThread || self.running || self.backend.isRunning || (!source && !self.systemOutputs.count))return NO;
    self.source=source;self.generation++;self.phase=Preparing;
    self.diagnosticSession=NSUUID.UUID.UUIDString;self.firstStopCode=nil;self.firstBackendCode=nil;self.firstStopSnapshot=nil;
    self.sessionStarted=self.now;self.lastTick=self.now;self.maxTickGap=0;self.maxPumpGap=0;self.lastPumpTurn=0;self.lastPipeWrite=0;self.lastPCMRead=0;self.lastHealthQueued=0;self.lastPipeErrno=0;self.frames=0;self.backendReady=NO;self.backendFinalReceived=NO;self.cleanupVerified=NO;self.clippingNoted=NO;self.failureReason=nil;self.callbackNoted=NO;self.packetNoted=NO;self.writeNoted=NO;self.nonzeroNoted=NO;
    self.cancelRequested=NO;self.renderersLaunched=NO;self.cleanupFailed=NO;self.releaseInProgress=NO;self.authRequiredReceiverID=nil;
    self.captureStartInProgress=YES;self.deadline=INFINITY;
    self.lines=[NSMutableData data];self.pending=[NSMutableData data];
    self.systemRenderer=nil;self.systemSource=nil;self.nextSourceHealth=0;[self setState:@"preparing"];
    [self diagnose:@"session_prepare" fields:nil];
    [self report:self.systemOutputs.count?@"Preparing selected outputs. You can cancel while connections are checked.":@"Preparing selected-app input. You can cancel while the audio service responds."];
    __weak MBApplicationSession *weak=self;
    self.timer=[NSTimer timerWithTimeInterval:.01 repeats:YES block:^(NSTimer *timer){
        MBApplicationSession *session=weak;if(session)[session tick];else[timer invalidate];
    }];
    [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
    if(self.systemOutputs.count){
        self.captureStartInProgress=NO;NSError *failure=nil;
        if(![self prepareRenderers:&failure]){self.failureReason=failure.localizedDescription?:@"Selected outputs could not be prepared";[self stopWithReason:@"renderer_prepare_failed"];return YES;}
        self.renderersLaunched=YES;self.deadline=self.now+20;
        [self report:@"Connecting the selected outputs. No system audio is being captured yet."];return YES;
    }
    NSUInteger generation=self.generation;
    dispatch_async(self.captureQueue,^{@autoreleasepool{
        NSError *failure=nil;
        [self diagnoseWorkerStage:@"prepare_revalidate_enter" generation:generation fields:nil];
        BOOL valid=!self.cancelRequested && [source revalidateRequiringOutput:YES error:&failure];
        [self diagnoseWorkerStage:@"prepare_revalidate_return" generation:generation fields:@{@"valid":@(valid)}];
        BOOL prepared=valid && !self.cancelRequested && [self prepareSelectedCapture:&failure];
        if(prepared && !self.cancelRequested)prepared=[source revalidateRequiringOutput:YES error:&failure];
        dispatch_async(dispatch_get_main_queue(),^{
            if(generation!=self.generation)return;
            self.captureStartInProgress=NO;
            if(self.phase==Stopping){[self finish:self.cleanupVerified];return;}
            if(!prepared){self.failureReason=failure.localizedDescription?:@"The selected app could not be prepared";[self stopWithReason:@"source_prepare_failed"];return;}
            NSError *backendError=nil;
            if(![self prepareRenderers:&backendError]){self.failureReason=backendError.localizedDescription?:@"The speakers could not be prepared";[self stopWithReason:@"renderer_prepare_failed"];return;}
            self.renderersLaunched=YES;self.deadline=self.now+10;
            [self report:@"Preparing both speakers. The selected app still plays normally."];
        });
    }});
    return YES; // Accepted, not a claim that asynchronous HAL setup succeeded.
}
- (BOOL)boundedSystemPacing {
    return self.systemOutputs.count && [[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeBoundedSystemPacing"] boolValue];
}
- (NSTask *)newBackendTask {return [NSTask new];}
- (BOOL)prepareRenderers:(NSError **)error {
    NSBundle *bundle=NSBundle.mainBundle;
    BOOL sourceEnabled=self.systemOutputs.count?
        [[bundle objectForInfoDictionaryKey:@"MacbeeSystemAudioEnabled"] boolValue]:
        [[bundle objectForInfoDictionaryKey:@"MacbeeApplicationTestEnabled"] boolValue];
    if(!sourceEnabled){
        if(error)*error=[NSError errorWithDomain:@"MacbeeApplicationSession" code:1 userInfo:@{NSLocalizedDescriptionKey:(self.systemOutputs.count?@"System Audio is disabled in this build.":@"Ordinary app sharing is disabled in this build.")}];return NO;
    }
    NSString *root=[bundle objectForInfoDictionaryKey:@"MacbeeDevelopmentRoot"];
    NSString *python=[bundle objectForInfoDictionaryKey:@"MacbeePythonExecutable"];
    BOOL packaged=[[bundle objectForInfoDictionaryKey:@"MacbeeUserLaunchableCandidate"] boolValue];
    if(packaged){root=bundle.resourcePath;python=@"/usr/bin/python3";}
    if(!root.length || !python.length)return NO;
    self.backend=[self newBackendTask];
    self.backend.executableURL=[NSURL fileURLWithPath:python];
    if(packaged){
        NSMutableDictionary *environment=[NSProcessInfo.processInfo.environment mutableCopy];
        environment[@"MACBEE_RUNTIME_HOME"]=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Macbee"];
        environment[@"MACBEE_ASSET_ROOT"]=root;
        [environment removeObjectForKey:@"MACBEE_OWNED_MARKER_TIMING"];
        [environment removeObjectForKey:@"PYTHONPATH"];
        [environment removeObjectForKey:@"PYTHONHOME"];
        self.backend.environment=environment;
    }
    NSMutableArray *arguments=[@[@"-u",@"-B",@"-E",@"-s"] mutableCopy];
    if(self.systemOutputs.count){
        [arguments addObjectsFromArray:@[[root stringByAppendingPathComponent:@"tools/run_system_relay.py"],@"--system-audio-stdin",@"--outputs",[self.systemOutputs componentsJoinedByString:@","]]];
        if([[bundle objectForInfoDictionaryKey:@"MacbeeConfiguredOutputsEnabled"] boolValue])[arguments addObject:@"--configured-outputs"];
    }
    else {
        [arguments addObjectsFromArray:@[[root stringByAppendingPathComponent:@"tools/run_application_relay.py"],@"--selected-app-stdin"]];
        if([[bundle objectForInfoDictionaryKey:@"MacbeeManualSessionEnabled"] boolValue])[arguments addObject:@"--manual-session"];
        if(self.applicationOutputs.count)[arguments addObjectsFromArray:@[@"--outputs",[self.applicationOutputs componentsJoinedByString:@","]]];
    }
    NSMutableDictionary *diagnosticEnvironment=[(self.backend.environment?:NSProcessInfo.processInfo.environment) mutableCopy];
    diagnosticEnvironment[@"MACBEE_DIAGNOSTIC_SESSION"]=self.diagnosticSession;
    if(self.sshControlPaths.count){
        NSData *ctlJSON=[NSJSONSerialization dataWithJSONObject:self.sshControlPaths options:0 error:nil];
        if(ctlJSON)diagnosticEnvironment[@"MACBEE_SSH_CONTROL_PATHS"]=[[NSString alloc] initWithData:ctlJSON encoding:NSUTF8StringEncoding];
    }else [diagnosticEnvironment removeObjectForKey:@"MACBEE_SSH_CONTROL_PATHS"];
    self.backend.environment=diagnosticEnvironment;
    self.backend.arguments=arguments;
    self.input=[NSPipe pipe];self.output=[NSPipe pipe];self.backend.standardInput=self.input;
    self.backend.standardOutput=self.output;self.backend.standardError=self.output;
    // Configure the private input before launching anything. Failure here
    // must not leave a backend behind while the session reports Idle.
    int fd=self.input.fileHandleForWriting.fileDescriptor,flags=fcntl(fd,F_GETFL);
    if(flags==-1 || fcntl(fd,F_SETFL,flags|O_NONBLOCK)==-1 || fcntl(fd,F_SETNOSIGPIPE,1)==-1){
        if(error)*error=[NSError errorWithDomain:NSPOSIXErrorDomain code:errno userInfo:nil];
        [self.input.fileHandleForWriting closeFile];self.backend=nil;return NO;
    }
    NSUInteger generation=self.generation;__weak MBApplicationSession *weak=self;
    self.output.fileHandleForReading.readabilityHandler=^(NSFileHandle *handle){
        NSData *data=handle.availableData;
        if(!data.length){
            handle.readabilityHandler=nil;
            dispatch_async(dispatch_get_main_queue(),^{
                MBApplicationSession *s=weak;
                if(s && s.generation==generation && s.running && s.phase!=Stopping){
                    s.failureReason=@"Backend control pipe closed before cleanup was confirmed";[s stopWithReason:@"backend_control_eof"];
                }
            });return;
        }
        // Record receipt before main-thread dispatch; do not inspect or log contents.
        NSDictionary *receipt=@{@"stage":@"backend_stdout_received",@"generation":@(generation),@"bytes":@(data.length),@"uptime":@(NSProcessInfo.processInfo.systemUptime)};
        dispatch_async(dispatch_get_main_queue(),^{
            MBApplicationSession *reader=weak;if(reader.diagnosticHandler)reader.diagnosticHandler(receipt);
            MBApplicationSession *s=weak;if(!s || s.generation!=generation || !s.running)return;
            if(s.lines.length+data.length>32768){s.failureReason=@"Backend output exceeded its bound";[s stopWithReason:@"backend_control_overflow"];return;}
            [s.lines appendData:data];
            while(s.lines.length){
                const uint8_t *bytes=s.lines.bytes,*nl=memchr(bytes,'\n',s.lines.length);if(!nl)break;
                NSUInteger n=(NSUInteger)(nl-bytes);NSData *line=[s.lines subdataWithRange:NSMakeRange(0,n)];
                [s.lines replaceBytesInRange:NSMakeRange(0,n+1) withBytes:NULL length:0];
                NSDictionary *event=[NSJSONSerialization JSONObjectWithData:line options:0 error:nil];
                if(![event isKindOfClass:NSDictionary.class])continue;
                if([event[@"event"] isEqual:@"ready"]){
                    [s diagnose:@"backend_ready_main_dispatch" fields:nil];
                    NSMutableArray *expected=[NSMutableArray array];
                    if(s.systemOutputs.count || s.applicationOutputs.count)for(NSString *side in (s.systemOutputs?:s.applicationOutputs))[expected addObject:[NSString stringWithFormat:@"CoreAudioPlayer -> %@ default output",side]];
                    else [expected addObjectsFromArray:@[@"CoreAudioPlayer -> selected-app speakers",@"CoreAudioPlayer -> selected-app speakers"]];
                    NSArray *chosen=s.systemOutputs?:s.applicationOutputs;
                    BOOL systemValid=!chosen.count || ([event[@"outputs"] isEqual:chosen] && [event[@"backend_pid"] intValue]==s.backend.processIdentifier);
                    if(s.systemOutputs.count)s.systemRenderer=[event[@"renderer"] isKindOfClass:NSDictionary.class]?event[@"renderer"]:nil;
                    if(!systemValid || ![event[@"sinks"] isEqual:expected]){s.failureReason=@"Unexpected destinations; capture was not started";[s stopWithReason:@"destination_mismatch"];}
                    else [s renderersReady:generation];
                } else if([event[@"event"] isEqual:@"output"]){
                    NSArray *chosen=s.systemOutputs?:s.applicationOutputs;
                    if([chosen containsObject:event[@"output"]] && [event[@"state"] isEqual:@"offline"] && s.outputStateHandler)
                        s.outputStateHandler(event[@"output"],@"offline");
                } else if([event[@"event"] isEqual:@"state"] && s.systemOutputs.count && s.phase==Sharing){
                    if([@[@"waiting",@"playing",@"degraded"] containsObject:event[@"state"]?:@""])[s setState:event[@"state"]];
                } else if([event[@"event"] isEqual:@"stopping"]){
                    [s rememberBackendReason:event];
                    if([event[@"error"] isKindOfClass:NSString.class])s.failureReason=event[@"error"];
                    else if(s.phase!=Stopping)s.failureReason=@"The audio backend stopped unexpectedly.";
                    [s stopWithReason:@"backend_stopping"];[s persistStopEvidence];
                } else if([event[@"event"] isEqual:@"stopped"] || [event[@"event"] isEqual:@"failed"]){
                    s.backendFinalReceived=YES;[s rememberBackendReason:event];
                    if([event[@"event"] isEqual:@"failed"])s.failureReason=[event[@"error"] isKindOfClass:NSString.class]?event[@"error"]:@"Backend failed";
                    id cleanup=event[@"cleanup_verified"];
                    [s renderersStopped:[cleanup isKindOfClass:NSNumber.class] && [cleanup boolValue] generation:generation];[s persistStopEvidence];
                }
            }
        });
    };
    self.backend.terminationHandler=^(NSTask *ended){
        (void)ended;dispatch_async(dispatch_get_main_queue(),^{
            MBApplicationSession *s=weak;if(!s || s.generation!=generation)return;s.backend=nil;
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
                if(s.generation==generation && s.running && !s.backendFinalReceived && !s.cleanupVerified){
                    // A meaningful stopping/failed record outranks this fallback.
                    if(!s.failureReason.length)s.failureReason=@"Backend exited without confirmed renderer cleanup";
                    [s stopWithReason:@"backend_exit_unconfirmed"];
                }
            });
        });
    };
    if(![self.backend launchAndReturnError:error]){self.output.fileHandleForReading.readabilityHandler=nil;self.backend=nil;[self.input.fileHandleForWriting closeFile];return NO;}
    return YES;
}
- (BOOL)prepareSelectedCapture:(NSError **)error {
    self.capture=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:self.captureQueue];
    __weak MBApplicationSession *weak=self;
    self.capture.diagnosticHandler=^(NSDictionary *event){
        dispatch_async(dispatch_get_main_queue(),^{MBApplicationSession *session=weak;if(session.diagnosticHandler)session.diagnosticHandler(event);});
    };
    if(self.cancelRequested){[self.capture cancelPendingStart];return NO;}
    return [self.capture prepareForSelectedSource:self.source error:error];
}
- (BOOL)beginSelectedCapture:(NSError **)error {return [self.capture activatePreparedSource:error];}
- (BOOL)prepareSystemCapture:(NSError **)error {
    pid_t backend=self.backend.processIdentifier;Class sourceClass=NSClassFromString(@"MBSystemAudioSource");
    if(!sourceClass)return NO;
    if([self.systemOutputs containsObject:@"local"]){
        NSDictionary *identity=self.systemRenderer;
        if([identity[@"backend_pid"] intValue]!=backend || ![identity[@"guardian_pid"] isKindOfClass:NSNumber.class] || ![identity[@"renderer_pid"] isKindOfClass:NSNumber.class])return NO;
        NSString *root=[[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeUserLaunchableCandidate"] boolValue]?NSBundle.mainBundle.resourcePath:
            [[[NSBundle.mainBundle objectForInfoDictionaryKey:@"MacbeeDevelopmentRoot"] stringByAppendingPathComponent:@".local-app/relay-runtime"] copy];
        self.systemSource=[[sourceClass alloc] initWithOwnedBackendPID:backend guardianPID:[identity[@"guardian_pid"] intValue]
            rendererPID:[identity[@"renderer_pid"] intValue] rendererExecutable:[root stringByAppendingPathComponent:@"bin/snapclient-application"] error:error];
    }else self.systemSource=[[sourceClass alloc] initWithoutLocalRendererForOwnedBackendPID:backend error:error];
    if(!self.systemSource)return NO;
    self.capture=[[MBControlledCapture alloc] initWithSelectedLifecycleQueue:self.captureQueue];
    if(self.cancelRequested){[self.capture cancelPendingStart];return NO;}
    return [self.capture prepareForSystemAudioSource:self.systemSource error:error];
}
- (void)renderersReady:(NSUInteger)generation {
    if(generation!=self.generation || self.phase!=Preparing || self.captureStartInProgress || self.backendReady)return;
    self.backendReady=YES;self.captureStartInProgress=YES;
    if(self.systemOutputs.count)self.deadline=self.now+60;
    MBApplicationSource *source=self.source;
    dispatch_async(self.captureQueue,^{@autoreleasepool{
        NSError *failure=nil;
        [self diagnoseWorkerStage:@"capture_revalidate_enter" generation:generation fields:nil];
        BOOL valid=!self.cancelRequested && (self.systemOutputs.count?[self prepareSystemCapture:&failure]:[source revalidateRequiringOutput:YES error:&failure]);
        [self diagnoseWorkerStage:@"capture_revalidate_return" generation:generation fields:@{@"valid":@(valid)}];
        BOOL began=valid && !self.cancelRequested && [self beginSelectedCapture:&failure];
        dispatch_async(dispatch_get_main_queue(),^{
            if(generation!=self.generation)return;
            self.captureStartInProgress=NO;
            if(self.phase==Stopping){
                [self suspendCapture];
                if(self.cleanupVerified || self.now>=self.leaseDeadline)[self finish:self.cleanupVerified];
                return;
            }
            if(!began){self.failureReason=failure.localizedDescription?:(self.systemOutputs.count?@"System Audio could not be started":@"The selected app could not be captured");[self stopWithReason:@"capture_start_failed"];return;}
            NSTimeInterval limit=self.maximumSharingDuration;
            self.phase=Sharing;
            if(self.systemOutputs.count)self.nextSourceHealth=0;
            if(self.boundedSystemPacing){
                self.presentationActivity=[self beginPresentationActivity];
                [self startAudioPumpWithLimit:limit];
            }
            self.deadline=limit>0?self.now+limit:INFINITY;
            if(self.systemOutputs.count){[self setState:@"waiting"];[self report:@"Connected. Waiting for audio from this Mac."];return;}
            [self setState:@"sharing"];
            [self diagnose:@"capture_start_returned" fields:@{@"limit_seconds":@(limit),@"manual_session":@(limit==0)}];
            [self report:limit>0?[NSString stringWithFormat:@"Sharing %@. Stop anytime; diagnostic limit %.0f seconds.",self.source.name,limit]:[NSString stringWithFormat:@"Sharing %@. Stop anytime.",self.source.name]];
        });
    }});
}
- (void)suspendCapture {[self.capture suspendReadingKeepingTap];}
- (void)stopRenderers {if(self.backend.isRunning)[self.backend terminate];}
- (BOOL)releaseCapture {
    NSDictionary *snapshot=[self.capture diagnosticSnapshot];
    dispatch_async(dispatch_get_main_queue(),^{[self diagnose:@"capture_release" fields:snapshot];});
    NSError *error=nil;BOOL ok=!self.capture || [self.capture stopWithError:&error];
    if(ok)self.capture=nil; // Any dealloc fallback stays on the HAL owner, never main.
    return ok;
}
- (void)stop {[self stopWithReason:@"user_stop"];}
- (void)stopForApplicationExit {[self stopWithReason:@"app_exit"];}
- (void)stopWithReason:(NSString *)reason {
    if(!NSThread.isMainThread){dispatch_async(dispatch_get_main_queue(),^{[self stopWithReason:reason];});return;}
    if(self.phase==Idle || self.phase==Stopping)return;
    [self stopAudioPump];
    self.firstStopCode=reason;self.firstStopSnapshot=[self stopSnapshot];
    [self diagnose:@"first_stop" fields:self.firstStopSnapshot];[self persistStopEvidence];
    [self releasePresentationActivity];self.phase=Stopping;self.cancelRequested=YES;self.leaseDeadline=self.now+MBRestoreTimeout;
    [self setState:@"stopping"];
    [self.capture cancelPendingStart];[self suspendCapture];
    [self diagnose:@"stop_requested" fields:@{@"reason_code":self.firstStopCode}];
    if(self.pending.length)memset(self.pending.mutableBytes,0,self.pending.length);[self.pending setLength:0];
    [self stopRenderers];
    if(!self.renderersLaunched)self.cleanupVerified=YES;
    [self report:self.captureStartInProgress?@"Cancel requested. Audio setup is still pending; waiting for the audio service before cleanup.":(self.systemOutputs.count?@"Stopping selected outputs before restoring direct Mac audio.":@"Stopping both buffered players before restoring the selected app.")];
    if(self.cleanupVerified)[self finish:YES];
}
- (void)renderersStopped:(BOOL)verified generation:(NSUInteger)generation {
    if(!NSThread.isMainThread){dispatch_async(dispatch_get_main_queue(),^{[self renderersStopped:verified generation:generation];});return;}
    if(generation!=self.generation || self.phase==Idle)return;
    if(self.phase!=Stopping)[self stopWithReason:@"backend_completed"];
    self.cleanupVerified=verified;
    if(verified || (!self.backendReady && [self.firstBackendCode isEqual:@"auth_required"]))[self finish:verified];
    else [self report:@"Playback cleanup is unconfirmed; waiting for its safety lease."];
}
- (void)finish:(BOOL)verified {
    if(self.captureStartInProgress || self.releaseInProgress || self.cleanupFailed)return;
    self.releaseInProgress=YES;
    [self report:@"Audio cleanup pending. Waiting for the audio service; a new session cannot start yet."];
    NSUInteger generation=self.generation;
    dispatch_async(self.captureQueue,^{@autoreleasepool{
        BOOL restored=[self releaseCapture];
        dispatch_async(dispatch_get_main_queue(),^{
            if(generation!=self.generation)return;
            self.releaseInProgress=NO;
            if(!restored){
                self.cleanupFailed=YES;[self.timer invalidate];self.timer=nil;[self persistStopEvidence];
                [self report:@"Audio cleanup failed or remains incomplete. Sharing is disabled; source restoration was not verified."];
                return;
            }
            [self.timer invalidate];self.timer=nil;
            self.output.fileHandleForReading.readabilityHandler=nil;[self.input.fileHandleForWriting closeFile];
            if(self.pending.length)memset(self.pending.mutableBytes,0,self.pending.length);[self.pending setLength:0];
            self.phase=Idle;self.source=nil;[self persistStopEvidence];
            self.systemSource=nil;self.backendReady=NO;
            NSString *result=!self.renderersLaunched?@"Preparation canceled. No capture or playback was started.":
                (verified?(self.systemOutputs.count?@"Sharing stopped. Selected outputs exited before direct Mac audio was restored.":@"Sharing stopped. Both buffered players exited before the app's direct output was restored."):@"Local capture released. Receiver cleanup or source restoration was not fully verified.");
            [self report:self.failureReason.length?[NSString stringWithFormat:@"%@ %@",self.failureReason,result]:result];
            [self setState:self.failureReason.length?@"failed":@"idle"];
        });
    }});
}
- (void)tick {
    if(self.phase==Idle)return;
    self.maxTickGap=MAX(self.maxTickGap,self.now-self.lastTick);self.lastTick=self.now;
    if(self.phase==Stopping){if(self.cleanupVerified || self.now>=self.leaseDeadline)[self finish:self.cleanupVerified];return;}
    if(self.now>=self.deadline){if(self.phase==Preparing)self.failureReason=@"Speaker preparation timed out.";[self stopWithReason:self.phase==Preparing?@"prepare_deadline":@"diagnostic_duration_limit"];return;}
    if(self.pumpTimer){self.mainHeartbeat=NSProcessInfo.processInfo.systemUptime;return;} // The pump is now the only ring/pipe consumer.
    BOOL system=self.systemOutputs.count>0;
    [self audioTurnForSystem:system bounded:self.boundedSystemPacing sharing:self.phase==Sharing && !self.captureStartInProgress limit:self.maximumSharingDuration];
}
// Transfer sole ownership of pending bytes, pipe writes, frame counters and the
// SPSC consumer at activation. Keep all HAL lifecycle calls on captureQueue.
// No queue used below waits on the main thread.
- (void)startAudioPumpWithLimit:(NSTimeInterval)limit {
    self.pumpFailed=NO;self.mainHeartbeat=NSProcessInfo.processInfo.systemUptime;
    self.pumpQueue=dispatch_queue_create("macbee.system.audio-pump",
        dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_USER_INTERACTIVE,0));
    self.pumpTimer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,self.pumpQueue);
    dispatch_source_set_timer(self.pumpTimer,DISPATCH_TIME_NOW,10*NSEC_PER_MSEC,NSEC_PER_MSEC);
    __weak MBApplicationSession *weak=self;
    dispatch_source_set_event_handler(self.pumpTimer,^{@autoreleasepool{
        MBApplicationSession *session=weak;
        if(!session || session.pumpFailed)return;
        NSTimeInterval now=NSProcessInfo.processInfo.systemUptime;
        if(session.lastPumpTurn)session.maxPumpGap=MAX(session.maxPumpGap,now-session.lastPumpTurn);
        session.lastPumpTurn=now;
        // Normal UI hiccups must not perturb PCM. A genuinely unresponsive
        // control owner must not silently renew a session forever either.
        if(now-session.mainHeartbeat>=2.0){
            [session audioFailure:@"native_owner_unresponsive" message:@"The sharing controls stopped responding."];return;
        }
        [session audioTurnForSystem:YES bounded:YES sharing:YES limit:limit];
    }});
    dispatch_resume(self.pumpTimer);
}
- (void)stopAudioPump {
    if(!self.pumpTimer)return;
    dispatch_source_cancel(self.pumpTimer);
    // A turn contains only a bounded ring read and a nonblocking pipe write.
    // Drain any in-flight consumer before snapshot, mutable state or fd teardown.
    dispatch_sync(self.pumpQueue,^{});
    self.pumpTimer=nil;self.pumpQueue=nil;
}
- (void)onAudioOwnerMain:(void (^)(void))block {
    if(NSThread.isMainThread){block();return;}
    NSUInteger generation=self.generation;
    dispatch_async(dispatch_get_main_queue(),^{if(self.generation==generation && self.phase==Sharing)block();});
}
- (void)audioDiagnostic:(NSString *)stage fields:(NSDictionary *)fields {
    [self onAudioOwnerMain:^{[self diagnose:stage fields:fields];}];
}
- (void)audioReportClipping {
    [self onAudioOwnerMain:^{[self report:@"Full-scale saturation detected in the shared audio; no gain was added."];}];
}
- (void)audioFailure:(NSString *)reason message:(NSString *)message {
    self.pumpFailed=YES;
    [self onAudioOwnerMain:^{self.failureReason=message;[self stopWithReason:reason];}];
}
- (void)audioTurnForSystem:(BOOL)system bounded:(BOOL)bounded sharing:(BOOL)sharing limit:(NSTimeInterval)limit {

    if(bounded && self.capture.hasFault && [[self.capture diagnosticSnapshot][@"fault_reason"] intValue]==7){
        [self audioFailure:@"capture_timestamp_invalid" message:@"CoreAudio did not provide a valid input capture time."];return;
    }
    if(system && self.backend.isRunning && self.now>=self.nextSourceHealth){
        if(sharing && !self.capture.systemHealthValid){[self audioFailure:@"capture_health_failed" message:@"System audio health could not be verified."];return;}
        uint8_t control[8]={'M','B','H','2',(sharing?2:1),0,0,0};
        if(!self.pending.length)self.pendingSince=self.now;
        [self.pending appendBytes:control length:8];self.nextSourceHealth=self.now+.5;self.lastHealthQueued=self.now;
    }
    if(!system && !sharing)return;
    // HAL/source revalidation lives on the serial capture watchdog, not the UI.
    if(self.capture.hasFault){[self audioFailure:@"capture_fault" message:@"The source input stopped unexpectedly."];return;}
    // Drain and immediately flush split HAL callbacks within a bounded budget
    // so >=21 us drift-compensation jitter across 128/256-frame callbacks never
    // caps throughput below 48 kHz.
    BOOL supportsQueued=[self.capture respondsToSelector:@selector(queuedFrames)];
    float samples[8192];uint8_t packet[16+4096*4];
    int reads=0;size_t totalFrames=0;
    for(int step=0;step<17;step++){
        if(self.pending.length){
            if(!self.input)return;
            ssize_t n=write(self.input.fileHandleForWriting.fileDescriptor,self.pending.bytes,self.pending.length);
            if(n>0){
                self.lastPipeWrite=self.now;
                if(!self.writeNoted){self.writeNoted=YES;[self audioDiagnostic:@"first_pipe_write" fields:@{@"bytes":@(n)}];}
                [self.pending replaceBytesInRange:NSMakeRange(0,(NSUInteger)n) withBytes:NULL length:0];
            }
            else if(n<0 && errno!=EAGAIN && errno!=EINTR){self.lastPipeErrno=errno;[self audioFailure:@"native_pipe_error" message:@"The audio transport input pipe failed."];return;}
            if(self.pending.length && self.now-self.pendingSince>(bounded?.680:.250)){[self audioFailure:@"native_backpressure" message:@"Audio backpressure exceeded its bound."];return;}
            if(self.pending.length)return;
        }
        if(!sharing)return;
        if(reads>0){
            BOOL hasMore=bounded && reads<16 && totalFrames<4096 && (totalFrames<480 || (supportsQueued && [self.capture queuedFrames]>0));
            if(!hasMore)return;
        }
        if(!self.callbackNoted){NSDictionary *snapshot=[self.capture diagnosticSnapshot];if([snapshot[@"callbacks"] unsignedLongLongValue]){self.callbackNoted=YES;[self audioDiagnostic:@"first_callback_observed" fields:snapshot];}}
        uint64_t ns=0;
        size_t frames=bounded?[self.capture readStereoFrames:samples capacity:4096 captureTime:&ns]:[self.capture readStereoFrames:samples capacity:4096];
        if(!frames)return;
        self.lastPCMRead=self.now;
        if(bounded && !ns){[self audioFailure:@"capture_timestamp_invalid" message:@"The first captured frame has no input time."];return;}
        size_t header=bounded?16:8;memcpy(packet,bounded?"MBP4":"MBP2",4);
        if(bounded)for(int i=0;i<8;i++)packet[8+i]=(uint8_t)(ns>>(8*i));
        for(int i=0;i<4;i++)packet[4+i]=(uint8_t)((uint32_t)frames>>(8*i));
        size_t clipped=0;size_t n=mb_application_pcm16(samples,frames,packet+header,sizeof(packet)-header,&clipped);memset(samples,0,sizeof(samples));

        if(!n || (limit>0 && self.frames+frames>(uint64_t)limit*48000)){[self audioFailure:@"source_format_or_limit" message:@"Unsupported or oversized source input."];return;}
        if(!self.packetNoted){self.packetNoted=YES;[self audioDiagnostic:@"first_pcm_packet" fields:@{@"frames":@(frames)}];}
        if(!self.nonzeroNoted){BOOL nonzero=NO;for(size_t i=header;i<n+header;i++)if(packet[i]){nonzero=YES;break;}if(nonzero){self.nonzeroNoted=YES;[self audioDiagnostic:@"first_nonzero_pcm_packet" fields:@{@"frames":@(frames)}];}}
        self.frames+=frames;totalFrames+=frames;reads++;
        [self.pending appendBytes:packet length:n+header];memset(packet,0,sizeof(packet));self.pendingSince=self.now;
        if(clipped && !self.clippingNoted){self.clippingNoted=YES;[self audioReportClipping];}
    }
}
- (void)dealloc {
    if(_pumpTimer)dispatch_source_cancel(_pumpTimer);
    [self releasePresentationActivity];
    // The pump temporarily owns a strong reference for each turn. If it is
    // the last owner, leave invalidation of the main run-loop timer to main.
    NSTimer *timer=_timer;
    if(timer){
        if(NSThread.isMainThread)[timer invalidate];
        else dispatch_async(dispatch_get_main_queue(),^{[timer invalidate];});
    }
    self.output.fileHandleForReading.readabilityHandler=nil;
    [self.input.fileHandleForWriting closeFile];
    // A disappearing UI owner must not make ARC run HAL teardown on main.
    MBControlledCapture *capture=_capture;
    [capture cancelPendingStart];
    if(capture)dispatch_async(_captureQueue,^{[capture stopWithError:nil];});
}

@end
