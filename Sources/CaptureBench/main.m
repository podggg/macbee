#import <Cocoa/Cocoa.h>
#import "../ControlledCapture/ControlledCapture.h"
#include <math.h>

@interface CaptureBench : NSObject <NSApplicationDelegate,NSWindowDelegate>
@property NSWindow *window;
@property NSTextField *status;
@property NSTextView *log;
@property NSButton *start;
@property NSButton *stop;
@property NSTask *helper;
@property NSPipe *input;
@property NSPipe *output;
@property MBControlledCapture *capture;
@property NSTimer *timer;
@property BOOL ready;
@property BOOL armed;
@property BOOL finishing;
@property BOOL finishOK;
@property BOOL quitting;
@property NSUInteger generation;
@property uint64_t frames;
@property uint64_t nonzero;
@property uint64_t quiet;
@property uint64_t pulses;
@property float peak;
@property NSTimeInterval deadline;
@end
@implementation CaptureBench
- (void)applicationDidFinishLaunching:(NSNotification *)note {
    (void)note;
    self.window=[[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,500,390)
      styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable backing:NSBackingStoreBuffered defer:NO];
    self.window.title=@"Macbee Capture Test";self.window.delegate=self;self.window.releasedWhenClosed=NO;
    NSTextField *title=[NSTextField labelWithString:@"Controlled source capture"];
    title.font=[NSFont boldSystemFontOfSize:21];
    NSTextField *detail=[NSTextField wrappingLabelWithString:@"This test reads only its own generated helper audio. No microphone, existing music, calls, recording files, or network transmission. The helper is silent until the tap is ready and is muted while captured."];
    detail.font=[NSFont systemFontOfSize:12];detail.textColor=NSColor.secondaryLabelColor;
    self.status=[NSTextField wrappingLabelWithString:@"Not started. System audio permission has not been requested."];
    self.start=[NSButton buttonWithTitle:@"Run Controlled Test" target:self action:@selector(run:)];
    self.start.enabled=[NSProcessInfo.processInfo.arguments containsObject:@"--allow-controlled-test"];
    self.stop=[NSButton buttonWithTitle:@"Stop" target:self action:@selector(stop:)];self.stop.enabled=NO;
    NSStackView *buttons=[NSStackView stackViewWithViews:@[self.start,self.stop]];buttons.spacing=12;
    NSScrollView *scroll=[NSScrollView new];scroll.hasVerticalScroller=YES;scroll.borderType=NSBezelBorder;
    self.log=[[NSTextView alloc] initWithFrame:NSMakeRect(0,0,452,150)];self.log.editable=NO;
    self.log.font=[NSFont monospacedSystemFontOfSize:11 weight:NSFontWeightRegular];scroll.documentView=self.log;
    NSStackView *stack=[NSStackView stackViewWithViews:@[title,detail,self.status,buttons,scroll]];
    stack.orientation=NSUserInterfaceLayoutOrientationVertical;stack.alignment=NSLayoutAttributeLeading;stack.spacing=15;
    stack.translatesAutoresizingMaskIntoConstraints=NO;[self.window.contentView addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:self.window.contentView.leadingAnchor constant:24],
      [stack.trailingAnchor constraintEqualToAnchor:self.window.contentView.trailingAnchor constant:-24],
      [stack.topAnchor constraintEqualToAnchor:self.window.contentView.topAnchor constant:24],
      [stack.bottomAnchor constraintEqualToAnchor:self.window.contentView.bottomAnchor constant:-24],
      [scroll.widthAnchor constraintEqualToAnchor:stack.widthAnchor],[scroll.heightAnchor constraintGreaterThanOrEqualToConstant:130],
      [detail.widthAnchor constraintEqualToAnchor:stack.widthAnchor],[self.status.widthAnchor constraintEqualToAnchor:stack.widthAnchor]]];
    NSMenu *main=[NSMenu new];NSMenuItem *app=[NSMenuItem new];NSMenu *menu=[NSMenu new];
    [menu addItemWithTitle:@"Quit Capture Test" action:@selector(terminate:) keyEquivalent:@"q"];app.submenu=menu;[main addItem:app];NSApp.mainMenu=main;
    [self.window center];[self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];
}
- (void)append:(NSString *)text {self.log.string=[self.log.string stringByAppendingString:text];}
- (void)run:(id)sender {
    (void)sender;if(self.helper)return;
    NSUInteger generation=++self.generation;
    self.ready=NO;self.armed=NO;self.finishing=NO;self.frames=0;self.nonzero=0;self.quiet=48000;self.pulses=0;self.peak=0;
    self.log.string=@"";self.start.enabled=NO;self.stop.enabled=YES;
    self.status.stringValue=@"Starting zero-only helper. No other app audio is selected.";
    NSTask *helper=[NSTask new];self.helper=helper;
    helper.executableURL=[[NSBundle mainBundle].executableURL.URLByDeletingLastPathComponent URLByAppendingPathComponent:@"macbee-controlled-source"];
    helper.arguments=@[@"--controlled-source"];
    self.input=[NSPipe pipe];self.output=[NSPipe pipe];helper.standardInput=self.input;
    helper.standardOutput=self.output;helper.standardError=self.output;
    __weak CaptureBench *weak=self;
    self.output.fileHandleForReading.readabilityHandler=^(NSFileHandle *handle){
        NSData *data=handle.availableData;if(!data.length){handle.readabilityHandler=nil;return;}
        NSString *text=[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]?:@"";
        dispatch_async(dispatch_get_main_queue(),^{
            CaptureBench *s=weak;if(!s || s.generation!=generation)return;[s append:text];
            if(!s.ready && !s.finishing && [text containsString:@"source_ready"]){s.ready=YES;[s beginCapture];}
        });
    };
    helper.terminationHandler=^(NSTask *ended){
        dispatch_async(dispatch_get_main_queue(),^{
            CaptureBench *s=weak;if(!s || s.generation!=generation)return;
            if(!s.finishing)[s finish:s.armed && s.pulses==6 && ended.terminationStatus==0];
            s.helper=nil;s.stop.enabled=NO;s.start.enabled=YES;
            [s.input.fileHandleForWriting closeFile];s.input=nil;
            [s append:[NSString stringWithFormat:@"helper_exit=%d\n",ended.terminationStatus]];
            if(ended.terminationStatus!=0)s.finishOK=NO;
            s.status.stringValue=s.finishOK?@"Controlled capture passed. No audio recording was saved.":@"Test stopped or incomplete. See details; no ordinary app audio was selected.";
            if(s.quitting)[NSApp replyToApplicationShouldTerminate:YES];
        });
    };
    NSError *error=nil;
    if(![helper launchAndReturnError:&error]){
        self.helper=nil;self.start.enabled=YES;self.stop.enabled=NO;
        self.output.fileHandleForReading.readabilityHandler=nil;[self.input.fileHandleForWriting closeFile];
        self.status.stringValue=error.localizedDescription;return;
    }
    self.deadline=NSProcessInfo.processInfo.systemUptime+15;
    self.timer=[NSTimer scheduledTimerWithTimeInterval:.02 repeats:YES block:^(NSTimer *timer){
        CaptureBench *s=weak;if(!s){[timer invalidate];return;}[s consume];
    }];
}
- (void)beginCapture {
    self.capture=[MBControlledCapture new];NSError *error=nil;
    self.status.stringValue=@"Requesting the controlled helper tap. macOS may ask for System Audio Recording permission.";
    if(![self.capture startForOwnedHelper:self.helper error:&error]){
        [self append:[error.localizedDescription stringByAppendingString:@"\n"]];[self finish:NO];return;
    }
    self.status.stringValue=@"Waiting for the helper tap. The original helper is muted while read.";
}
- (void)consume {
    if(self.finishing)return;
    if(NSProcessInfo.processInfo.systemUptime>=self.deadline){[self finish:NO];return;}
    if(!self.capture)return;
    float samples[4096*2];size_t count=[self.capture readStereoFrames:samples capacity:4096];
    if(count && !self.armed){
        @try {[self.input.fileHandleForWriting writeData:[@"ARM\n" dataUsingEncoding:NSUTF8StringEncoding]];}
        @catch(NSException *exception){[self append:@"Controlled helper disconnected before arming.\n"];[self finish:NO];return;}
        self.armed=YES;
        self.status.stringValue=@"Reading six synthetic pulses in memory. The helper output is muted while tapped.";
    }
    for(size_t i=0;i<count;i++){
        float left=samples[i*2],right=samples[i*2+1];float peak=fmaxf(fabsf(left),fabsf(right));
        if(!isfinite(peak) || peak>.0051f || fabsf(left-right)>.0001f){[self append:@"Unexpected input amplitude or channel data; stopping.\n"];[self finish:NO];return;}
        if(peak>self.peak)self.peak=peak;
        if(peak>1e-7f){if(self.quiet>=2400)self.pulses++;self.quiet=0;self.nonzero++;}else self.quiet++;
    }
    self.frames+=count;
}
- (void)finish:(BOOL)success {
    if(self.finishing)return;self.finishing=YES;self.finishOK=success && !self.capture.hasFault;
    [self.timer invalidate];self.timer=nil;
    NSError *error=nil;if(self.capture && ![self.capture stopWithError:&error]){self.finishOK=NO;[self append:[error.localizedDescription stringByAppendingString:@"\n"]];}
    self.capture=nil;
    [self append:[NSString stringWithFormat:@"frames=%llu pulses=%llu nonzero_frames=%llu peak=%.7f\n",
      (unsigned long long)self.frames,(unsigned long long)self.pulses,(unsigned long long)self.nonzero,self.peak]];
    self.status.stringValue=@"Stopping the owned helper and releasing the private tap…";
    if(self.helper.isRunning)[self.helper terminate];
}
- (void)stop:(id)sender {(void)sender;[self finish:NO];}
- (BOOL)windowShouldClose:(NSWindow *)window {(void)window;if(self.helper){[self finish:NO];return NO;}return YES;}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    (void)sender;if(self.helper){self.quitting=YES;[self finish:NO];return NSTerminateLater;}return NSTerminateNow;
}
@end
int main(void){@autoreleasepool{NSApplication *app=[NSApplication sharedApplication];CaptureBench *delegate=[CaptureBench new];app.delegate=delegate;[app run];}return 0;}
