/* Test-process-only metadata fixtures. Never reads actual apps or audio. */
#import "../Sources/ControlledCapture/ApplicationSource.h"
#import <objc/runtime.h>
#include <assert.h>
#include <string.h>
#include <libproc.h>
@interface FakeApplication : NSObject
@property pid_t processIdentifier;
@property(getter=isTerminated) BOOL terminated;
@property NSApplicationActivationPolicy activationPolicy;
@property NSString *bundleIdentifier;
@property NSString *localizedName;
@property NSDate *launchDate;
@end
@implementation FakeApplication @end
int proc_pidinfo(int pid,int flavor,uint64_t arg,void *buffer,int size){(void)pid;(void)flavor;(void)arg;(void)buffer;(void)size;return 0;}
int proc_pidpath(int pid,void *buffer,uint32_t size){(void)pid;(void)buffer;(void)size;return 0;}
static FakeApplication *fake;static UInt32 active=1;static AudioObjectID translated=42;
static id lookup(id cls,SEL cmd,pid_t pid){(void)cls;(void)cmd;return pid==111?fake:nil;}
OSStatus AudioObjectGetPropertyDataSize(AudioObjectID object,const AudioObjectPropertyAddress *address,UInt32 qsize,const void *qualifier,UInt32 *size){
    (void)object;(void)qsize;(void)qualifier;if(address->mSelector!=kAudioHardwarePropertyProcessObjectList)return -1;*size=sizeof(AudioObjectID);return noErr;
}
OSStatus AudioObjectGetPropertyData(AudioObjectID object,const AudioObjectPropertyAddress *address,UInt32 qsize,const void *qualifier,UInt32 *size,void *data){
    (void)object;(void)qsize;(void)qualifier;
    switch(address->mSelector){
      case kAudioHardwarePropertyProcessObjectList:*(AudioObjectID *)data=42;*size=sizeof(AudioObjectID);return noErr;
      case kAudioHardwarePropertyTranslatePIDToProcessObject:*(AudioObjectID *)data=translated;return noErr;
      case kAudioProcessPropertyPID:*(pid_t *)data=111;*size=sizeof(pid_t);return noErr;
      case kAudioProcessPropertyIsRunningOutput:*(UInt32 *)data=active;*size=sizeof(active);return noErr;
      case kAudioProcessPropertyBundleID:*(CFStringRef *)data=CFStringCreateCopy(NULL,CFSTR("test.explicit.player"));*size=sizeof(CFStringRef);return noErr;
      default:return -1;
    }
}
int main(void){@autoreleasepool{
    fake=[FakeApplication new];fake.processIdentifier=111;fake.activationPolicy=NSApplicationActivationPolicyRegular;
    fake.bundleIdentifier=@"test.explicit.player";fake.localizedName=@"Fixture Player";fake.launchDate=[NSDate dateWithTimeIntervalSince1970:10];
    Method method=class_getClassMethod(NSRunningApplication.class,@selector(runningApplicationWithProcessIdentifier:));
    IMP previous=method_setImplementation(method,(IMP)lookup);
    NSArray *list=[MBApplicationSource availableSourcesWithError:nil];assert(list.count==1);
    MBApplicationSource *source=list.firstObject;assert(source.pid==111 && source.audioProcess==42);
    assert([source revalidateRequiringOutput:YES error:nil]);
    active=0;assert(![source revalidateRequiringOutput:YES error:nil]);assert([source revalidateRequiringOutput:NO error:nil]);
    assert([MBApplicationSource availableSourcesWithError:nil].count==0);active=1;
    fake.launchDate=[NSDate dateWithTimeIntervalSince1970:11];assert(![source revalidateRequiringOutput:NO error:nil]);
    fake.launchDate=source.launchDate;translated=43;assert(![source revalidateRequiringOutput:NO error:nil]);translated=42;
    fake.bundleIdentifier=@"unrelated.browser.helper";assert(![source revalidateRequiringOutput:NO error:nil]);
    assert([MBApplicationSource availableSourcesWithError:nil].count==0);fake.bundleIdentifier=source.bundleID;
    fake.activationPolicy=NSApplicationActivationPolicyProhibited;assert([MBApplicationSource availableSourcesWithError:nil].count==0);
    fake.activationPolicy=NSApplicationActivationPolicyRegular;fake.terminated=YES;assert(![source revalidateRequiringOutput:NO error:nil]);
    method_setImplementation(method,previous);
    puts("identity/output/exit/PID-reuse/helper-mismatch fixtures passed; no live metadata or capture");
}return 0;}
