#import <Cocoa/Cocoa.h>
#import "SSHBrowse.h"
#import "../SSHLogin/SSHControlMaster.h"

NS_ASSUME_NONNULL_BEGIN

typedef NS_ENUM(NSInteger, MBAddMacItemKind) {
    MBAddMacItemBonjour = 0,
    MBAddMacItemAlias = 1,
    MBAddMacItemManual = 2,
};

@interface MBAddMacItem : NSObject
@property(nonatomic) MBAddMacItemKind kind;
@property(nonatomic, copy) NSString *identifier;
@property(nonatomic, copy) NSString *title;
@property(nonatomic, copy) NSString *subtitle;
@property(nonatomic, copy) NSString *host;
@property(nonatomic, copy) NSString *defaultUser;
@property(nonatomic) NSInteger port;
@property(nonatomic, copy) NSString *preview;
@property(nonatomic) BOOL configured;
@property(nonatomic) BOOL isThisMac;
@end

@interface MBAddMacRowCellView : NSTableCellView
@property(nonatomic, strong) NSTextField *titleLabel;
@property(nonatomic, strong) NSTextField *subtitleLabel;
@property(nonatomic, strong, nullable) NSTextField *badgeLabel;
@property(nonatomic, strong, nullable) NSView *badgePill;
@property(nonatomic) BOOL rowDisabled;
- (void)applyColors;
@end

typedef void (^MBAddMacSetupRunner)(NSArray<NSString *> *arguments,
                                     void (^completion)(NSDictionary *result));

@interface MBAddMacSheet : NSObject <NSTableViewDataSource, NSTableViewDelegate, NSTextFieldDelegate, NSWindowDelegate>

@property(nonatomic, readonly) NSWindow *sheetWindow;
@property(nonatomic, readonly) NSTableView *tableView;
@property(nonatomic, readonly) NSTextField *nameField;
@property(nonatomic, readonly) NSTextField *usernameField;
@property(nonatomic, readonly) NSTextField *addressField;
@property(nonatomic, readonly) NSTextField *targetPreviewLabel;
@property(nonatomic, readonly) NSTextField *discoveryStatusLabel;
@property(nonatomic, readonly) NSTextField *statusLabel;
@property(nonatomic, readonly) NSButton *rememberKeychain;
@property(nonatomic, readonly) NSButton *connectButton;
@property(nonatomic, readonly) NSButton *cancelButton;
@property(nonatomic, readonly) NSArray<MBAddMacItem *> *items;

/// Defaults to MBSSHBrowse, MBSSHControlMaster, and NSProcessInfo.processInfo.hostName; overridable in tests.
@property(nonatomic, copy, nullable) MBSSHBrowse *(^browserFactory)(void);
@property(nonatomic, copy, nullable) MBSSHControlMaster *(^controlMasterFactory)(void);
@property(nonatomic, copy) NSString *localHostName;

- (instancetype)initWithSetupRunner:(MBAddMacSetupRunner)setupRunner;

/// Builds the sheet UI without showing any window (for headless AppKit layout tests).
- (void)buildSheet;

/// Presents the sheet on `parentWindow`, loads `--discover`, and starts Bonjour browsing.
- (void)presentOnWindow:(NSWindow *)parentWindow
             completion:(void (^)(NSDictionary *_Nullable addedRow,
                                  MBSSHControlMaster *_Nullable master,
                                  NSString *_Nullable notice))completion;

/// Programmatic helpers for controller updates and deterministic UI tests.
- (void)applyDiscoveryResult:(NSDictionary *)result;
- (void)applyBonjourRows:(NSArray<NSDictionary *> *)rows status:(NSString *)status;
- (BOOL)selectItemAtIndex:(NSInteger)index;
- (void)connectAndAdd:(nullable id)sender;
- (void)cancel:(nullable id)sender;
- (void)refreshControls:(nullable id)sender;

@end

NS_ASSUME_NONNULL_END
