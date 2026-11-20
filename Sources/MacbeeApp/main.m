#import <Cocoa/Cocoa.h>
#ifdef MACBEE_APPLICATION_UI
#import "../ControlledCapture/ApplicationSession.h"
#ifdef MACBEE_APPLICATION_PREVIEW
#import "ApplicationPreview.h"
#endif
#endif
#ifdef MACBEE_CONTROLLED_RELAY
#import "../ControlledCapture/ControlledRelay.h"
#endif

@interface MacbeeApp : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property NSWindow *window;
@property NSWindow *diagnostics;
@property NSStatusItem *item;
@property NSTextField *state;
@property NSTextView *log;
@property NSString *diagnosticBacklog;
@property NSButton *silentButton;
@property NSButton *toneButton;
@property NSButton *stopButton;
@property NSTask *task;
@property NSPipe *pipe;
@property NSPipe *inputPipe;
@property BOOL stopping;
@property BOOL quitting;
@property BOOL preview;
@property NSWindow *closingWindow;
@property NSUInteger generation;
@property NSTextField *privacy;
#ifdef MACBEE_APPLICATION_UI
@property NSPopUpButton *sourcePicker;
@property NSButton *sharingButton;
@property NSButton *refreshSourcesButton;
@property NSTextField *sharingState;
@property MBApplicationSession *applicationSession;
@property NSArray<MBApplicationSource *> *applicationSources;
@property BOOL sharingStopping;
@property BOOL userCandidate;
@property BOOL manualSession;
#endif
#ifdef MACBEE_CONTROLLED_RELAY
@property MBControlledRelay *relay;
@property BOOL speakerTest;
#endif
@end

@implementation MacbeeApp
- (NSTextField *)label:(NSString *)text size:(CGFloat)size weight:(NSFontWeight)weight secondary:(BOOL)secondary {
    NSTextField *field=[NSTextField wrappingLabelWithString:text];
    field.font=[NSFont systemFontOfSize:size weight:weight];
    field.textColor=secondary?NSColor.secondaryLabelColor:NSColor.labelColor;
    return field;
}
- (NSStackView *)column:(NSArray<NSView *> *)views spacing:(CGFloat)spacing {
    NSStackView *stack=[NSStackView stackViewWithViews:views];
    stack.orientation=NSUserInterfaceLayoutOrientationVertical;
    stack.alignment=NSLayoutAttributeLeading;stack.spacing=spacing;
    return stack;
}
- (NSStackView *)row:(NSArray<NSView *> *)views spacing:(CGFloat)spacing {
    NSStackView *row=[NSStackView stackViewWithViews:views];
    row.orientation=NSUserInterfaceLayoutOrientationHorizontal;
    row.alignment=NSLayoutAttributeCenterY;row.spacing=spacing;return row;
}
- (NSView *)spacer {
    NSView *v=[NSView new];[v setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];return v;
}
- (NSImageView *)symbol:(NSString *)name description:(NSString *)description {
    NSImageView *image=[NSImageView imageViewWithImage:[NSImage imageWithSystemSymbolName:name accessibilityDescription:description]];
    image.contentTintColor=NSColor.secondaryLabelColor;
    [image.widthAnchor constraintEqualToConstant:28].active=YES;
    [image.heightAnchor constraintEqualToConstant:28].active=YES;
    image.imageScaling=NSImageScaleProportionallyUpOrDown;return image;
}
- (NSButton *)button:(NSString *)title action:(SEL)action {
    NSButton *button=[NSButton buttonWithTitle:title target:self action:action];
    button.accessibilityLabel=title;return button;
}
- (NSBox *)card:(NSView *)content {
    NSBox *box=[NSBox new];box.boxType=NSBoxCustom;box.titlePosition=NSNoTitle;
    box.borderWidth=.5;box.cornerRadius=12;
    box.fillColor=NSColor.controlBackgroundColor;box.borderColor=NSColor.separatorColor;
    box.contentViewMargins=NSMakeSize(0,0);
    content.translatesAutoresizingMaskIntoConstraints=NO;[box.contentView addSubview:content];
    [NSLayoutConstraint activateConstraints:@[
        [content.leadingAnchor constraintEqualToAnchor:box.contentView.leadingAnchor constant:18],
        [content.trailingAnchor constraintEqualToAnchor:box.contentView.trailingAnchor constant:-18],
        [content.topAnchor constraintEqualToAnchor:box.contentView.topAnchor constant:18],
        [content.bottomAnchor constraintEqualToAnchor:box.contentView.bottomAnchor constant:-18]]];
    return box;
}
- (void)place:(NSStackView *)stack in:(NSWindow *)window padding:(CGFloat)padding {
    stack.translatesAutoresizingMaskIntoConstraints=NO;[window.contentView addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:window.contentView.leadingAnchor constant:padding],
        [stack.trailingAnchor constraintEqualToAnchor:window.contentView.trailingAnchor constant:-padding],
        [stack.topAnchor constraintEqualToAnchor:window.contentView.topAnchor constant:padding],
        [stack.bottomAnchor constraintLessThanOrEqualToAnchor:window.contentView.bottomAnchor constant:-padding]]];
}
- (NSWindow *)makeWindow:(NSString *)title size:(NSSize)size minimum:(NSSize)minimum {
    NSWindow *window=[[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,size.width,size.height)
      styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable
      backing:NSBackingStoreBuffered defer:NO];
    window.title=title;window.minSize=minimum;window.delegate=self;window.releasedWhenClosed=NO;
    window.backgroundColor=NSColor.windowBackgroundColor;[window center];return window;
}
- (void)applicationDidFinishLaunching:(NSNotification *)note {
    (void)note;
    self.preview=[[[NSBundle mainBundle] objectForInfoDictionaryKey:@"MacbeeUIOnly"] boolValue] || getenv("MACBEE_UI_MOCK");
#ifdef MACBEE_APPLICATION_UI
    self.userCandidate=[[[NSBundle mainBundle] objectForInfoDictionaryKey:@"MacbeeUserLaunchableCandidate"] boolValue];
    self.manualSession=[[[NSBundle mainBundle] objectForInfoDictionaryKey:@"MacbeeManualSessionEnabled"] boolValue];
    // User-facing launch must not inherit the optional synthetic timing mode.
    if(self.userCandidate)unsetenv("MACBEE_OWNED_MARKER_TIMING");
    // A legacy environment toggle must never label a real capture build mock
    // or bypass its explicit selected-source launch gate.
#ifdef MACBEE_APPLICATION_PREVIEW
    self.preview=YES;
#else
    self.preview=NO;
#endif
#endif
#ifdef MACBEE_CONTROLLED_RELAY
    self.speakerTest=[[[NSBundle mainBundle] objectForInfoDictionaryKey:@"MacbeeControlledSpeakerTest"] boolValue];
#endif
    self.item=[[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.item.button.title=self.preview?@"Macbee Preview":@"Macbee";
    NSMenu *menu=[NSMenu new];
    NSMenuItem *about=[[NSMenuItem alloc] initWithTitle:@"About Macbee" action:@selector(showAbout:) keyEquivalent:@""];
    about.target=self;[menu addItem:about];
    NSMenuItem *updates=[[NSMenuItem alloc] initWithTitle:@"Check for Updates…" action:@selector(checkForUpdates:) keyEquivalent:@""];
    updates.target=self;[menu addItem:updates];[menu addItem:NSMenuItem.separatorItem];
    for(NSArray *entry in @[@[@"Show Macbee",NSStringFromSelector(@selector(show:)),@""],
                            @[@"Diagnostics…",NSStringFromSelector(@selector(showDiagnostics:)),@"d"]]) {
        NSMenuItem *item=[[NSMenuItem alloc] initWithTitle:entry[0] action:NSSelectorFromString(entry[1]) keyEquivalent:entry[2]];
        item.target=self;[menu addItem:item];
    }
    NSMenuItem *close=[[NSMenuItem alloc] initWithTitle:@"Close Window" action:@selector(performClose:) keyEquivalent:@"w"];
    [menu addItem:close];[menu addItem:NSMenuItem.separatorItem];
    NSMenuItem *quit=[[NSMenuItem alloc] initWithTitle:@"Quit Macbee" action:@selector(terminate:) keyEquivalent:@"q"];
    quit.target=NSApp;[menu addItem:quit];self.item.menu=menu;
    NSMenu *main=[NSMenu new];NSMenuItem *appItem=[NSMenuItem new];appItem.submenu=[menu copy];
    [main addItem:appItem];NSApp.mainMenu=main;

    CGFloat previewWidth=540;
    if(self.preview && getenv("MACBEE_PREVIEW_WIDTH"))previewWidth=MAX(460,MIN(540,atof(getenv("MACBEE_PREVIEW_WIDTH"))));
    self.window=[self makeWindow:@"Macbee" size:NSMakeSize(previewWidth,510) minimum:NSMakeSize(460,510)];
    NSTextField *tag=[self label:@"DEVELOPMENT PREVIEW" size:10 weight:NSFontWeightSemibold secondary:YES];
    NSButton *diagnostics=[self button:@"Diagnostics" action:@selector(showDiagnostics:)];
    diagnostics.bezelStyle=NSBezelStyleRoundRect;diagnostics.controlSize=NSControlSizeSmall;
    diagnostics.toolTip=@"Open controlled tests and technical logs";
    NSStackView *top=[self row:@[tag,[self spacer],diagnostics] spacing:12];
    NSTextField *heading=[self label:@"Sound, together." size:28 weight:NSFontWeightSemibold secondary:NO];
    NSTextField *intro=[self label:@"One audio source. Your Macs, in sync." size:14 weight:NSFontWeightRegular secondary:YES];
    NSStackView *hero=[self column:@[heading,intro] spacing:7];

    NSStackView *sourceText=[self column:@[
        [self label:@"This Mac" size:14 weight:NSFontWeightSemibold secondary:NO],
        [self label:@"System audio is not connected yet" size:12 weight:NSFontWeightRegular secondary:YES]] spacing:4];
#ifdef MACBEE_APPLICATION_UI
#ifdef MACBEE_APPLICATION_PREVIEW
    self.applicationSources=MBPreviewSources();
#else
    self.applicationSources=[MBApplicationSource availableSourcesWithError:nil];
#endif
    self.sourcePicker=[[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
    [self.sourcePicker addItemWithTitle:@"Choose an example app…"];
    for(MBApplicationSource *app in self.applicationSources)[self.sourcePicker addItemWithTitle:app.name];
    self.sourcePicker.target=self;self.sourcePicker.action=@selector(selectApplication:);
#ifdef MACBEE_APPLICATION_PREVIEW
    self.sourcePicker.accessibilityLabel=@"Example audio source";
#else
    [self.sourcePicker itemAtIndex:0].title=@"Choose a playing app…";
    self.sourcePicker.accessibilityLabel=@"Selected audio source";
#endif
    NSString *sourceNote=@"One explicitly selected app. No system-wide mix.";
#ifdef MACBEE_APPLICATION_PREVIEW
    sourceNote=@"Offline examples. No real app is read.";
#endif
    self.refreshSourcesButton=[self button:@"Refresh" action:@selector(refreshSources:)];
    self.refreshSourcesButton.controlSize=NSControlSizeSmall;
    self.refreshSourcesButton.toolTip=@"Find currently playing apps. Refresh clears the selection.";
    sourceText=[self column:@[[self row:@[self.sourcePicker,self.refreshSourcesButton] spacing:8],
        [self label:sourceNote size:12 weight:NSFontWeightRegular secondary:YES]] spacing:5];
#endif
    NSBox *source=[self card:[self row:@[[self symbol:@"laptopcomputer" description:@"Audio source"],sourceText,[self spacer]] spacing:14]];
    NSTextField *sourceTitle=[self label:@"SOURCE" size:10 weight:NSFontWeightSemibold secondary:YES];
    NSStackView *sourceSection=[self column:@[sourceTitle,source] spacing:9];

    NSTextField *destTitle=[self label:@"DESTINATIONS" size:10 weight:NSFontWeightSemibold secondary:YES];
    NSButton *add=[self button:@"Add Mac…" action:nil];add.enabled=NO;add.controlSize=NSControlSizeSmall;
    add.toolTip=@"Pairing is not available in this development build";
    NSStackView *destinationHeader=[self row:@[destTitle,[self spacer],add] spacing:8];
    NSStackView *emptyText=[self column:@[
        [self label:@"No Macs paired" size:14 weight:NSFontWeightSemibold secondary:NO],
        [self label:@"Your paired Macs will appear here.\nDevice pairing is still in development." size:12 weight:NSFontWeightRegular secondary:YES]] spacing:5];
#ifdef MACBEE_APPLICATION_UI
    emptyText=[self column:@[[self label:@"This Mac + configured receiver" size:14 weight:NSFontWeightSemibold secondary:NO],
        [self label:@"Connection is checked on Start.\nExisting SSH test route, not device pairing." size:12 weight:NSFontWeightRegular secondary:YES]] spacing:5];
#endif
#ifdef MACBEE_APPLICATION_PREVIEW
    emptyText=[self column:@[[self label:@"Two example Macs" size:14 weight:NSFontWeightSemibold secondary:NO],
        [self label:@"Simulated destinations, not connected devices.\nReal device pairing is not available yet." size:12 weight:NSFontWeightRegular secondary:YES]] spacing:5];
#endif
    NSBox *destinations=[self card:[self row:@[[self symbol:@"laptopcomputer.and.arrow.down" description:@"Destination Macs"],emptyText,[self spacer]] spacing:14]];
    NSStackView *destinationSection=[self column:@[destinationHeader,destinations] spacing:9];

    NSButton *start=[self button:@"Start Sharing" action:nil];start.enabled=NO;
    start.bezelStyle=NSBezelStyleRounded;start.controlSize=NSControlSizeLarge;
    start.font=[NSFont systemFontOfSize:14 weight:NSFontWeightSemibold];
    start.toolTip=@"Available after system audio and destination pairing are connected";
    [start.widthAnchor constraintEqualToConstant:166].active=YES;
    NSTextField *unavailable=[self label:@"Sharing is not available yet." size:12 weight:NSFontWeightRegular secondary:YES];
#ifdef MACBEE_APPLICATION_UI
    tag.stringValue=@"SELECTED-APP DEVELOPMENT TEST";
    if(self.userCandidate)tag.stringValue=self.manualSession?@"EARLY ACCESS · MANUAL START / STOP":@"EARLY ACCESS · BOUNDED SESSIONS";
    start.title=@"Start Sharing";start.accessibilityLabel=start.title;start.target=self;start.action=@selector(toggleApplication:);
    start.toolTip=@"Share only the selected app; Stop anytime";
    self.applicationSession=[MBApplicationSession new];
#ifdef MACBEE_APPLICATION_PREVIEW
    tag.stringValue=@"OFFLINE INTERACTION PREVIEW";
    start.title=@"Preview Start";start.accessibilityLabel=start.title;start.target=self;start.action=@selector(toggleApplication:);
    start.toolTip=@"Exercise Start and Stop without audio, capture, or networking";
    self.applicationSession=[MBApplicationPreviewSession new];
#endif
    self.sharingButton=start;self.sharingState=unavailable;unavailable.stringValue=self.preview?@"Choose an example app.":
        (self.userCandidate?@"Choose a playing app, then Start Sharing.":@"Choose a playing app. Exact source approval is required.");
    __weak MacbeeApp *weakApp=self;
    self.applicationSession.diagnosticHandler=^(NSDictionary *event){
        // Lifecycle/counter metadata only. Never sample contents or source identities.
        NSData *json=[NSJSONSerialization dataWithJSONObject:event options:NSJSONWritingSortedKeys error:nil];
        NSString *line=json?[[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding]:@"{}";
        fprintf(stderr,"MACBEE_DIAGNOSTIC %s\n",line.UTF8String);fflush(stderr);
        void (^append)(void)=^{MacbeeApp *app=weakApp;if(app)[app append:[line stringByAppendingString:@"\n"]];};
        if(NSThread.isMainThread)append();else dispatch_async(dispatch_get_main_queue(),append);
    };
    self.applicationSession.statusHandler=^(NSString *message){
        MacbeeApp *app=weakApp;if(!app)return;
        app.sharingState.stringValue=app.preview?[@"Simulation: " stringByAppendingString:message]:message;
        [app append:[message stringByAppendingString:@"\n"]];
        if(!app.preview)app.privacy.stringValue=app.applicationSession.running?@"Selected app only. Stop cancels preparation or sharing. No microphone or saved audio.":@"No audio is being captured or shared.";
        [app refreshApplicationControls];
        if(!app.applicationSession.running){
            if(app.closingWindow){[app.closingWindow close];app.closingWindow=nil;}
            if(app.quitting)[NSApp replyToApplicationShouldTerminate:YES];
        }
    };
#endif
    NSStackView *actions=[self row:@[unavailable,[self spacer],start] spacing:12];
    NSTextField *privacy=[self label:@"No audio is being captured or shared." size:11 weight:NSFontWeightRegular secondary:YES];
    self.privacy=privacy;
    NSStackView *root=[self column:@[top,hero,sourceSection,destinationSection,actions,privacy] spacing:22];
    [self place:root in:self.window padding:28];
    for(NSView *view in @[top,hero,sourceSection,destinationSection,actions,privacy])
        [view.widthAnchor constraintEqualToAnchor:root.widthAnchor].active=YES;
    [source.widthAnchor constraintEqualToAnchor:sourceSection.widthAnchor].active=YES;
    [destinationHeader.widthAnchor constraintEqualToAnchor:destinationSection.widthAnchor].active=YES;
    [destinations.widthAnchor constraintEqualToAnchor:destinationSection.widthAnchor].active=YES;
    self.window.initialFirstResponder=diagnostics;
#ifdef MACBEE_APPLICATION_UI
    self.window.initialFirstResponder=self.sourcePicker;
    if(self.preview)self.privacy.stringValue=@"Offline preview. No audio, app inspection, or network activity.";
    [self.sourcePicker setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    [unavailable.widthAnchor constraintLessThanOrEqualToConstant:235].active=YES;
#endif
    [self show:nil];
    const char *snapshot=getenv("MACBEE_SNAPSHOT_PATH");
    if(self.preview && snapshot) {
        NSString *path=[NSString stringWithUTF8String:snapshot];
        dispatch_async(dispatch_get_main_queue(),^{
            NSView *view=self.window.contentView.superview;[view layoutSubtreeIfNeeded];[view displayIfNeeded];
            NSBitmapImageRep *rep=[view bitmapImageRepForCachingDisplayInRect:view.bounds];
            [view cacheDisplayInRect:view.bounds toBitmapImageRep:rep];
            [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:path atomically:YES];
        });
    }
}
#ifdef MACBEE_APPLICATION_UI
- (BOOL)selectedAppStartAllowed {
    BOOL candidate=self.userCandidate && [[[NSBundle mainBundle] objectForInfoDictionaryKey:@"MacbeeApplicationTestEnabled"] boolValue];
    return self.preview || candidate || [NSProcessInfo.processInfo.arguments containsObject:@"--allow-selected-app"];
}
- (void)refreshSources:(id)sender {
    (void)sender;if(self.applicationSession.running)return;
#ifdef MACBEE_APPLICATION_PREVIEW
    self.applicationSources=MBPreviewSources();
#else
    self.applicationSources=[MBApplicationSource availableSourcesWithError:nil];
#endif
    [self.sourcePicker removeAllItems];
    [self.sourcePicker addItemWithTitle:self.preview?@"Choose an example app…":@"Choose a playing app…"];
    for(MBApplicationSource *source in self.applicationSources)[self.sourcePicker addItemWithTitle:source.name];
    [self.sourcePicker selectItemAtIndex:0];[self selectApplication:nil];
}
- (void)refreshApplicationControls {
    BOOL running=self.applicationSession.running;if(!running)self.sharingStopping=NO;
    self.sourcePicker.enabled=!running;
    self.refreshSourcesButton.enabled=!running;
    self.sharingButton.title=self.preview?(running?@"Preview Stop":@"Preview Start"):(running?@"Stop Sharing":@"Start Sharing");
    self.sharingButton.accessibilityLabel=self.sharingButton.title;
    BOOL allowed=[self selectedAppStartAllowed];
    self.sharingButton.enabled=allowed && !self.sharingStopping && (running || self.sourcePicker.indexOfSelectedItem>0);
}
- (void)selectApplication:(id)sender {
    (void)sender;if(self.applicationSession.running)return;
    self.sharingState.stringValue=self.preview?(self.sourcePicker.indexOfSelectedItem>0?@"Ready for an offline preview.":@"Choose an example app."):
        (self.sourcePicker.indexOfSelectedItem>0?(self.userCandidate?(self.manualSession?@"All audio from this app. No scheduled session limit.":@"All audio from this app. Bounded session."):@"Selected app only. Bounded session; source approval required."):@"Choose a playing app.");
    [self refreshApplicationControls];
}
- (void)toggleApplication:(id)sender {
    (void)sender;if(self.applicationSession.running){self.sharingStopping=YES;[self.applicationSession stop];[self refreshApplicationControls];return;}
    if(![self selectedAppStartAllowed])return;
    NSInteger selected=self.sourcePicker.indexOfSelectedItem-1;
    if(selected<0 || selected>=(NSInteger)self.applicationSources.count)return;
    NSError *error=nil;
    if(![self.applicationSession startSource:self.applicationSources[(NSUInteger)selected] error:&error])
        self.sharingState.stringValue=error.localizedDescription?:@"The preview could not start.";
    [self refreshApplicationControls];
}
#endif
- (void)show:(id)sender {(void)sender;[self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];[self.window makeFirstResponder:self.window.initialFirstResponder];}
- (void)showAbout:(id)sender {
    (void)sender;NSBundle *bundle=NSBundle.mainBundle;
    NSString *version=[bundle objectForInfoDictionaryKey:@"MacbeeReleaseVersion"]?:[bundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"]?:@"Development";
    NSString *lifetime=[[bundle objectForInfoDictionaryKey:@"MacbeeManualSessionEnabled"] boolValue]?@"Manual Start / Stop; connection loss also stops sharing.":@"Finite test sessions.";
    [NSApp orderFrontStandardAboutPanelWithOptions:@{NSAboutPanelOptionApplicationName:@"Macbee",NSAboutPanelOptionApplicationVersion:version,
        NSAboutPanelOptionVersion:[bundle objectForInfoDictionaryKey:@"CFBundleVersion"]?:@"1",
        NSAboutPanelOptionCredits:[[NSAttributedString alloc] initWithString:[NSString stringWithFormat:@"Early access. One selected app, two configured Macs.\n%@\nNo microphone or saved audio. Updates are installed manually.",lifetime]]}];
}
- (void)checkForUpdates:(id)sender {
    (void)sender;[[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"https://github.com/podggg/macbee/releases"]];
}
- (void)showDiagnostics:(id)sender {
    (void)sender;
    if(!self.diagnostics) {
        self.diagnostics=[self makeWindow:@"Macbee Diagnostics" size:NSMakeSize(580,440) minimum:NSMakeSize(480,400)];
        NSTextField *heading=[self label:@"Controlled audio tests" size:20 weight:NSFontWeightSemibold secondary:NO];
        NSTextField *info=[self label:self.preview?@"UI preview only. These controls run a mock task, not audio or networking.":@"Synthetic source only, using the existing SSH test route.\nNo capture, recording, system volume, or output-device changes." size:12 weight:NSFontWeightRegular secondary:YES];
#ifdef MACBEE_CONTROLLED_RELAY
        info.stringValue=self.speakerTest?@"Owned synthetic helper only. Six soft tones play through both built-in speakers after buffer warmup. No microphone, ordinary app audio, recording files, or volume changes. Sender permission may be requested.":@"Owned synthetic helper only. Both Macs discard received audio without playing it. No microphone, ordinary apps, recording files, or volume changes. Sender permissions may be requested.";
#endif
        self.state=[self label:self.preview?@"Mock mode · no audio or network":@"Idle · no test running" size:12 weight:NSFontWeightSemibold secondary:NO];
        self.silentButton=[self button:@"Silent Check" action:@selector(startSilent:)];
        self.toneButton=[self button:@"Six-tone Test" action:@selector(startTone:)];
        self.stopButton=[self button:@"Stop" action:@selector(stop:)];self.stopButton.enabled=NO;
#ifdef MACBEE_APPLICATION_UI
        info.stringValue=self.preview?@"Offline interaction test only. No real app lookup, audio, or network.":@"One explicitly selected app through the configured SSH receiver. Stop anytime; the session has a finite limit. No microphone, recording files, or volume changes.";
        self.silentButton.hidden=YES;self.toneButton.hidden=YES;self.stopButton.hidden=YES;
#endif
        NSStackView *buttons=[self row:@[self.silentButton,self.toneButton,[self spacer],self.stopButton] spacing:10];
#ifdef MACBEE_CONTROLLED_RELAY
        self.silentButton.title=self.speakerTest?@"Controlled Speaker Check":@"Controlled Relay Check";self.silentButton.accessibilityLabel=self.silentButton.title;
        NSString *approvalFlag=self.speakerTest?@"--allow-controlled-speakers":@"--allow-controlled-relay";
        self.silentButton.enabled=!self.preview && [NSProcessInfo.processInfo.arguments containsObject:approvalFlag];
        self.toneButton.hidden=YES;self.toneButton.enabled=NO;
        self.state.stringValue=self.speakerTest?@"Not started · separate controlled-speaker approval required":@"Not started · separate controlled-relay approval required";
#endif
        NSTextField *limits=[self label:@"Speaker timing and video lip-sync are not measured." size:11 weight:NSFontWeightRegular secondary:YES];
        NSScrollView *scroll=[NSScrollView new];scroll.hasVerticalScroller=YES;scroll.borderType=NSBezelBorder;
        self.log=[[NSTextView alloc] initWithFrame:NSMakeRect(0,0,520,170)];self.log.editable=NO;
        self.log.font=[NSFont monospacedSystemFontOfSize:11 weight:NSFontWeightRegular];self.log.string=self.diagnosticBacklog.length?self.diagnosticBacklog:@"Test details will appear here.";
        self.log.accessibilityLabel=@"Diagnostic log";scroll.documentView=self.log;
        NSStackView *stack=[self column:@[heading,info,self.state,buttons,limits,scroll] spacing:15];
        [self place:stack in:self.diagnostics padding:24];
        for(NSView *view in @[info,buttons,limits,scroll])[view.widthAnchor constraintEqualToAnchor:stack.widthAnchor].active=YES;
        [scroll.heightAnchor constraintGreaterThanOrEqualToConstant:170].active=YES;
        [stack.bottomAnchor constraintEqualToAnchor:self.diagnostics.contentView.bottomAnchor constant:-24].active=YES;
        self.diagnostics.initialFirstResponder=self.silentButton;
    }
    [self.diagnostics makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];[self.diagnostics makeFirstResponder:self.diagnostics.initialFirstResponder];
}
- (void)startSilent:(id)sender {
    (void)sender;
#ifdef MACBEE_CONTROLLED_RELAY
    NSString *approvalFlag=self.speakerTest?@"--allow-controlled-speakers":@"--allow-controlled-relay";
    if(self.preview || ![NSProcessInfo.processInfo.arguments containsObject:approvalFlag] || self.relay.running)return;
    self.relay=self.relay?:[MBControlledRelay new];self.diagnosticBacklog=@"";self.log.string=@"";self.closingWindow=nil;self.stopping=NO;
    self.silentButton.enabled=NO;self.stopButton.enabled=YES;
    __weak MacbeeApp *weak=self;
    self.relay.statusHandler=^(NSString *message){
        MacbeeApp *s=weak;if(!s)return;s.state.stringValue=message;[s append:[message stringByAppendingString:@"\n"]];
        s.privacy.stringValue=s.relay.running?@"Controlled synthetic test active. Ordinary app sharing is unavailable.":@"No audio is being captured or shared.";
        if(!s.relay.running){
            s.stopButton.enabled=NO;s.silentButton.enabled=YES;
            if(s.closingWindow){[s.closingWindow close];s.closingWindow=nil;}
            if(s.quitting)[NSApp replyToApplicationShouldTerminate:YES];
        }
    };
    NSError *error=nil;if(![self.relay startWithError:&error]){
        self.state.stringValue=error.localizedDescription?:@"The controlled relay could not start.";
        self.stopButton.enabled=self.relay.running;self.silentButton.enabled=!self.relay.running;
    }
#else
    [self launchSilent:YES];
#endif
}
- (void)startTone:(id)sender {(void)sender;[self launchSilent:NO];}
- (void)append:(NSString *)text {
    if(!text.length)return;
    NSString *joined=[(self.diagnosticBacklog?:@"") stringByAppendingString:text];
    if(joined.length>64000)joined=[joined substringFromIndex:joined.length-64000];
    self.diagnosticBacklog=joined;self.log.string=joined;[self.log scrollRangeToVisible:NSMakeRange(joined.length,0)];
}
- (void)launchSilent:(BOOL)silent {
#ifdef MACBEE_CONTROLLED_RELAY
    (void)silent;return; // No legacy tone route; C2 cannot select C3's speaker path.
#endif
    if(self.task)return;
    NSString *root=[[NSBundle mainBundle] objectForInfoDictionaryKey:@"MacbeeDevelopmentRoot"];
#ifdef MACBEE_APPLICATION_UI
    (void)silent;(void)root;self.state.stringValue=@"Use the main window to select a source and start or stop.";return;
#endif
    NSString *script=[root stringByAppendingPathComponent:self.preview?@"tools/mock_ui_test.py":@"tools/run_coreaudio_probe.py"];
    if(![[NSFileManager defaultManager] fileExistsAtPath:script]){self.state.stringValue=@"Local test tools are unavailable. Nothing started.";return;}
    self.stopping=NO;self.closingWindow=nil;self.diagnosticBacklog=@"";self.log.string=@"";NSUInteger generation=++self.generation;
    self.state.stringValue=self.preview?@"Mock running · no audio or network":(silent?@"Preparing a silent check…":@"Preparing six low-level tones…");
    self.silentButton.enabled=NO;self.toneButton.enabled=NO;self.stopButton.enabled=YES;
    NSTask *task=[NSTask new];self.task=task;
    NSString *python=[[NSBundle mainBundle] objectForInfoDictionaryKey:@"MacbeePythonExecutable"];
    task.executableURL=[NSURL fileURLWithPath:python?:@"/usr/bin/python3"];
    task.arguments=self.preview?@[script]:(silent?@[script,@"--silent",@"--watch-parent"]:@[script,@"--watch-parent"]);
    self.inputPipe=[NSPipe pipe];task.standardInput=self.inputPipe;
    self.pipe=[NSPipe pipe];task.standardOutput=self.pipe;task.standardError=self.pipe;
    __weak MacbeeApp *weakSelf=self;
    self.pipe.fileHandleForReading.readabilityHandler=^(NSFileHandle *handle){
        NSData *data=handle.availableData;if(!data.length){handle.readabilityHandler=nil;return;}
        NSString *text=[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]?:@"";
        dispatch_async(dispatch_get_main_queue(),^{if(weakSelf.generation==generation)[weakSelf append:text];});
    };
    task.terminationHandler=^(NSTask *finished){
        dispatch_async(dispatch_get_main_queue(),^{
            MacbeeApp *s=weakSelf;if(!s)return;
            s.task=nil;[s.inputPipe.fileHandleForWriting closeFile];s.inputPipe=nil;
            s.stopButton.enabled=NO;s.silentButton.enabled=YES;s.toneButton.enabled=YES;
            s.state.stringValue=(s.stopping && finished.terminationStatus==0)?@"Stopped · cleanup complete":
              (finished.terminationStatus==0?@"Test complete · acoustic timing remains unmeasured":@"Test incomplete · see the diagnostic log");
            if(s.closingWindow){[s.closingWindow close];s.closingWindow=nil;}
            if(s.quitting)[NSApp replyToApplicationShouldTerminate:YES];
        });
    };
    NSError *error=nil;
    if(![task launchAndReturnError:&error]){
        self.pipe.fileHandleForReading.readabilityHandler=nil;[self.inputPipe.fileHandleForWriting closeFile];self.inputPipe=nil;self.task=nil;
        self.state.stringValue=@"The test could not start";[self append:error.localizedDescription];
        self.stopButton.enabled=NO;self.silentButton.enabled=YES;self.toneButton.enabled=YES;
    }
}
- (void)stop:(id)sender {
#ifdef MACBEE_CONTROLLED_RELAY
    (void)sender;self.stopping=YES;self.stopButton.enabled=NO;[self.relay stop];return;
#endif
    (void)sender;if(!self.task || self.stopping)return;
    self.stopping=YES;self.stopButton.enabled=NO;self.state.stringValue=@"Stopping and cleaning up…";[self.task terminate];
}
- (BOOL)windowShouldClose:(NSWindow *)window {
#ifdef MACBEE_APPLICATION_UI
    if(window==self.diagnostics)return YES; // closing logs is not Stop Sharing
    if(self.applicationSession.running){self.closingWindow=window;[self toggleApplication:nil];return NO;}
#endif
#ifdef MACBEE_CONTROLLED_RELAY
    if(self.relay.running){self.closingWindow=window;[self stop:nil];return NO;}
#endif
    if(self.task){self.closingWindow=window;[self stop:nil];return NO;}return YES;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
#ifdef MACBEE_APPLICATION_UI
    if(self.applicationSession.running){self.quitting=YES;[self toggleApplication:nil];return NSTerminateLater;}
#endif
#ifdef MACBEE_CONTROLLED_RELAY
    (void)sender;if(self.relay.running){self.quitting=YES;[self stop:nil];return NSTerminateLater;}return NSTerminateNow;
#endif
    (void)sender;if(self.task){self.quitting=YES;[self stop:nil];return NSTerminateLater;}return NSTerminateNow;
}
@end
int main(void){@autoreleasepool{NSApplication *app=[NSApplication sharedApplication];
    MacbeeApp *delegate=[MacbeeApp new];app.delegate=delegate;[app run];}return 0;}
