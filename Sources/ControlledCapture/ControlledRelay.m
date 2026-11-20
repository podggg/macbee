#import "ControlledRelay.h"
#import "ControlledCapture.h"
#include "../AudioPipeline/controlled_pcm.h"
#include <fcntl.h>
#include <errno.h>
#include <unistd.h>

@interface MBControlledRelay ()
@property NSTask *backend;
@property NSTask *helper;
@property NSPipe *backendInput;
@property NSPipe *helperInput;
@property NSPipe *backendOutput;
@property NSPipe *helperOutput;
@property NSMutableData *backendLines;
@property NSMutableData *helperLines;
@property NSMutableData *pending;
@property MBControlledCapture *capture;
@property NSTimer *timer;
@property BOOL armed;
@property BOOL cancelling;
@property BOOL ending;
@property BOOL endedPacket;
@property BOOL receivedComplete;
@property BOOL helperReady;
@property BOOL backendReady;
@property BOOL finalized;
@property BOOL sourceFinished;
@property BOOL speakerTest;
@property BOOL backendStarted;
@property NSUInteger generation;
@property uint64_t frames;
@property NSTimeInterval deadline;
@property NSTimeInterval pendingSince;
@end

@implementation MBControlledRelay
- (BOOL)running {return self.backend!=nil || self.helper!=nil || self.capture!=nil || self.timer!=nil;}
- (void)report:(NSString *)text {if(self.statusHandler)self.statusHandler(text);}
- (BOOL)startWithError:(NSError **)error {
    if(!NSThread.isMainThread || self.running)return NO;
    NSBundle *bundle=NSBundle.mainBundle;
    NSString *root=[bundle objectForInfoDictionaryKey:@"MacbeeDevelopmentRoot"];
    NSString *python=[bundle objectForInfoDictionaryKey:@"MacbeePythonExecutable"];
    NSString *usage=[bundle objectForInfoDictionaryKey:@"NSAudioCaptureUsageDescription"];
    if(!root.length || !python.length || !usage.length){
        if(error)*error=[NSError errorWithDomain:@"MacbeeControlledRelay" code:1 userInfo:@{NSLocalizedDescriptionKey:@"This build is not configured for the separately approved controlled relay test."}];return NO;
    }
    self.generation++;self.armed=NO;self.cancelling=NO;self.ending=NO;self.endedPacket=NO;
    self.receivedComplete=NO;self.helperReady=NO;self.backendReady=NO;self.finalized=NO;self.sourceFinished=NO;self.frames=0;
    self.speakerTest=[[bundle objectForInfoDictionaryKey:@"MacbeeControlledSpeakerTest"] boolValue];self.backendStarted=NO;
    self.pending=[NSMutableData data];self.backendLines=[NSMutableData data];self.helperLines=[NSMutableData data];
    self.backend=[NSTask new];self.backend.executableURL=[NSURL fileURLWithPath:python];
    self.backend.arguments=@[@"-u",[root stringByAppendingPathComponent:self.speakerTest?@"tools/run_speaker_relay.py":@"tools/run_capture_relay.py"],@"--controlled-pcm-stdin"];
    self.backendInput=[NSPipe pipe];self.backendOutput=[NSPipe pipe];
    self.backend.standardInput=self.backendInput;self.backend.standardOutput=self.backendOutput;
    // No raw samples are written to stdout/stderr. Python emits bounded JSON.
    self.backend.standardError=self.backendOutput;
    [self watch:self.backendOutput task:self.backend backend:YES];
    self.deadline=NSProcessInfo.processInfo.systemUptime+22;
    __weak MBControlledRelay *weak=self;
    self.timer=[NSTimer scheduledTimerWithTimeInterval:.01 repeats:YES block:^(NSTimer *timer){
        MBControlledRelay *s=weak;if(!s){[timer invalidate];return;}[s tick];
    }];
    // For C3 only, establish the owned zero-only helper before speaker
    // guardians take their baseline. Do not weaken their new-output-PID guard.
    if(self.speakerTest)[self launchHelper];else [self launchBackend];
    return !self.cancelling;
}
- (void)launchBackend {
    if(self.backendStarted || self.cancelling)return;
    NSError *error=nil;
    if(![self.backend launchAndReturnError:&error]){
        self.backendOutput.fileHandleForReading.readabilityHandler=nil;self.backend=nil;
        [self fail:error.localizedDescription];return;
    }
    self.backendStarted=YES;
    int fd=self.backendInput.fileHandleForWriting.fileDescriptor;
    if(fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK)==-1 || fcntl(fd,F_SETNOSIGPIPE,1)==-1){[self fail:@"Could not configure the bounded PCM pipe."];return;}
    [self report:self.speakerTest?@"Preparing two guarded speaker buffers. The owned helper still outputs only zeros.":@"Preparing two silent sinks. No capture has started."];
}
- (void)launchHelper {
    if(self.helper || self.cancelling)return;
    self.helper=[NSTask new];self.helper.executableURL=[NSBundle.mainBundle.executableURL.URLByDeletingLastPathComponent URLByAppendingPathComponent:@"macbee-controlled-source"];
    self.helper.arguments=@[@"--controlled-source"];
    self.helperInput=[NSPipe pipe];self.helperOutput=[NSPipe pipe];
    self.helper.standardInput=self.helperInput;self.helper.standardOutput=self.helperOutput;self.helper.standardError=self.helperOutput;
    [self watch:self.helperOutput task:self.helper backend:NO];NSError *error=nil;
    if(![self.helper launchAndReturnError:&error]){self.helper=nil;[self fail:error.localizedDescription];return;}
    [self report:self.speakerTest?@"Starting the owned zero-only helper before speaker safety checks.":@"Starting only the owned synthetic helper; both destinations discard audio."];
}
- (void)beginCapture {
    if(self.capture || self.cancelling || !self.helperReady || !self.backendReady)return;
    self.capture=[MBControlledCapture new];NSError *error=nil;
    if(![self.capture startForOwnedHelper:self.helper error:&error]){[self fail:error.localizedDescription];return;}
    [self report:@"Reading only the owned helper tap. No ordinary app or microphone is selected."];
}
- (void)watch:(NSPipe *)pipe task:(NSTask *)task backend:(BOOL)backend {
    NSUInteger generation=self.generation;__weak MBControlledRelay *weak=self;
    pipe.fileHandleForReading.readabilityHandler=^(NSFileHandle *handle){
        NSData *data=handle.availableData;if(!data.length){handle.readabilityHandler=nil;return;}
        dispatch_async(dispatch_get_main_queue(),^{
            MBControlledRelay *s=weak;if(!s || s.generation!=generation)return;
            NSMutableData *lines=backend?s.backendLines:s.helperLines;
            if(lines.length+data.length>32768){[s fail:@"Diagnostic output exceeded its bound."];return;}
            [lines appendData:data];
            while(lines.length){
                const uint8_t *bytes=lines.bytes;const uint8_t *nl=memchr(bytes,'\n',lines.length);
                if(!nl)break;NSUInteger count=(NSUInteger)(nl-bytes);
                NSData *line=[lines subdataWithRange:NSMakeRange(0,count)];[lines replaceBytesInRange:NSMakeRange(0,count+1) withBytes:NULL length:0];
                NSDictionary *item=[NSJSONSerialization JSONObjectWithData:line options:0 error:nil];
                if(![item isKindOfClass:NSDictionary.class])continue;
                if(backend)[s backendEvent:item];else [s helperEvent:item];
            }
        });
    };
    task.terminationHandler=^(NSTask *ended){
        dispatch_async(dispatch_get_main_queue(),^{
            MBControlledRelay *s=weak;if(!s || s.generation!=generation)return;
            if(backend){
                s.backend=nil;
                // Final JSON may still be queued by the pipe reader. Defer the
                // verdict briefly; a missing complete record is never success.
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
                    if(s.generation!=generation)return;
                    if(!s.cancelling && (ended.terminationStatus!=0 || !s.receivedComplete))[s fail:@"The relay ended without verified completion."];
                    [s settle];
                });
            } else {
                s.helper=nil;
                if(!s.cancelling && (!s.armed || ended.terminationStatus!=0))[s fail:@"The controlled source exited before completion."];
                s.sourceFinished=YES;
                [s tick];
            }
        });
    };
}
- (void)backendEvent:(NSDictionary *)event {
    if([event[@"event"] isEqual:@"ready"] && !self.backendReady && !self.cancelling && !self.ending){
        self.backendReady=YES;
        NSArray *sinks=event[@"sinks"];
        NSString *sink=self.speakerTest?@"CoreAudioPlayer -> guarded built-in speakers":@"FilePlayer -> /dev/null";
        if(![sinks isEqual:@[sink,sink]]){[self fail:@"The expected controlled destinations were not verified."];return;}
        if(self.speakerTest)[self beginCapture];else [self launchHelper];
    } else if([event[@"event"] isEqual:@"complete"]){
        self.receivedComplete=[event[@"cleanup_verified"] boolValue] && self.endedPacket && !self.cancelling;
        if(!self.receivedComplete)[self fail:@"Incomplete relay cleanup."];
    } else if([event[@"event"] isEqual:@"failed"])[self fail:event[@"error"]?:@"Relay failed."];
}
- (void)helperEvent:(NSDictionary *)event {
    if([event[@"event"] isEqual:@"source_ready"] && !self.helperReady && !self.cancelling){
        self.helperReady=YES;
        if(self.speakerTest)[self launchBackend];else [self beginCapture];
    }
}
- (void)tick {
    if(self.cancelling){[self settle];return;}
    if(NSProcessInfo.processInfo.systemUptime>self.deadline){[self fail:@"The controlled relay reached its deadline."];return;}
    if(self.capture.hasFault){[self fail:@"The source tap reported a fault."];return;}
    if(self.pending.length){
        ssize_t count=write(self.backendInput.fileHandleForWriting.fileDescriptor,self.pending.bytes,self.pending.length);
        if(count>0)[self.pending replaceBytesInRange:NSMakeRange(0,(NSUInteger)count) withBytes:NULL length:0];
        else if(count<0 && errno!=EAGAIN && errno!=EINTR){[self fail:@"The PCM consumer disconnected."];return;}
        if(self.pending.length && NSProcessInfo.processInfo.systemUptime-self.pendingSince>.25){[self fail:@"PCM backpressure exceeded 250 ms; no samples were silently dropped."];return;}
        if(self.pending.length)return;
    }
    if(self.endedPacket)return;
    float samples[4096*2];size_t frames=[self.capture readStereoFrames:samples capacity:4096];
    if(frames){
        if(!self.armed){
            @try{[self.helperInput.fileHandleForWriting writeData:[@"ARM\n" dataUsingEncoding:NSUTF8StringEncoding]];}
            @catch(NSException *exception){[self fail:@"The synthetic helper disconnected before arming."];return;}
            self.armed=YES;
        }
        if(self.frames+frames>480000){[self fail:@"Ten-second PCM bound reached."];return;}
        uint8_t packet[MB_CONTROLLED_PACKET_BYTES];size_t length=mb_controlled_packet(samples,frames,packet,sizeof(packet));
        memset(samples,0,sizeof(samples));
        if(!length){[self fail:@"Unexpected synthetic amplitude, channel layout or sample."];return;}
        [self.pending appendBytes:packet length:length];memset(packet,0,sizeof(packet));
        self.frames+=frames;self.pendingSince=NSProcessInfo.processInfo.systemUptime;return;
    }
    if(self.sourceFinished && !self.ending){
        self.ending=YES;NSError *error=nil;
        if(![self.capture stopWithError:&error]){[self fail:error.localizedDescription];return;}self.capture=nil;
        uint8_t end[8];mb_controlled_packet(NULL,0,end,sizeof(end));[self.pending appendBytes:end length:sizeof(end)];
        self.endedPacket=YES;self.pendingSince=NSProcessInfo.processInfo.systemUptime;
        [self report:self.speakerTest?@"Source stopped. Draining guarded speaker buffers and verifying cleanup.":@"Source stopped. Draining the two silent destinations and verifying cleanup."];
    }
}
- (void)fail:(NSString *)message {[self report:message];[self stop];}
- (void)stop {
    self.cancelling=YES;if(self.pending.length)memset(self.pending.mutableBytes,0,self.pending.length);[self.pending setLength:0];
    // Stop the owned source BEFORE releasing its muted tap, preventing an
    // intentionally canceled synthetic tone from escaping via the old path.
    if(self.helper.isRunning)[self.helper terminate];
    if(self.backend.isRunning)[self.backend terminate];
    else if(!self.backendStarted)self.backend=nil;
    [self settle];
}
- (void)settle {
    if(self.finalized)return;
    if(self.helper)return;
    if(self.capture){NSError *error=nil;if(![self.capture stopWithError:&error]){self.cancelling=YES;[self report:error.localizedDescription];}self.capture=nil;}
    if(self.backend)return;
    self.finalized=YES;
    [self.timer invalidate];self.timer=nil;
    self.backendOutput.fileHandleForReading.readabilityHandler=nil;self.helperOutput.fileHandleForReading.readabilityHandler=nil;
    [self.backendInput.fileHandleForWriting closeFile];[self.helperInput.fileHandleForWriting closeFile];
    [self.pending setLength:0];
    NSString *success=self.speakerTest?@"Controlled speaker relay passed. Acoustic synchronization was not measured.":@"Controlled relay passed. Both sinks were silent; speaker synchronization was not measured.";
    [self report:(!self.cancelling && self.receivedComplete)?success:@"Controlled relay stopped. No ordinary app audio was selected."];
}
@end
