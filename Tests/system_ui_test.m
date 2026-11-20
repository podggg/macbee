/* Real AppKit controls with an inert session. No HAL, backend, metadata or network. */
#define MACBEE_SYSTEM_UI_TEST 1
#import "../Sources/MacbeeApp/SystemMain.m"
#include <assert.h>
@interface MBApplicationSession ()
@property(nonatomic,readwrite) BOOL running,outputsConnected;
@property(nonatomic,readwrite,copy) NSString *sharingState;
@property(nonatomic,readwrite,copy,nullable) NSString *authRequiredReceiverID;
@property NSArray *chosen;
@property NSUInteger starts,stops;
@end
@implementation MBApplicationSession
- (BOOL)startSource:(MBApplicationSource *)source error:(NSError **)error{(void)source;(void)error;abort();}
- (BOOL)startSource:(MBApplicationSource *)source outputs:(NSArray *)outputs error:(NSError **)error{(void)source;return [self startSystemAudioWithOutputs:outputs error:error];}
- (BOOL)startSystemAudioWithOutputs:(NSArray *)outputs error:(NSError **)error{
 (void)error;assert(!self.running && outputs.count);self.chosen=outputs;self.starts++;self.running=YES;self.sharingState=@"preparing";return YES;
}
- (void)stop{self.stops++;self.sharingState=@"stopping";}
- (void)stopForApplicationExit{[self stop];}
@end
@implementation MBApplicationSource
+ (NSArray *)availableSourcesWithError:(NSError **)error{(void)error;abort();}
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error{(void)output;(void)error;abort();}
- (NSString *)name{return @"Synthetic Player";}
- (pid_t)pid{return 0;}
- (AudioObjectID)audioProcess{return 0;}
- (NSArray *)audioProcesses{return @[];}
- (NSString *)bundleID{return @"test.fixture";}
- (NSDate *)launchDate{return NSDate.distantPast;}
@end

@interface MBFakeControlMaster : MBSSHControlMaster
@property(nonatomic,copy) NSString *mockPath,*mockDestination;
@property(nonatomic) BOOL mockAlive,closedCalled,syncCloseSeen;
@property(nonatomic,weak) NSWindow *openedWindow;
@property(nonatomic) NSUInteger openCount;
@end
@implementation MBFakeControlMaster
- (NSString *)controlPath { return self.mockPath; }
- (BOOL)active { return self.mockAlive; }
- (BOOL)isAliveSync { return self.mockAlive; }
- (void)openForDestination:(NSString *)destination window:(NSWindow *)window completion:(void (^)(BOOL, BOOL, NSError *))completion {
 self.openCount++;self.mockDestination=destination;self.openedWindow=window;
 self.mockPath=@"/tmp/macbee-ctl.FIXTURE1/%C";self.mockAlive=YES;
 completion(YES,NO,nil);
}
- (void)closeSynchronously:(BOOL)sync {
 self.closedCalled=YES;if(sync)self.syncCloseSeen=YES;self.mockAlive=NO;
}
@end

@interface MBFakeSheetBrowser : MBSSHBrowse
@property(nonatomic) NSUInteger starts,stops;
@end
@implementation MBFakeSheetBrowser
- (void)startBrowsing{self.starts++;}
- (void)stopBrowsing{self.stops++;}
@end

@interface MBFixtureApp : MBSystemApp
@property NSMutableArray<NSArray *> *setupLog;
@property MBFakeControlMaster *nextMaster;
@property NSMutableArray<MBFakeControlMaster *> *createdMasters;
@property BOOL checkNeedsAuthOnce;
@end
@implementation MBFixtureApp
- (instancetype)init {
 if((self=[super init])){_setupLog=[NSMutableArray array];_createdMasters=[NSMutableArray array];}
 return self;
}
- (MBSSHControlMaster *)newControlMaster {
 MBFakeControlMaster *m=self.nextMaster?:[MBFakeControlMaster new];
 self.nextMaster=nil;[self.createdMasters addObject:m];return m;
}
- (MBAddMacSheet *)newAddMacSheetWithRunner:(MBAddMacSetupRunner)runner {
 MBAddMacSheet *sheet=[super newAddMacSheetWithRunner:runner];
 sheet.browserFactory=^MBSSHBrowse *{return [MBFakeSheetBrowser new];};
 __weak typeof(self) weak=self;
 sheet.controlMasterFactory=^MBSSHControlMaster *{return [weak newControlMaster];};
 return sheet;
}
- (void)performSetup:(NSArray *)arguments completion:(void (^)(NSDictionary *))completion {
 [self.setupLog addObject:arguments];
 NSString *op=arguments.firstObject;
 if([op isEqual:@"--list"]){
  completion(@{@"ok":@YES,@"result":@{@"outputs":@[],@"local_selected":@YES}});
 }else if([op isEqual:@"--select"]){
  completion(@{@"ok":@YES,@"result":@{@"saved":@YES}});
 }else if([op isEqual:@"--discover"]){
  completion(@{@"ok":@YES,@"result":@{@"aliases":@[],@"configured_keys":@[],@"warnings":@[]}});
 }else if([op isEqual:@"--receiver-host"]){
  completion(@{@"ok":@YES,@"result":@{@"id":arguments[1],@"name":@"Test Mac 1",@"ssh_host":@"alex@192.168.1.50"}});
 }else if([op isEqual:@"--check"]){
  if(self.checkNeedsAuthOnce){
   self.checkNeedsAuthOnce=NO;
   completion(@{@"ok":@NO,@"code":@"auth_required",@"receiver_id":arguments[1],@"error":@"Auth required"});
  }else{
   completion(@{@"ok":@YES,@"result":@{@"id":arguments[1],@"availability":@"available"}});
  }
 }else if([op isEqual:@"--add"]){
  completion(@{@"ok":@YES,@"result":@{@"id":@"r-added0000000001",@"name":@"Added Mac",@"selected":@NO,@"availability":@"available"}});
 }else{
  assert(0 && "Unexpected setup operation");
 }
}
@end
static BOOL viewTreeContainsText(NSView *view,NSString *needle){
 if([view isKindOfClass:NSTextField.class] && [((NSTextField *)view).stringValue containsString:needle])return YES;
 if([view isKindOfClass:NSButton.class] && [((NSButton *)view).title containsString:needle])return YES;
 if([view isKindOfClass:NSSegmentedControl.class]){
  NSSegmentedControl *seg=(NSSegmentedControl *)view;
  for(NSInteger i=0;i<seg.segmentCount;i++)if([[seg labelForSegment:i] containsString:needle])return YES;
 }
 for(NSView *sub in view.subviews)if(viewTreeContainsText(sub,needle))return YES;
 return NO;
}
static void complete(MBApplicationSession *s){s.running=NO;s.outputsConnected=NO;s.sharingState=@"idle";}
static void completeAuthRequired(MBApplicationSession *s,NSString *receiverID){
 s.running=NO;s.outputsConnected=NO;s.authRequiredReceiverID=receiverID;s.sharingState=@"failed";
 if(s.stateHandler)s.stateHandler(@"failed");
}
int main(void){@autoreleasepool{
 [NSApplication sharedApplication];[NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
 MBFixtureApp *app=[MBFixtureApp new];app.session=[MBApplicationSession new];[app wireSessionHandlers];[app buildWindow];
 assert(!app.window.visible && app.start.enabled);
 assert([app.window.appearance.name isEqual:NSAppearanceNameAqua]);
 assert(NSEqualSizes(app.window.contentView.frame.size,NSMakeSize(440,520)));
 assert(NSEqualSizes(app.window.contentMinSize,NSMakeSize(380,460)));
 assert(viewTreeContainsText(app.window.contentView,@"Shares all Mac audio, including notifications and calls. No microphone or recordings."));
 assert(!viewTreeContainsText(app.window.contentView,@"One App"));
 assert(!viewTreeContainsText(app.window.contentView,@"SOURCE"));
 assert(!viewTreeContainsText(app.window.contentView,@"Refresh"));
 // Launch loadCatalog must not discover or open SSH masters.
 [app loadCatalog];
 assert(app.setupLog.count==1 && [app.setupLog.firstObject.firstObject isEqual:@"--list"]);
 assert(app.createdMasters.count==0);

 for(int i=1;i<=5;i++)[app addOutput:@{@"id":[NSString stringWithFormat:@"r-%016x",i],@"name":[NSString stringWithFormat:@"Test Mac %d",i],@"selected":@YES,@"availability":@"not_checked"}];
 [app refresh:nil];assert(app.rows.count==6 && [app.selectionSummary.stringValue isEqual:@"6 outputs selected"]);
 assert([app.rows[1].badge.stringValue isEqual:@"Configured"]);
 for(MBOutputRow *row in app.rows)row.selection.state=NSControlStateValueOff;[app refresh:nil];assert(!app.start.enabled);
 app.rows[2].selection.state=NSControlStateValueOn;[app toggle:nil];assert(app.session.chosen.count==1 && [app.session.chosen[0] isEqual:app.rows[2].identifier]);
 assert(!app.add.enabled && !app.rows[0].selection.enabled && !app.rows[1].check.enabled && [app.status.stringValue isEqual:@"Connecting"]);
 [app toggle:nil];[app refresh:nil];assert(app.session.stops==1 && !app.start.enabled);
 complete(app.session);for(MBOutputRow *row in app.rows)row.selection.state=NSControlStateValueOn;[app refresh:nil];[app toggle:nil];assert(app.session.chosen.count==6);
 app.session.outputsConnected=YES;app.session.sharingState=@"waiting";[app refresh:nil];assert([app.status.stringValue isEqual:@"Waiting for audio"]);
 app.rows[3].availability=@"offline";app.session.sharingState=@"playing";[app refresh:nil];assert([app.rows[3].badge.stringValue isEqual:@"Offline"] && [app.rows[4].badge.stringValue isEqual:@"Connected"]);
 assert([app.selectionSummary.stringValue containsString:@"1 offline"] && [app.hint.stringValue containsString:@"remaining outputs continue"]);
 assert(![app windowShouldClose:app.window] && app.closeRequested && app.session.stops==2);
 complete(app.session);[app refresh:nil];assert([app windowShouldClose:app.window]);
 app.closeRequested=NO;
 app.setupBusy=YES;[app refresh:nil];assert(!app.add.enabled && !app.start.enabled && ![app windowShouldClose:app.window]);assert([app applicationShouldTerminate:NSApp]==NSTerminateCancel);
 app.setupBusy=NO;[app toggle:nil];assert([app applicationShouldTerminate:NSApp]==NSTerminateLater && app.quitRequested && app.session.stops==3);complete(app.session);
 app.quitRequested=NO;

 // Test auth_required on Start triggers interactive sign-in on self.window and retries with sshControlPaths!
 for(MBOutputRow *row in app.rows)row.selection.state=NSControlStateValueOff;
 app.rows[1].selection.state=NSControlStateValueOn; // r-0000000000000001
 [app refresh:nil];
 [app toggle:nil];
 assert(app.session.running && app.session.starts==4 && app.session.sshControlPaths.count==0);
 completeAuthRequired(app.session,app.rows[1].identifier);
 assert(app.createdMasters.count==1);
 MBFakeControlMaster *startMaster=app.createdMasters.lastObject;
 assert(startMaster.openCount==1 && [startMaster.mockDestination isEqual:@"alex@192.168.1.50"] && startMaster.openedWindow==app.window);
 assert(app.session.starts==5 && [app.session.sshControlPaths[app.rows[1].identifier] isEqual:@"/tmp/macbee-ctl.FIXTURE1/%C"]);
 // Normal Stop closes ControlMaster!
 app.session.authRequiredReceiverID=nil;complete(app.session);app.session.stateHandler(@"idle");
 assert(startMaster.closedCalled && !startMaster.mockAlive);

 // Test Check with auth_required re-auths and passes --control-path, and Quit closes it.
 app.checkNeedsAuthOnce=YES;
 [app checkMac:app.rows[1].check];
 assert(app.createdMasters.count==2);
 MBFakeControlMaster *checkMaster=app.createdMasters.lastObject;
 assert(checkMaster.openCount==1 && checkMaster.mockAlive);
 NSArray *lastCheck=app.setupLog.lastObject;
 assert([lastCheck.firstObject isEqual:@"--check"] && [lastCheck containsObject:@"--control-path"]);
 [app applicationWillTerminate:[NSNotification notificationWithName:NSApplicationWillTerminateNotification object:NSApp]];
 assert(checkMaster.closedCalled && checkMaster.syncCloseSeen && !checkMaster.mockAlive);

 // Test AddMacSheet flow inside SystemMain: adds unselected row, adopts master, cleans up on quit.
 [app addMac:nil];
 assert(app.activeAddSheet!=nil && app.setupBusy && ![app windowShouldClose:app.window] && [app applicationShouldTerminate:NSApp]==NSTerminateCancel);
 app.activeAddSheet.nameField.stringValue=@"Added Mac";
 app.activeAddSheet.addressField.stringValue=@"alex@192.168.1.60";
 [app.activeAddSheet refreshControls:nil];
 [app.activeAddSheet connectAndAdd:nil];
 assert(app.activeAddSheet==nil && !app.setupBusy && app.rows.count==7);
 assert([app.rows.lastObject.identifier isEqual:@"r-added0000000001"] && app.rows.lastObject.selection.state==NSControlStateValueOff);
 MBFakeControlMaster *addedMaster=app.createdMasters.lastObject;
 assert(addedMaster.mockAlive && !addedMaster.closedCalled);
 [app applicationWillTerminate:[NSNotification notificationWithName:NSApplicationWillTerminateNotification object:NSApp]];
 assert(addedMaster.closedCalled && addedMaster.syncCloseSeen);

 [app.window setContentSize:NSMakeSize(380,460)];[app.window.contentView layoutSubtreeIfNeeded];
 assert(NSContainsRect(app.window.contentView.bounds,[app.start convertRect:app.start.bounds toView:app.window.contentView]));
 assert(NSContainsRect(app.window.contentView.bounds,[app.add convertRect:app.add.bounds toView:app.window.contentView]));
 assert(NSContainsRect(app.window.contentView.bounds,[app.status convertRect:app.status.bounds toView:app.window.contentView]));
 assert(NSContainsRect(app.window.contentView.bounds,[app.hint convertRect:app.hint.bounds toView:app.window.contentView]));
 assert(NSContainsRect(app.window.contentView.bounds,[app.selectionSummary convertRect:app.selectionSummary.bounds toView:app.window.contentView]));
 assert(!app.window.visible);
 for(int i=6;i<=15;i++)[app addOutput:@{@"id":[NSString stringWithFormat:@"r-%016x",i],@"name":i==15?@"A deliberately long device name that must truncate safely":@"Additional Test Mac",@"selected":@NO,@"availability":@"not_checked"}];
 [app.window.contentView layoutSubtreeIfNeeded];NSScrollView *scroll=app.outputList.enclosingScrollView;
 assert(app.rows.count==17 && scroll.hasVerticalScroller && scroll.documentView.frame.size.height>scroll.contentView.bounds.size.height);
 assert(NSContainsRect(app.window.contentView.bounds,[app.start convertRect:app.start.bounds toView:app.window.contentView]));
 const char *snapshot=getenv("MACBEE_UI_SNAPSHOT");
 if(snapshot){
  MBSystemApp *preview=[MBFixtureApp new];preview.session=[MBApplicationSession new];[preview buildWindow];
  NSArray *names=@[@"Studio Mac",@"Living Room",@"Office Mac",@"Travel Mac"];
  for(NSUInteger i=0;i<names.count;i++)[preview addOutput:@{@"id":[NSString stringWithFormat:@"r-%016lx",(unsigned long)i+1],@"name":names[i],@"selected":@(i<2),@"availability":i==3?@"offline":@"not_checked"}];
  [preview refresh:nil];[preview.window setContentSize:NSMakeSize(440,520)];
  NSView *view=preview.window.contentView;view.wantsLayer=YES;view.layer.backgroundColor=preview.window.backgroundColor.CGColor;
  [view layoutSubtreeIfNeeded];[view display];[NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.05]];
  NSBitmapImageRep *rep=[view bitmapImageRepForCachingDisplayInRect:view.bounds];
  [view cacheDisplayInRect:view.bounds toBitmapImageRep:rep];NSData *png=[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
  assert([png writeToFile:[NSString stringWithUTF8String:snapshot] atomically:YES]);assert(!preview.window.visible);
 }

 puts("real native configured list: six rows, subset, Waiting/Playing, offline isolation, setup gate, Stop and Quit passed; inert session, window never shown");
}return 0;}
