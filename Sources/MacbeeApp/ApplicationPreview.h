#import "../ControlledCapture/ApplicationSession.h"
/* Offline fixtures only. No lookup of real processes, capture or networking. */
NSArray<MBApplicationSource *> *MBPreviewSources(void);
@interface MBApplicationPreviewSession : MBApplicationSession
@end
