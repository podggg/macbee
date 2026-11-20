#import <Cocoa/Cocoa.h>
#import "../ControlledCapture/ApplicationSession.h"
#import "AddMacSheet.h"
#import "../SSHLogin/SSHControlMaster.h"

@interface MBOutputDocument : NSView @end
@implementation MBOutputDocument
- (BOOL)isFlipped{return YES;}
@end

@interface MBOutputRow : NSObject
@property NSString *identifier,*name,*availability;
@property NSButton *selection,*check;
@property NSTextField *badge;
@end
@implementation MBOutputRow @end

@interface MBSystemApp : NSObject <NSApplicationDelegate,NSWindowDelegate>
@property NSWindow *window;
@property MBApplicationSession *session;
@property NSButton *start,*add;
@property NSTextField *status,*hint,*selectionSummary;
@property NSStackView *outputList;
@property NSMutableArray<MBOutputRow *> *rows;
@property BOOL closeRequested,quitRequested,setupBusy;
@property MBSSHControlMasterPool *sshMasters;
@property MBAddMacSheet *activeAddSheet;
@property NSArray<NSString *> *pendingOutputs;
@property NSMutableSet<NSString *> *reauthedInStartAttempt;
@end
static NSColor *ink(void){return [NSColor colorWithSRGBRed:.12 green:.18 blue:.18 alpha:1];}
static NSColor *muted(void){return [NSColor colorWithSRGBRed:.34 green:.39 blue:.39 alpha:1];}
static NSTextField *label(NSString *text,CGFloat size,NSFontWeight weight){
 NSTextField *field=[NSTextField wrappingLabelWithString:text];field.font=[NSFont systemFontOfSize:size weight:weight];field.textColor=muted();return field;
}
static NSStackView *stack(NSArray *items,BOOL vertical,CGFloat gap){
 NSStackView *view=[NSStackView stackViewWithViews:items];view.spacing=gap;
 view.orientation=vertical?NSUserInterfaceLayoutOrientationVertical:NSUserInterfaceLayoutOrientationHorizontal;
 view.alignment=vertical?NSLayoutAttributeLeading:NSLayoutAttributeCenterY;return view;
}
static NSView *flex(void){NSView *v=[NSView new];[v setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];return v;}
static NSView *rule(void){NSBox *v=[NSBox new];v.boxType=NSBoxSeparator;[v.heightAnchor constraintEqualToConstant:1].active=YES;return v;}
@implementation MBSystemApp
- (MBSSHControlMasterPool *)controlMasters {
 if(!_sshMasters)_sshMasters=[MBSSHControlMasterPool new];
 return _sshMasters;
}
- (MBSSHControlMaster *)newControlMaster {return [MBSSHControlMaster new];}
- (MBAddMacSheet *)newAddMacSheetWithRunner:(MBAddMacSetupRunner)runner {
 return [[MBAddMacSheet alloc] initWithSetupRunner:runner];
}
- (NSArray<NSString *> *)selectedIDs {
 NSMutableArray *ids=[NSMutableArray array];for(MBOutputRow *row in self.rows)if(row.selection.state==NSControlStateValueOn)[ids addObject:row.identifier];return ids;
}
- (void)wireSessionHandlers {
 __weak MBSystemApp *weak=self;
 self.session.statusHandler=^(NSString *message){MBSystemApp *app=weak;app.hint.stringValue=message;[app refresh:nil];};
 self.session.stateHandler=^(NSString *state){(void)state;MBSystemApp *app=weak;if(!app)return;[app refresh:nil];
  if(!app.session.running){
   NSString *authReceiver=app.session.authRequiredReceiverID;
   if(authReceiver.length && !app.quitRequested && !app.closeRequested && app.pendingOutputs.count &&
      ![app.reauthedInStartAttempt containsObject:authReceiver]){
    [app handleStartAuthRequiredForReceiver:authReceiver];
    return;
   }
   app.pendingOutputs=nil;
   if(app.quitRequested){[app.controlMasters closeAllSynchronously:YES];[NSApp replyToApplicationShouldTerminate:YES];}
   else [app.controlMasters closeAllSynchronously:NO];
   if(app.closeRequested)[app.window close];
  }};
 self.session.outputStateHandler=^(NSString *identifier,NSString *state){MBSystemApp *app=weak;for(MBOutputRow *row in app.rows)if([row.identifier isEqual:identifier])row.availability=state;[app refresh:nil];};
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
 (void)notification;self.session=[MBApplicationSession new];[self wireSessionHandlers];
 [self buildWindow];[self makeMenu];[self.window center];[self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];[self loadCatalog];
}
- (void)buildWindow {
 self.rows=[NSMutableArray array];
 self.window=[[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,440,520) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];
 self.window.title=@"Macbee";self.window.delegate=self;self.window.contentMinSize=NSMakeSize(380,460);
 self.window.appearance=[NSAppearance appearanceNamed:NSAppearanceNameAqua];
 self.window.backgroundColor=[NSColor colorWithSRGBRed:.985 green:.977 blue:.949 alpha:1];
 NSImage *icon=[[NSImage alloc] initWithContentsOfFile:[NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"Macbee.icns"]];
 NSImageView *bee=[NSImageView imageViewWithImage:icon?:[NSImage new]];bee.imageScaling=NSImageScaleProportionallyUpOrDown;
 [bee.widthAnchor constraintEqualToConstant:38].active=YES;[bee.heightAnchor constraintEqualToConstant:38].active=YES;bee.accessibilityLabel=@"Macbee";
 NSTextField *title=label(@"Macbee",25,NSFontWeightSemibold);title.textColor=ink();
 NSView *header=stack(@[bee,stack(@[title,label(@"One sound. Your chosen Macs.",12,NSFontWeightRegular)],YES,2)],NO,12);
 NSTextField *privacyNote=label(@"Shares all Mac audio, including notifications and calls. No microphone or recordings.",12,NSFontWeightRegular);
 self.add=[NSButton buttonWithTitle:@"Add Mac…" target:self action:@selector(addMac:)];self.add.bezelStyle=NSBezelStyleRounded;self.add.controlSize=NSControlSizeSmall;self.add.accessibilityHelp=@"Discover nearby Macs, SSH aliases, or enter an SSH address to add a Mac.";
 NSView *outputsHeader=stack(@[label(@"OUTPUTS",10,NSFontWeightBold),flex(),self.add],NO,8);
 self.outputList=stack(@[],YES,0);self.outputList.translatesAutoresizingMaskIntoConstraints=NO;
 NSScrollView *scroll=[NSScrollView new];scroll.hasVerticalScroller=YES;scroll.autohidesScrollers=YES;scroll.drawsBackground=NO;scroll.borderType=NSNoBorder;
 NSView *document=[MBOutputDocument new];document.translatesAutoresizingMaskIntoConstraints=NO;scroll.documentView=document;[document addSubview:self.outputList];
 [NSLayoutConstraint activateConstraints:@[[document.widthAnchor constraintEqualToAnchor:scroll.contentView.widthAnchor],
  [self.outputList.leadingAnchor constraintEqualToAnchor:document.leadingAnchor],[self.outputList.trailingAnchor constraintEqualToAnchor:document.trailingAnchor],
  [self.outputList.topAnchor constraintEqualToAnchor:document.topAnchor],[self.outputList.bottomAnchor constraintEqualToAnchor:document.bottomAnchor],
  [scroll.heightAnchor constraintGreaterThanOrEqualToConstant:96]]];
 self.selectionSummary=label(@"1 output selected",11,NSFontWeightRegular);
 self.status=label(@"Ready",18,NSFontWeightSemibold);self.status.textColor=ink();self.status.accessibilityLabel=@"Sharing status";
 self.hint=label(@"Choose outputs, then Start. Connections are verified on Start.",12,NSFontWeightRegular);
 self.start=[NSButton buttonWithTitle:@"Start" target:self action:@selector(toggle:)];self.start.bezelStyle=NSBezelStyleRounded;self.start.controlSize=NSControlSizeLarge;self.start.keyEquivalent=@"\r";[self.start.widthAnchor constraintEqualToConstant:88].active=YES;
 NSView *actions=stack(@[self.status,flex(),self.start],NO,12);NSStackView *footer=stack(@[actions,self.hint],YES,7);
 [actions.widthAnchor constraintEqualToAnchor:footer.widthAnchor].active=YES;[self.hint.widthAnchor constraintEqualToAnchor:footer.widthAnchor].active=YES;
 NSArray *items=@[header,privacyNote,rule(),outputsHeader,scroll,self.selectionSummary,rule(),footer];NSStackView *layout=stack(items,YES,14);
 NSView *view=self.window.contentView;layout.translatesAutoresizingMaskIntoConstraints=NO;[view addSubview:layout];
 [NSLayoutConstraint activateConstraints:@[[layout.leadingAnchor constraintEqualToAnchor:view.leadingAnchor constant:24],
  [layout.trailingAnchor constraintEqualToAnchor:view.trailingAnchor constant:-24],[layout.topAnchor constraintEqualToAnchor:view.topAnchor constant:20],
  [layout.bottomAnchor constraintEqualToAnchor:view.bottomAnchor constant:-20]]];
 for(NSView *item in items)[item.widthAnchor constraintEqualToAnchor:layout.widthAnchor].active=YES;
 [self addOutput:@{@"id":@"local",@"name":@"This Mac",@"selected":@YES,@"availability":@"available"}];[self refresh:nil];
}
- (void)addOutput:(NSDictionary *)item {
 NSString *identifier=item[@"id"],*name=item[@"name"];
 if(![identifier isKindOfClass:NSString.class] || ![name isKindOfClass:NSString.class] || name.length>60)return;
 for(MBOutputRow *existing in self.rows)if([existing.identifier isEqual:identifier])return;
 MBOutputRow *row=[MBOutputRow new];row.identifier=identifier;row.name=name;row.availability=item[@"availability"]?:@"not_checked";
 row.selection=[NSButton checkboxWithTitle:name target:self action:@selector(refresh:)];row.selection.font=[NSFont systemFontOfSize:14 weight:NSFontWeightMedium];row.selection.cell.lineBreakMode=NSLineBreakByTruncatingTail;[row.selection setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
 row.selection.state=[item[@"selected"] boolValue]?NSControlStateValueOn:NSControlStateValueOff;row.selection.accessibilityHelp=@"Select this output for the next explicit Start.";
 row.badge=label(@"Configured",11,NSFontWeightRegular);[row.badge setContentCompressionResistancePriority:1000 forOrientation:NSLayoutConstraintOrientationHorizontal];
 row.check=[NSButton buttonWithTitle:@"Check" target:self action:@selector(checkMac:)];row.check.bezelStyle=NSBezelStyleInline;row.check.identifier=identifier;row.check.hidden=[identifier isEqual:@"local"];
 NSView *subtitle=stack(@[label(@"Default output",11,NSFontWeightRegular),flex(),row.check],NO,4);
 NSStackView *entry=stack(@[stack(@[row.selection,flex(),row.badge],NO,8),subtitle],YES,3);entry.edgeInsets=NSEdgeInsetsMake(9,0,9,0);
 for(NSView *v in entry.arrangedSubviews)[v.widthAnchor constraintEqualToAnchor:entry.widthAnchor].active=YES;
 [self.outputList addArrangedSubview:entry];[entry.widthAnchor constraintEqualToAnchor:self.outputList.widthAnchor].active=YES;
 NSView *line=rule();[self.outputList addArrangedSubview:line];[line.widthAnchor constraintEqualToAnchor:self.outputList.widthAnchor].active=YES;[self.rows addObject:row];
}
- (void)refresh:(id)sender {
 (void)sender;BOOL running=self.session.running;NSString *state=self.session.sharingState?:@"idle";
 NSDictionary *names=@{@"idle":@"Ready",@"preparing":@"Connecting",@"waiting":@"Waiting for audio",@"playing":@"Playing",@"degraded":@"Recovering audio",@"sharing":@"Sharing",@"stopping":@"Stopping",@"failed":@"Stopped with an issue"};
 self.status.stringValue=names[state]?:@"Ready";NSUInteger selected=0,offline=0;
 for(MBOutputRow *row in self.rows){
  BOOL chosen=row.selection.state==NSControlStateValueOn;if(chosen)selected++;
  row.selection.enabled=!running && !self.setupBusy;row.check.enabled=!running && !self.setupBusy;
  if(self.session.outputsConnected && chosen && ![row.availability isEqual:@"offline"])row.availability=@"connected";
  else if(!running && [row.availability isEqual:@"connected"])row.availability=@"available";
  if(chosen && [row.availability isEqual:@"offline"])offline++;
  row.badge.stringValue=@{@"available":@"Available",@"connected":@"Connected",@"offline":@"Offline",@"checking":@"Checking…",@"not_checked":@"Configured"}[row.availability]?:@"Configured";
  row.badge.toolTip=@"Availability reflects the last check. Start verifies the connection again.";
 }
 self.selectionSummary.stringValue=[NSString stringWithFormat:@"%lu output%@ selected%@",(unsigned long)selected,selected==1?@"":@"s",offline?[NSString stringWithFormat:@" · %lu offline",(unsigned long)offline]:@""];
 self.start.title=running?@"Stop":@"Start";self.start.accessibilityLabel=self.start.title;
 self.start.enabled=![state isEqual:@"stopping"] && !self.setupBusy && (running || selected>0);
 self.add.enabled=!running && !self.setupBusy;
 if([state isEqual:@"waiting"])self.hint.stringValue=@"Connected. Play audio in any app when you are ready.";
 if([state isEqual:@"degraded"])self.hint.stringValue=@"Audio timing was interrupted. Sharing is still connected and recovering.";
 if([state isEqual:@"playing"])self.hint.stringValue=offline?@"An output disconnected. The remaining outputs continue.":@"Sharing with your selected outputs.";
}
- (void)beginStartWithOutputs:(NSArray<NSString *> *)outputs saveSelection:(BOOL)saveSelection {
 self.pendingOutputs=outputs;
 __weak MBSystemApp *weak=self;
 void (^launchSession)(void)=^{
  MBSystemApp *app=weak;if(!app)return;
  for(MBOutputRow *row in app.rows)if(row.selection.state==NSControlStateValueOn)row.availability=@"not_checked";
  app.session.sshControlPaths=[app.controlMasters controlPathsForReceiverIDs:outputs];
  NSError *error=nil;BOOL accepted=[app.session startSystemAudioWithOutputs:outputs error:&error];
  if(!accepted){app.pendingOutputs=nil;app.hint.stringValue=error.localizedDescription?:@"Choose at least one output.";}
  [app refresh:nil];
 };
 if(!saveSelection){launchSession();return;}
 self.setupBusy=YES;[self refresh:nil];
 // Remember this explicit choice before starting. This command only writes the
 // local selection; it neither contacts receivers nor captures audio.
 [self performSetup:@[@"--select",[outputs componentsJoinedByString:@","]] completion:^(NSDictionary *result){
  MBSystemApp *app=weak;if(!app)return;app.setupBusy=NO;
  if(![result[@"ok"] boolValue]){app.pendingOutputs=nil;app.hint.stringValue=result[@"error"]?:@"The output selection could not be saved.";[app refresh:nil];return;}
  launchSession();
 }];
}
- (void)handleStartAuthRequiredForReceiver:(NSString *)receiverID {
 if(!self.reauthedInStartAttempt)self.reauthedInStartAttempt=[NSMutableSet set];
 [self.reauthedInStartAttempt addObject:receiverID];
 self.setupBusy=YES;self.hint.stringValue=@"SSH sign-in required for a selected Mac…";[self refresh:nil];
 __weak MBSystemApp *weak=self;
 [self performSetup:@[@"--receiver-host",receiverID] completion:^(NSDictionary *result){
  MBSystemApp *app=weak;if(!app)return;
  NSString *sshHost=[result[@"ok"] boolValue] && [result[@"result"] isKindOfClass:NSDictionary.class]?result[@"result"][@"ssh_host"]:nil;
  if(![sshHost isKindOfClass:NSString.class] || !sshHost.length){
   app.setupBusy=NO;app.pendingOutputs=nil;
   app.hint.stringValue=result[@"error"]?:@"Could not resolve receiver SSH destination.";[app refresh:nil];return;
  }
  MBSSHControlMaster *master=[app newControlMaster];
  [master openForDestination:sshHost window:app.window completion:^(BOOL ok,BOOL cancelled,NSError *error){
   MBSystemApp *current=weak;if(!current){[master closeSynchronously:NO];return;}
   if(!ok){
    current.setupBusy=NO;current.pendingOutputs=nil;
    current.hint.stringValue=cancelled?@"SSH sign-in cancelled. Sharing was not started.":(error.localizedDescription?:@"SSH sign-in failed.");
    [current refresh:nil];return;
   }
   [current.controlMasters adoptMaster:master forReceiverID:receiverID];
   current.setupBusy=NO;
   [current beginStartWithOutputs:current.pendingOutputs saveSelection:NO];
  }];
 }];
}
- (void)toggle:(id)sender {
 (void)sender;if(self.session.running){self.pendingOutputs=nil;[self.session stop];return;}if(self.setupBusy)return;
 NSArray *outputs=[self selectedIDs];if(!outputs.count)return;
 self.reauthedInStartAttempt=[NSMutableSet set];
 [self beginStartWithOutputs:outputs saveSelection:YES];
}
- (void)performSetup:(NSArray<NSString *> *)arguments completion:(void (^)(NSDictionary *))completion {
 // Triggered by explicit setup/check or the read-only catalog load. Never Start.
 NSTask *task=[NSTask new];task.executableURL=[NSURL fileURLWithPath:@"/usr/bin/python3"];
 task.arguments=[@[@"-B",@"-E",@"-s",[NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"tools/receiver_setup.py"]] arrayByAddingObjectsFromArray:arguments];
 NSMutableDictionary *environment=[NSProcessInfo.processInfo.environment mutableCopy];
 environment[@"MACBEE_RUNTIME_HOME"]=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Macbee"];
 environment[@"MACBEE_ASSET_ROOT"]=NSBundle.mainBundle.resourcePath;task.environment=environment;
 NSPipe *output=[NSPipe pipe];task.standardOutput=output;task.standardError=[NSFileHandle fileHandleWithNullDevice];
 dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{
  NSError *error=nil;NSDictionary *row=nil;
  if([task launchAndReturnError:&error]){
   NSData *bytes=[output.fileHandleForReading readDataToEndOfFile];[task waitUntilExit];
   if(bytes.length<=65536)row=[NSJSONSerialization JSONObjectWithData:bytes options:0 error:nil];
  }
  if(![row isKindOfClass:NSDictionary.class])row=@{@"ok":@NO,@"error":@"Receiver setup could not finish. Check the existing SSH connection and try again."};
  dispatch_async(dispatch_get_main_queue(),^{completion(row);});
 });
}
- (void)loadCatalog {
 self.setupBusy=YES;[self refresh:nil];__weak MBSystemApp *weak=self;
 [self performSetup:@[@"--list"] completion:^(NSDictionary *result){MBSystemApp *app=weak;if(!app)return;app.setupBusy=NO;
  if([result[@"ok"] boolValue]){for(NSDictionary *row in result[@"result"][@"outputs"])[app addOutput:row];
   app.rows.firstObject.selection.state=[result[@"result"][@"local_selected"] boolValue]?NSControlStateValueOn:NSControlStateValueOff;}
  else app.hint.stringValue=result[@"error"]?:@"The saved output list could not be read.";[app refresh:nil];}];
}
- (void)runCheckForIdentifier:(NSString *)identifier allowInteractiveAuth:(BOOL)allowAuth {
 MBSSHControlMaster *live=[self.controlMasters liveMasterForReceiverID:identifier];
 NSMutableArray<NSString *> *args=[@[@"--check",identifier] mutableCopy];
 if(live.controlPath.length)[args addObjectsFromArray:@[@"--control-path",live.controlPath]];
 __weak MBSystemApp *weak=self;
 [self performSetup:args completion:^(NSDictionary *result){
  MBSystemApp *app=weak;if(!app)return;
  if(![result[@"ok"] boolValue] && allowAuth && [result[@"code"] isEqual:@"auth_required"]){
   app.hint.stringValue=@"SSH sign-in required to check this Mac…";
   [app performSetup:@[@"--receiver-host",identifier] completion:^(NSDictionary *lookup){
    MBSystemApp *cur=weak;if(!cur)return;
    NSString *sshHost=[lookup[@"ok"] boolValue] && [lookup[@"result"] isKindOfClass:NSDictionary.class]?lookup[@"result"][@"ssh_host"]:nil;
    if(![sshHost isKindOfClass:NSString.class] || !sshHost.length){
     cur.setupBusy=NO;
     for(MBOutputRow *row in cur.rows)if([row.identifier isEqual:identifier])row.availability=@"offline";
     cur.hint.stringValue=lookup[@"error"]?:(result[@"error"]?:@"Receiver is unavailable.");[cur refresh:nil];return;
    }
    MBSSHControlMaster *master=[cur newControlMaster];
    [master openForDestination:sshHost window:cur.window completion:^(BOOL ok,BOOL cancelled,NSError *err){
     MBSystemApp *after=weak;if(!after){[master closeSynchronously:NO];return;}
     if(!ok){
      after.setupBusy=NO;
      for(MBOutputRow *row in after.rows)if([row.identifier isEqual:identifier])row.availability=@"offline";
      after.hint.stringValue=cancelled?@"SSH sign-in cancelled.":(err.localizedDescription?:@"SSH sign-in failed.");[after refresh:nil];return;
     }
     [after.controlMasters adoptMaster:master forReceiverID:identifier];
     [after runCheckForIdentifier:identifier allowInteractiveAuth:NO];
    }];
   }];
   return;
  }
  app.setupBusy=NO;
  for(MBOutputRow *row in app.rows)if([row.identifier isEqual:identifier])row.availability=[result[@"ok"] boolValue]?@"available":@"offline";
  app.hint.stringValue=[result[@"ok"] boolValue]?@"Receiver verified. Select it, then Start when ready.":(result[@"error"]?:@"Receiver is unavailable.");[app refresh:nil];
 }];
}
- (void)checkMac:(NSButton *)sender {
 if(self.session.running || self.setupBusy)return;self.setupBusy=YES;
 for(MBOutputRow *row in self.rows)if([row.identifier isEqual:sender.identifier])row.availability=@"checking";[self refresh:nil];
 [self runCheckForIdentifier:sender.identifier allowInteractiveAuth:YES];
}
- (void)addMac:(id)sender {
 (void)sender;if(self.session.running || self.setupBusy || self.activeAddSheet)return;
 self.setupBusy=YES;[self refresh:nil];
 __weak MBSystemApp *weak=self;
 MBAddMacSheet *sheet=[self newAddMacSheetWithRunner:^(NSArray<NSString *> *arguments,void (^completion)(NSDictionary *)){
  MBSystemApp *app=weak;if(!app){completion(@{@"ok":@NO,@"error":@"Window closed."});return;}
  [app performSetup:arguments completion:completion];
 }];
 self.activeAddSheet=sheet;
 [sheet presentOnWindow:self.window completion:^(NSDictionary *addedRow,MBSSHControlMaster *master,NSString *notice){
  MBSystemApp *app=weak;if(!app){[master closeSynchronously:NO];return;}
  app.activeAddSheet=nil;app.setupBusy=NO;
  if(addedRow){
   [app addOutput:addedRow];
   NSString *rid=[addedRow[@"id"] isKindOfClass:NSString.class]?addedRow[@"id"]:nil;
   if(master && rid.length)[app.controlMasters adoptMaster:master forReceiverID:rid];
   else [master closeSynchronously:NO];
   app.hint.stringValue=notice?:@"Mac added, not selected. Choose it explicitly before sharing.";
  }else [master closeSynchronously:NO];
  [app refresh:nil];
 }];
}
- (BOOL)windowShouldClose:(NSWindow *)sender {
 (void)sender;if(self.setupBusy || self.activeAddSheet){self.hint.stringValue=@"Receiver setup is still finishing. No audio has started.";return NO;}
 if(self.session.running){self.closeRequested=YES;[self.session stopForApplicationExit];return NO;}return YES;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
 (void)sender;if(self.setupBusy || self.activeAddSheet)return NSTerminateCancel;
 if(self.session.running){self.quitRequested=YES;[self.session stopForApplicationExit];return NSTerminateLater;}
 [self.controlMasters closeAllSynchronously:YES];
 return NSTerminateNow;
}
- (void)applicationWillTerminate:(NSNotification *)notification {
 (void)notification;
 [self.controlMasters closeAllSynchronously:YES];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {(void)sender;return YES;}
- (void)about:(id)sender {(void)sender;NSBundle *bundle=NSBundle.mainBundle;NSString *ver=[bundle objectForInfoDictionaryKey:@"MacbeeReleaseVersion"]?:@"1.2.6-dev";NSString *build=[bundle objectForInfoDictionaryKey:@"CFBundleVersion"]?:@"10";[NSApp orderFrontStandardAboutPanelWithOptions:@{NSAboutPanelOptionApplicationName:@"Macbee",NSAboutPanelOptionApplicationVersion:ver,NSAboutPanelOptionVersion:build,NSAboutPanelOptionCredits:[[NSAttributedString alloc] initWithString:@"Configured SSH outputs. Explicit Start and Stop. No microphone or saved audio."]}];}
- (void)updates:(id)sender {(void)sender;[[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"https://github.com/podggg/macbee/releases"]];}
- (void)makeMenu {
 NSMenu *bar=[NSMenu new];NSMenuItem *item=[NSMenuItem new];[bar addItem:item];NSMenu *menu=[NSMenu new];item.submenu=menu;
 NSMenuItem *about=[[NSMenuItem alloc] initWithTitle:@"About Macbee" action:@selector(about:) keyEquivalent:@""];about.target=self;[menu addItem:about];
 NSMenuItem *update=[[NSMenuItem alloc] initWithTitle:@"Check for Updates…" action:@selector(updates:) keyEquivalent:@""];update.target=self;[menu addItem:update];
 [menu addItem:NSMenuItem.separatorItem];[menu addItemWithTitle:@"Quit Macbee" action:@selector(terminate:) keyEquivalent:@"q"];NSApp.mainMenu=bar;
}
@end
#ifndef MACBEE_SYSTEM_UI_TEST
int main(void){@autoreleasepool{[NSApplication sharedApplication];[NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];MBSystemApp *app=[MBSystemApp new];NSApp.delegate=app;[NSApp run];}return 0;}
#endif
