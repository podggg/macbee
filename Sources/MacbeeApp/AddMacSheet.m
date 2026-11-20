#import "AddMacSheet.h"

@implementation MBAddMacItem
@end

@implementation MBAddMacRowCellView

- (void)applyColors {
    BOOL emphasized = (self.backgroundStyle == NSBackgroundStyleEmphasized);
    if (emphasized) {
        self.titleLabel.textColor = NSColor.alternateSelectedControlTextColor;
        self.subtitleLabel.textColor = [NSColor.alternateSelectedControlTextColor colorWithAlphaComponent:0.88];
        if (self.badgeLabel) {
            self.badgeLabel.textColor = NSColor.alternateSelectedControlTextColor;
            [self.effectiveAppearance performAsCurrentDrawingAppearance:^{
                self.badgePill.layer.backgroundColor = [NSColor.alternateSelectedControlTextColor colorWithAlphaComponent:0.22].CGColor;
            }];
        }
    } else {
        self.titleLabel.textColor = self.rowDisabled ? NSColor.secondaryLabelColor : NSColor.labelColor;
        self.subtitleLabel.textColor = self.rowDisabled ? NSColor.tertiaryLabelColor : NSColor.secondaryLabelColor;
        if (self.badgeLabel) {
            self.badgeLabel.textColor = NSColor.secondaryLabelColor;
            [self.effectiveAppearance performAsCurrentDrawingAppearance:^{
                self.badgePill.layer.backgroundColor = [NSColor.labelColor colorWithAlphaComponent:0.08].CGColor;
            }];
        }
    }
}

- (void)setBackgroundStyle:(NSBackgroundStyle)backgroundStyle {
    [super setBackgroundStyle:backgroundStyle];
    [self applyColors];
}

- (void)viewDidChangeEffectiveAppearance {
    [super viewDidChangeEffectiveAppearance];
    [self applyColors];
}

@end

static NSError *MBAddSheetError(NSString *message) {
    return [NSError errorWithDomain:@"Macbee.AddMac" code:1 userInfo:@{NSLocalizedDescriptionKey: message}];
}

static NSTextField *MBAddLabel(NSString *text, CGFloat size, NSFontWeight weight, NSColor *color) {
    NSTextField *field = [NSTextField wrappingLabelWithString:text];
    field.font = [NSFont systemFontOfSize:size weight:weight];
    field.textColor = color;
    return field;
}

static NSStackView *MBAddStack(NSArray<NSView *> *items, BOOL vertical, CGFloat gap) {
    NSStackView *view = [NSStackView stackViewWithViews:items];
    view.spacing = gap;
    view.orientation = vertical ? NSUserInterfaceLayoutOrientationVertical : NSUserInterfaceLayoutOrientationHorizontal;
    view.alignment = vertical ? NSLayoutAttributeLeading : NSLayoutAttributeCenterY;
    return view;
}

static NSString *MBStripTrailingDot(NSString *host) {
    NSString *result = host ?: @"";
    while ([result hasSuffix:@"."]) {
        result = [result substringToIndex:result.length - 1];
    }
    return result;
}

static NSString *MBNormalizedHost(NSString *host) {
    NSString *trimmed = [(host ?: @"") stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].lowercaseString;
    return MBStripTrailingDot(trimmed);
}

static BOOL MBHostMatchesLocalHost(NSString *bonjourHost, NSString *localHostName) {
    NSString *candidate = MBNormalizedHost(bonjourHost);
    NSString *local = MBNormalizedHost(localHostName);
    if (!candidate.length || !local.length) return NO;
    if ([candidate isEqual:local]) return YES;
    if (![local containsString:@"."] && [candidate isEqual:[local stringByAppendingString:@".local"]]) return YES;
    if ([local hasSuffix:@".local"] && ![candidate containsString:@"."]) {
        NSString *bare = [local substringToIndex:local.length - 6];
        if (bare.length && [candidate isEqual:bare]) return YES;
    }
    return NO;
}

static NSString *MBDefaultDeviceName(NSString *rawName) {
    NSString *trimmed = [rawName stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if ([trimmed.lowercaseString hasSuffix:@".local."]) {
        trimmed = [trimmed substringToIndex:trimmed.length - 7];
    } else if ([trimmed.lowercaseString hasSuffix:@".local"]) {
        trimmed = [trimmed substringToIndex:trimmed.length - 6];
    }
    if (trimmed.length > 60) {
        trimmed = [trimmed substringToIndex:60];
    }
    return trimmed;
}

@interface MBAddMacSheet ()
@property(nonatomic, copy) MBAddMacSetupRunner setupRunner;
@property(nonatomic, readwrite, strong) NSWindow *sheetWindow;
@property(nonatomic, readwrite, strong) NSTableView *tableView;
@property(nonatomic, readwrite, strong) NSTextField *nameField;
@property(nonatomic, readwrite, strong) NSTextField *usernameField;
@property(nonatomic, readwrite, strong) NSTextField *addressField;
@property(nonatomic, readwrite, strong) NSTextField *targetPreviewLabel;
@property(nonatomic, readwrite, strong) NSTextField *discoveryStatusLabel;
@property(nonatomic, readwrite, strong) NSTextField *statusLabel;
@property(nonatomic, readwrite, strong) NSButton *rememberKeychain;
@property(nonatomic, readwrite, strong) NSButton *connectButton;
@property(nonatomic, readwrite, strong) NSButton *cancelButton;

@property(nonatomic, strong) NSView *usernameSection;
@property(nonatomic, strong) NSView *addressSection;
@property(nonatomic, strong) NSView *targetSection;

@property(nonatomic, copy) NSArray<NSDictionary *> *aliases;
@property(nonatomic, copy) NSArray<NSDictionary *> *configuredKeys;
@property(nonatomic, copy) NSArray<NSDictionary *> *bonjourRows;
@property(nonatomic, copy) NSArray<NSString *> *discoveryWarnings;
@property(nonatomic, copy) NSString *bonjourStatus;
@property(nonatomic, readwrite, copy) NSArray<MBAddMacItem *> *items;

@property(nonatomic, copy, nullable) NSString *selectedItemID;
@property(nonatomic) BOOL nameEditedByUser;
@property(nonatomic) BOOL userInteractedWithSelection;
@property(nonatomic) BOOL applyingAutoSelection;
@property(nonatomic) BOOL reloadingTable;
@property(nonatomic) BOOL busy;
@property(nonatomic) BOOL closed;

@property(nonatomic, weak, nullable) NSWindow *parentWindow;
@property(nonatomic, strong, nullable) MBSSHBrowse *browser;
@property(nonatomic, strong, nullable) MBSSHControlMaster *activeMaster;
@property(nonatomic, copy, nullable) void (^completion)(NSDictionary *_Nullable, MBSSHControlMaster *_Nullable, NSString *_Nullable);
@end

@implementation MBAddMacSheet

- (instancetype)initWithSetupRunner:(MBAddMacSetupRunner)setupRunner {
    if ((self = [super init])) {
        _setupRunner = [setupRunner copy];
        _aliases = @[];
        _configuredKeys = @[];
        _bonjourRows = @[];
        _discoveryWarnings = @[];
        _bonjourStatus = @"";
        _items = @[];
        _localHostName = [NSProcessInfo.processInfo.hostName copy] ?: @"";
    }
    return self;
}

- (void)buildSheet {
    if (self.sheetWindow) return;

    self.sheetWindow = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 480, 560)
                                                   styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
                                                     backing:NSBackingStoreBuffered
                                                       defer:NO];
    self.sheetWindow.title = @"Add a Mac";
    self.sheetWindow.delegate = self;
    self.sheetWindow.contentMinSize = NSMakeSize(440, 520);
    self.sheetWindow.backgroundColor = NSColor.windowBackgroundColor;

    NSTextField *title = MBAddLabel(@"Add a Mac", 18, NSFontWeightSemibold, NSColor.labelColor);
    NSTextField *intro = MBAddLabel(@"Pick a Mac below or enter its SSH address. Remote Login must be enabled on that Mac.", 12, NSFontWeightRegular, NSColor.secondaryLabelColor);

    self.discoveryStatusLabel = MBAddLabel(@"Looking for nearby Macs and SSH config aliases…", 11, NSFontWeightRegular, NSColor.secondaryLabelColor);
    self.discoveryStatusLabel.accessibilityLabel = @"Discovery status";

    self.tableView = [[NSTableView alloc] initWithFrame:NSMakeRect(0, 0, 420, 140)];
    self.tableView.headerView = nil;
    self.tableView.rowHeight = 42;
    self.tableView.usesAlternatingRowBackgroundColors = NO;
    self.tableView.allowsEmptySelection = NO;
    self.tableView.dataSource = self;
    self.tableView.delegate = self;
    self.tableView.accessibilityLabel = @"Available SSH destinations";

    NSTableColumn *col = [[NSTableColumn alloc] initWithIdentifier:@"destination"];
    col.resizingMask = NSTableColumnAutoresizingMask;
    col.width = 416;
    [self.tableView addTableColumn:col];

    NSScrollView *listScroll = [NSScrollView new];
    listScroll.documentView = self.tableView;
    listScroll.hasVerticalScroller = YES;
    listScroll.autohidesScrollers = YES;
    listScroll.borderType = NSBezelBorder;
    [listScroll.heightAnchor constraintGreaterThanOrEqualToConstant:130].active = YES;

    NSTextField *nameLabel = MBAddLabel(@"DEVICE NAME", 10, NSFontWeightBold, NSColor.secondaryLabelColor);
    self.nameField = [NSTextField textFieldWithString:@""];
    self.nameField.placeholderString = @"Living Room Mac";
    self.nameField.accessibilityLabel = @"Device name";
    self.nameField.delegate = self;
    NSView *nameSection = MBAddStack(@[nameLabel, self.nameField], YES, 4);
    [self.nameField.widthAnchor constraintEqualToAnchor:nameSection.widthAnchor].active = YES;

    NSTextField *userLabel = MBAddLabel(@"MAC USERNAME", 10, NSFontWeightBold, NSColor.secondaryLabelColor);
    self.usernameField = [NSTextField textFieldWithString:NSUserName() ?: @""];
    self.usernameField.placeholderString = @"alex";
    self.usernameField.accessibilityLabel = @"Mac username";
    self.usernameField.delegate = self;
    NSTextField *userHint = MBAddLabel(@"Account short name on that Mac (for example, alex).", 11, NSFontWeightRegular, NSColor.secondaryLabelColor);
    NSStackView *userStack = MBAddStack(@[userLabel, self.usernameField, userHint], YES, 4);
    [self.usernameField.widthAnchor constraintEqualToAnchor:userStack.widthAnchor].active = YES;
    self.usernameSection = userStack;

    NSTextField *addrLabel = MBAddLabel(@"SSH ADDRESS", 10, NSFontWeightBold, NSColor.secondaryLabelColor);
    self.addressField = [NSTextField textFieldWithString:@""];
    self.addressField.placeholderString = @"username@192.168.1.20";
    self.addressField.accessibilityLabel = @"SSH address";
    self.addressField.delegate = self;
    NSTextField *addrFormat = MBAddLabel(@"Format: user@host, user@host:port, or an SSH config alias — do not include \"ssh \".", 11, NSFontWeightRegular, NSColor.secondaryLabelColor);
    NSTextField *addrExamples = MBAddLabel(@"Examples: alex@192.168.1.20 or alex@mac-mini.local (IPv6: alex@[fe80::1]:22).", 11, NSFontWeightRegular, NSColor.secondaryLabelColor);
    NSStackView *addrStack = MBAddStack(@[addrLabel, self.addressField, addrFormat, addrExamples], YES, 4);
    [self.addressField.widthAnchor constraintEqualToAnchor:addrStack.widthAnchor].active = YES;
    self.addressSection = addrStack;

    NSTextField *targetHeader = MBAddLabel(@"TARGET", 10, NSFontWeightBold, NSColor.secondaryLabelColor);
    self.targetPreviewLabel = MBAddLabel(@"", 12, NSFontWeightMedium, NSColor.labelColor);
    self.targetPreviewLabel.accessibilityLabel = @"Resolved SSH target";
    NSStackView *targetStack = MBAddStack(@[targetHeader, self.targetPreviewLabel], YES, 3);
    self.targetSection = targetStack;

    self.rememberKeychain = [NSButton checkboxWithTitle:@"Remember passphrase in Keychain"
                                                 target:self
                                                 action:@selector(refreshControls:)];
    self.rememberKeychain.state = NSControlStateValueOff;
    self.rememberKeychain.font = [NSFont systemFontOfSize:12 weight:NSFontWeightRegular];
    self.rememberKeychain.accessibilityHelp = @"Optional. Saves your SSH key passphrase in macOS Keychain via ssh-add when checked.";

    self.statusLabel = MBAddLabel(@"Select a Mac or enter its address, then click Connect & Add.", 12, NSFontWeightRegular, NSColor.labelColor);
    self.statusLabel.accessibilityLabel = @"Add Mac status";

    self.cancelButton = [NSButton buttonWithTitle:@"Cancel" target:self action:@selector(cancel:)];
    self.cancelButton.bezelStyle = NSBezelStyleRounded;
    self.cancelButton.keyEquivalent = @"\033";

    self.connectButton = [NSButton buttonWithTitle:@"Connect & Add" target:self action:@selector(connectAndAdd:)];
    self.connectButton.bezelStyle = NSBezelStyleRounded;
    self.connectButton.keyEquivalent = @"\r";

    NSView *spacer = [NSView new];
    [spacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSStackView *buttons = MBAddStack(@[spacer, self.cancelButton, self.connectButton], NO, 10);

    NSArray<NSView *> *rows = @[
        title,
        intro,
        self.discoveryStatusLabel,
        listScroll,
        nameSection,
        self.usernameSection,
        self.addressSection,
        self.targetSection,
        self.rememberKeychain,
        self.statusLabel,
        buttons
    ];
    NSStackView *layout = MBAddStack(rows, YES, 10);
    layout.detachesHiddenViews = YES;
    layout.translatesAutoresizingMaskIntoConstraints = NO;

    NSView *content = self.sheetWindow.contentView;
    [content addSubview:layout];
    [NSLayoutConstraint activateConstraints:@[
        [layout.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:20],
        [layout.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-20],
        [layout.topAnchor constraintEqualToAnchor:content.topAnchor constant:18],
        [layout.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-18]
    ]];
    for (NSView *view in rows) {
        [view.widthAnchor constraintEqualToAnchor:layout.widthAnchor].active = YES;
    }

    [self rebuildItemsPreservingSelection:NO];
}

- (void)presentOnWindow:(NSWindow *)parentWindow
             completion:(void (^)(NSDictionary *_Nullable addedRow,
                                  MBSSHControlMaster *_Nullable master,
                                  NSString *_Nullable notice))completion {
    NSAssert(NSThread.isMainThread, @"AddMacSheet must be presented on the main thread");
    [self buildSheet];
    self.parentWindow = parentWindow;
    self.completion = completion;
    self.closed = NO;
    self.busy = NO;
    self.nameEditedByUser = NO;
    self.userInteractedWithSelection = NO;
    self.rememberKeychain.state = NSControlStateValueOff;

    __weak typeof(self) weak = self;
    MBSSHBrowse *browser = self.browserFactory ? self.browserFactory() : [MBSSHBrowse new];
    self.browser = browser;
    browser.updated = ^(NSArray<NSDictionary *> *rows, NSString *status) {
        MBAddMacSheet *strong = weak;
        if (!strong || strong.closed) return;
        [strong applyBonjourRows:rows status:status];
    };
    [browser startBrowsing];

    if (self.setupRunner) {
        self.setupRunner(@[@"--discover"], ^(NSDictionary *result) {
            MBAddMacSheet *strong = weak;
            if (!strong || strong.closed) return;
            [strong applyDiscoveryResult:result];
        });
    }

    if (parentWindow.visible || NSApp.activationPolicy != NSApplicationActivationPolicyProhibited) {
        [parentWindow beginSheet:self.sheetWindow completionHandler:^(NSModalResponse returnCode) {
            (void)returnCode;
        }];
    }
}

- (void)applyDiscoveryResult:(NSDictionary *)result {
    if (![result[@"ok"] boolValue]) {
        self.discoveryWarnings = @[result[@"error"] ?: @"Could not read ~/.ssh/config."];
        [self rebuildItemsPreservingSelection:YES];
        return;
    }
    NSDictionary *payload = [result[@"result"] isKindOfClass:NSDictionary.class] ? result[@"result"] : @{};
    self.aliases = [payload[@"aliases"] isKindOfClass:NSArray.class] ? payload[@"aliases"] : @[];
    self.configuredKeys = [payload[@"configured_keys"] isKindOfClass:NSArray.class] ? payload[@"configured_keys"] : @[];
    self.discoveryWarnings = [payload[@"warnings"] isKindOfClass:NSArray.class] ? payload[@"warnings"] : @[];
    [self rebuildItemsPreservingSelection:YES];
}

- (void)applyBonjourRows:(NSArray<NSDictionary *> *)rows status:(NSString *)status {
    self.bonjourRows = rows ?: @[];
    self.bonjourStatus = status ?: @"";
    [self rebuildItemsPreservingSelection:YES];
}

- (void)rebuildItemsPreservingSelection:(BOOL)preserve {
    NSMutableArray<MBAddMacItem *> *built = [NSMutableArray array];

    for (NSDictionary *row in self.bonjourRows) {
        NSString *name = [row[@"name"] isKindOfClass:NSString.class] ? row[@"name"] : @"";
        NSString *rawHost = [row[@"host"] isKindOfClass:NSString.class] ? row[@"host"] : @"";
        NSString *host = MBStripTrailingDot(rawHost);
        NSInteger port = [row[@"port"] isKindOfClass:NSNumber.class] ? [row[@"port"] integerValue] : 22;
        NSString *defaultUser = [row[@"user"] isKindOfClass:NSString.class] ? row[@"user"] : (NSUserName() ?: @"");
        if (!name.length || !host.length) continue;

        NSString *probeEndpoint = (port > 0 && port != 22)
            ? [NSString stringWithFormat:@"%@@%@:%ld", defaultUser.length ? defaultUser : @"user", host, (long)port]
            : [NSString stringWithFormat:@"%@@%@", defaultUser.length ? defaultUser : @"user", host];
        BOOL isThisMac = MBHostMatchesLocalHost(host, self.localHostName);
        BOOL configured = !isThisMac && [MBSSHControlMaster isDestination:probeEndpoint
                                                         configuredInKeys:self.configuredKeys
                                                                  aliases:self.aliases];

        MBAddMacItem *item = [MBAddMacItem new];
        item.kind = MBAddMacItemBonjour;
        item.identifier = [NSString stringWithFormat:@"bonjour:%@:%ld", host.lowercaseString, (long)(port > 0 ? port : 22)];
        item.title = name;
        item.host = host;
        item.port = (port > 0) ? port : 22;
        item.defaultUser = defaultUser;
        item.configured = configured;
        item.isThisMac = isThisMac;
        item.subtitle = (item.port != 22)
            ? [NSString stringWithFormat:@"%@:%ld · Nearby Mac", host, (long)item.port]
            : [NSString stringWithFormat:@"%@ · Nearby Mac", host];
        [built addObject:item];
    }

    for (NSDictionary *row in self.aliases) {
        NSString *alias = [row[@"alias"] isKindOfClass:NSString.class] ? row[@"alias"] : @"";
        if (!alias.length) continue;
        NSString *preview = [row[@"preview"] isKindOfClass:NSString.class] ? row[@"preview"] : @"unresolved";
        NSString *host = [row[@"host"] isKindOfClass:NSString.class] ? row[@"host"] : @"";
        NSString *user = [row[@"user"] isKindOfClass:NSString.class] ? row[@"user"] : @"";
        NSInteger port = [row[@"port"] isKindOfClass:NSNumber.class] ? [row[@"port"] integerValue] : 22;
        BOOL configured = [row[@"configured"] boolValue];

        MBAddMacItem *item = [MBAddMacItem new];
        item.kind = MBAddMacItemAlias;
        item.identifier = [@"alias:" stringByAppendingString:alias];
        item.title = alias;
        item.host = host;
        item.defaultUser = user;
        item.port = (port > 0) ? port : 22;
        item.preview = preview;
        item.configured = configured;
        if ([preview isEqual:@"static"] && host.length) {
            NSString *userHost = user.length ? [NSString stringWithFormat:@"%@@%@", user, host] : host;
            item.subtitle = (item.port != 22)
                ? [NSString stringWithFormat:@"%@:%ld · SSH config", userHost, (long)item.port]
                : [NSString stringWithFormat:@"%@ · SSH config", userHost];
        } else {
            item.subtitle = @"SSH config alias";
        }
        [built addObject:item];
    }

    MBAddMacItem *manual = [MBAddMacItem new];
    manual.kind = MBAddMacItemManual;
    manual.identifier = @"manual";
    manual.title = @"Enter address manually…";
    manual.subtitle = @"Type user@host or user@192.168.1.20";
    manual.configured = NO;
    [built addObject:manual];

    NSInteger targetRow = NSNotFound;
    if (preserve && self.userInteractedWithSelection && self.selectedItemID.length) {
        for (NSUInteger i = 0; i < built.count; i++) {
            if ([built[i].identifier isEqual:self.selectedItemID] && !built[i].configured && !built[i].isThisMac) {
                targetRow = (NSInteger)i;
                break;
            }
        }
    }
    if (targetRow == NSNotFound) {
        // Default to the first selectable discovered item, or Manual if none exist yet.
        for (NSUInteger i = 0; i < built.count; i++) {
            if (!built[i].configured && !built[i].isThisMac) {
                targetRow = (NSInteger)i;
                break;
            }
        }
    }

    self.reloadingTable = YES;
    self.items = built;
    [self.tableView reloadData];
    self.reloadingTable = NO;

    NSMutableArray<NSString *> *notes = [NSMutableArray array];
    if (self.bonjourStatus.length) {
        [notes addObject:self.bonjourStatus];
    } else if (self.bonjourRows.count == 0) {
        [notes addObject:@"Looking for nearby Macs with Remote Login on…"];
    }
    if (self.discoveryWarnings.count) {
        [notes addObject:self.discoveryWarnings.firstObject];
    }
    self.discoveryStatusLabel.stringValue = [notes componentsJoinedByString:@" "];

    if (targetRow != NSNotFound && targetRow < (NSInteger)self.items.count) {
        [self applySelectionAtIndex:targetRow userInitiated:NO];
    }
}

- (nullable MBAddMacItem *)selectedItem {
    NSInteger row = self.tableView.selectedRow;
    if (row < 0 || row >= (NSInteger)self.items.count) return nil;
    return self.items[(NSUInteger)row];
}

- (BOOL)selectItemAtIndex:(NSInteger)index {
    return [self applySelectionAtIndex:index userInitiated:YES];
}

- (BOOL)applySelectionAtIndex:(NSInteger)index userInitiated:(BOOL)userInitiated {
    if (index < 0 || index >= (NSInteger)self.items.count) return NO;
    MBAddMacItem *item = self.items[(NSUInteger)index];
    if (item.configured || item.isThisMac) return NO;

    if (userInitiated) self.userInteractedWithSelection = YES;
    BOOL changedSelection = ![self.selectedItemID isEqual:item.identifier];
    self.selectedItemID = item.identifier;
    self.applyingAutoSelection = !userInitiated;
    [self.tableView selectRowIndexes:[NSIndexSet indexSetWithIndex:(NSUInteger)index] byExtendingSelection:NO];
    self.applyingAutoSelection = NO;

    if (changedSelection) {
        if (item.kind == MBAddMacItemBonjour) {
            self.usernameField.stringValue = item.defaultUser.length ? item.defaultUser : (NSUserName() ?: @"");
            if (!self.nameEditedByUser || !self.nameField.stringValue.length) {
                self.nameField.stringValue = MBDefaultDeviceName(item.title);
            }
        } else if (item.kind == MBAddMacItemAlias) {
            if (!self.nameEditedByUser || !self.nameField.stringValue.length) {
                self.nameField.stringValue = MBDefaultDeviceName(item.title);
            }
        } else {
            if (!self.nameEditedByUser) {
                self.nameField.stringValue = @"";
            }
        }
    }

    self.usernameSection.hidden = (item.kind != MBAddMacItemBonjour);
    self.addressSection.hidden = (item.kind != MBAddMacItemManual);
    self.targetSection.hidden = (item.kind == MBAddMacItemManual);
    [self refreshControls:nil];
    return YES;
}

- (void)controlTextDidChange:(NSNotification *)obj {
    self.userInteractedWithSelection = YES;
    if (obj.object == self.nameField) {
        NSString *trimmed = [self.nameField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        self.nameEditedByUser = trimmed.length > 0;
    }
    [self refreshControls:nil];
}

- (nullable NSString *)resolvedDestinationForItem:(MBAddMacItem *)item
                                     strippedPrefix:(BOOL *)strippedOut
                                              error:(NSError **)error {
    if (strippedOut) *strippedOut = NO;
    if (!item || item.configured || item.isThisMac) {
        if (error) {
            *error = MBAddSheetError(item.isThisMac
                ? @"This Mac cannot be added as a remote output."
                : @"Choose an unconfigured Mac or enter an address manually.");
        }
        return nil;
    }
    if (item.kind == MBAddMacItemBonjour) {
        NSString *user = [self.usernameField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (!user.length) {
            if (error) *error = MBAddSheetError(@"Enter the account username on the receiving Mac.");
            return nil;
        }
        NSString *raw = (item.port > 0 && item.port != 22)
            ? [NSString stringWithFormat:@"%@@%@:%ld", user, item.host, (long)item.port]
            : [NSString stringWithFormat:@"%@@%@", user, item.host];
        NSString *canonical = nil;
        if (![MBSSHControlMaster sshArgumentsForEndpoint:raw canonical:&canonical error:error]) {
            return nil;
        }
        return canonical;
    }
    if (item.kind == MBAddMacItemAlias) {
        NSString *canonical = nil;
        if (![MBSSHControlMaster sshArgumentsForEndpoint:item.title canonical:&canonical error:error]) {
            return nil;
        }
        return canonical;
    }
    NSString *normalized = [MBSSHControlMaster normalizeManualAddress:self.addressField.stringValue
                                                    strippedSSHPrefix:strippedOut];
    NSString *canonical = nil;
    if (![MBSSHControlMaster sshArgumentsForEndpoint:normalized canonical:&canonical error:error]) {
        return nil;
    }
    return canonical;
}

- (void)refreshControls:(id)sender {
    (void)sender;
    MBAddMacItem *item = [self selectedItem];
    if (item && item.kind == MBAddMacItemBonjour) {
        NSString *user = [self.usernameField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        NSString *target = user.length ? [NSString stringWithFormat:@"%@@%@", user, item.host] : item.host;
        if (item.port > 0 && item.port != 22) {
            target = [target stringByAppendingFormat:@":%ld", (long)item.port];
        }
        self.targetPreviewLabel.stringValue = target;
    } else if (item && item.kind == MBAddMacItemAlias) {
        if ([item.preview isEqual:@"static"] && item.host.length) {
            NSString *target = item.defaultUser.length
                ? [NSString stringWithFormat:@"%@@%@", item.defaultUser, item.host]
                : item.host;
            if (item.port > 0 && item.port != 22) {
                target = [target stringByAppendingFormat:@":%ld", (long)item.port];
            }
            self.targetPreviewLabel.stringValue = [NSString stringWithFormat:@"%@ → %@", item.title, target];
        } else {
            self.targetPreviewLabel.stringValue = item.title;
        }
    } else {
        self.targetPreviewLabel.stringValue = @"";
    }

    NSString *name = [self.nameField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    BOOL hasTarget = NO;
    if (item && !item.configured && !item.isThisMac) {
        if (item.kind == MBAddMacItemBonjour) {
            hasTarget = [self.usernameField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length > 0;
        } else if (item.kind == MBAddMacItemAlias) {
            hasTarget = YES;
        } else {
            hasTarget = [self.addressField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length > 0;
        }
    }

    self.nameField.enabled = !self.busy;
    self.usernameField.enabled = !self.busy;
    self.addressField.enabled = !self.busy;
    self.rememberKeychain.enabled = !self.busy;
    self.tableView.enabled = !self.busy;
    self.cancelButton.enabled = YES;
    self.connectButton.enabled = !self.busy && name.length > 0 && hasTarget;
}

- (void)connectAndAdd:(id)sender {
    (void)sender;
    if (self.busy || self.closed) return;

    NSString *deviceName = [self.nameField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!deviceName.length || deviceName.length > 60) {
        self.statusLabel.stringValue = @"Enter a short device name (1 to 60 characters).";
        return;
    }
    for (NSUInteger i = 0; i < deviceName.length; i++) {
        if ([deviceName characterAtIndex:i] < 32) {
            self.statusLabel.stringValue = @"Device name cannot contain control characters.";
            return;
        }
    }

    MBAddMacItem *item = [self selectedItem];
    BOOL strippedPrefix = NO;
    NSError *destError = nil;
    NSString *destination = [self resolvedDestinationForItem:item strippedPrefix:&strippedPrefix error:&destError];
    if (!destination) {
        self.statusLabel.stringValue = destError.localizedDescription ?: @"Enter a valid SSH address (for example, username@192.168.1.20).";
        return;
    }
    if (strippedPrefix) {
        self.addressField.stringValue = destination;
    }
    if ([MBSSHControlMaster isDestination:destination configuredInKeys:self.configuredKeys aliases:self.aliases]) {
        self.statusLabel.stringValue = @"This Mac is already added.";
        return;
    }

    BOOL wantKeychain = (self.rememberKeychain.state == NSControlStateValueOn);
    self.busy = YES;
    self.statusLabel.stringValue = @"Signing in via SSH…";
    [self refreshControls:nil];

    MBSSHControlMaster *master = self.controlMasterFactory ? self.controlMasterFactory() : [MBSSHControlMaster new];
    self.activeMaster = master;

    __weak typeof(self) weak = self;
    [master openForDestination:destination window:self.sheetWindow completion:^(BOOL ok, BOOL cancelled, NSError *error) {
        MBAddMacSheet *strong = weak;
        if (!strong || strong.closed) {
            [master closeSynchronously:NO];
            return;
        }
        if (!ok) {
            strong.busy = NO;
            strong.activeMaster = nil;
            strong.statusLabel.stringValue = cancelled
                ? @"Sign-in cancelled. Nothing was added."
                : (error.localizedDescription ?: @"SSH sign-in failed.");
            [strong refreshControls:nil];
            return;
        }

        strong.statusLabel.stringValue = @"Verifying and setting up receiver components…";
        NSString *ctlPath = master.controlPath ?: @"";
        NSMutableArray<NSString *> *args = [@[@"--add", @"--host", destination, @"--name", deviceName] mutableCopy];
        if (ctlPath.length) {
            [args addObjectsFromArray:@[@"--control-path", ctlPath]];
        }
        if (!strong.setupRunner) {
            [master closeSynchronously:NO];
            strong.busy = NO;
            strong.activeMaster = nil;
            strong.statusLabel.stringValue = @"Receiver setup is unavailable.";
            [strong refreshControls:nil];
            return;
        }
        strong.setupRunner(args, ^(NSDictionary *setupResult) {
            MBAddMacSheet *current = weak;
            if (!current || current.closed) {
                [master closeSynchronously:NO];
                return;
            }
            if (![setupResult[@"ok"] boolValue]) {
                [master closeSynchronously:NO];
                current.busy = NO;
                current.activeMaster = nil;
                current.statusLabel.stringValue = setupResult[@"error"] ?: @"The Mac was not added.";
                [current refreshControls:nil];
                return;
            }
            NSDictionary *addedRow = [setupResult[@"result"] isKindOfClass:NSDictionary.class] ? setupResult[@"result"] : nil;
            if (wantKeychain && master.promptedIdentityPath.length) {
                current.statusLabel.stringValue = @"Saving key passphrase in Keychain…";
                [master saveIdentityToKeychain:master.promptedIdentityPath
                                        window:current.sheetWindow
                                    completion:^(BOOL saved, BOOL kcCancelled, NSError *kcError) {
                    MBAddMacSheet *doneSheet = weak;
                    if (!doneSheet || doneSheet.closed) {
                        [master closeSynchronously:NO];
                        return;
                    }
                    NSString *notice = nil;
                    if (saved) {
                        notice = @"Mac added (not selected) and passphrase saved in Keychain.";
                    } else if (kcCancelled) {
                        notice = @"Mac added, not selected. Keychain passphrase was not saved.";
                    } else {
                        notice = kcError.localizedDescription
                            ? [@"Mac added, not selected. " stringByAppendingString:kcError.localizedDescription]
                            : @"Mac added, not selected. Keychain could not save the passphrase.";
                    }
                    [doneSheet finishWithAddedRow:addedRow master:master notice:notice];
                }];
                return;
            }
            [current finishWithAddedRow:addedRow master:master notice:nil];
        });
    }];
}

- (void)finishWithAddedRow:(nullable NSDictionary *)addedRow
                    master:(nullable MBSSHControlMaster *)master
                    notice:(nullable NSString *)notice {
    self.closed = YES;
    self.busy = NO;
    self.activeMaster = nil;
    [self.browser stopBrowsing];
    self.browser.updated = nil;
    self.browser = nil;
    if (self.parentWindow && self.sheetWindow.sheetParent == self.parentWindow) {
        [self.parentWindow endSheet:self.sheetWindow returnCode:addedRow ? NSModalResponseOK : NSModalResponseCancel];
    }
    void (^done)(NSDictionary *, MBSSHControlMaster *, NSString *) = self.completion;
    self.completion = nil;
    if (done) done(addedRow, master, notice);
}

- (void)cancel:(id)sender {
    (void)sender;
    if (self.closed) return;
    MBSSHControlMaster *master = self.activeMaster;
    self.activeMaster = nil;
    [master closeSynchronously:NO];
    [self finishWithAddedRow:nil master:nil notice:nil];
}

#pragma mark - NSTableViewDataSource / Delegate

- (NSInteger)numberOfRowsInTableView:(NSTableView *)tableView {
    (void)tableView;
    return (NSInteger)self.items.count;
}

- (BOOL)tableView:(NSTableView *)tableView shouldSelectRow:(NSInteger)row {
    (void)tableView;
    if (row < 0 || row >= (NSInteger)self.items.count) return NO;
    MBAddMacItem *item = self.items[(NSUInteger)row];
    return !item.configured && !item.isThisMac;
}

- (void)tableViewSelectionDidChange:(NSNotification *)notification {
    (void)notification;
    if (self.reloadingTable) return;
    NSInteger row = self.tableView.selectedRow;
    if (row >= 0 && row < (NSInteger)self.items.count) {
        [self applySelectionAtIndex:row userInitiated:!self.applyingAutoSelection];
    }
}

- (BOOL)windowShouldClose:(NSWindow *)sender {
    [self cancel:sender];
    return NO;
}

- (nullable NSView *)tableView:(NSTableView *)tableView viewForTableColumn:(NSTableColumn *)tableColumn row:(NSInteger)row {
    (void)tableView;
    (void)tableColumn;
    if (row < 0 || row >= (NSInteger)self.items.count) return nil;
    MBAddMacItem *item = self.items[(NSUInteger)row];
    BOOL rowDisabled = item.configured || item.isThisMac;
    NSString *badgeText = item.isThisMac ? @"This Mac" : (item.configured ? @"Added" : nil);

    MBAddMacRowCellView *cell = [MBAddMacRowCellView new];
    cell.rowDisabled = rowDisabled;

    NSTextField *title = [NSTextField labelWithString:item.title];
    title.font = [NSFont systemFontOfSize:13 weight:NSFontWeightMedium];
    title.lineBreakMode = NSLineBreakByTruncatingTail;
    [title setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    cell.titleLabel = title;

    NSTextField *sub = [NSTextField labelWithString:item.subtitle];
    sub.font = [NSFont systemFontOfSize:11 weight:NSFontWeightRegular];
    sub.lineBreakMode = NSLineBreakByTruncatingTail;
    [sub setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    cell.subtitleLabel = sub;

    NSStackView *left = MBAddStack(@[title, sub], YES, 1);
    NSMutableArray<NSView *> *rowViews = [NSMutableArray arrayWithObject:left];
    if (badgeText.length) {
        NSTextField *badge = [NSTextField labelWithString:badgeText];
        badge.font = [NSFont systemFontOfSize:11 weight:NSFontWeightMedium];
        [badge setContentCompressionResistancePriority:1000 forOrientation:NSLayoutConstraintOrientationHorizontal];
        [badge setContentHuggingPriority:1000 forOrientation:NSLayoutConstraintOrientationHorizontal];
        NSStackView *pill = MBAddStack(@[badge], NO, 0);
        pill.edgeInsets = NSEdgeInsetsMake(2, 6, 2, 6);
        pill.wantsLayer = YES;
        pill.layer.cornerRadius = 4;
        [pill setContentCompressionResistancePriority:1000 forOrientation:NSLayoutConstraintOrientationHorizontal];
        [pill setContentHuggingPriority:1000 forOrientation:NSLayoutConstraintOrientationHorizontal];
        cell.badgeLabel = badge;
        cell.badgePill = pill;

        NSView *spacer = [NSView new];
        [spacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
        [rowViews addObject:spacer];
        [rowViews addObject:pill];
    }

    NSStackView *container = MBAddStack(rowViews, NO, 8);
    container.edgeInsets = NSEdgeInsetsMake(4, 6, 4, 6);
    container.translatesAutoresizingMaskIntoConstraints = NO;
    [cell addSubview:container];
    [NSLayoutConstraint activateConstraints:@[
        [container.leadingAnchor constraintEqualToAnchor:cell.leadingAnchor],
        [container.trailingAnchor constraintEqualToAnchor:cell.trailingAnchor],
        [container.topAnchor constraintEqualToAnchor:cell.topAnchor],
        [container.bottomAnchor constraintEqualToAnchor:cell.bottomAnchor]
    ]];
    [cell applyColors];
    return cell;
}

- (void)dealloc {
    [self.browser stopBrowsing];
    self.browser.updated = nil;
    [self.activeMaster closeSynchronously:YES];
}

@end
