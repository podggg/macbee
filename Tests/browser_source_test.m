/* All process/HAL/app/bundle observations are in-memory fixtures. No live lookup. */
#import "../Sources/ControlledCapture/ApplicationSource.h"
#import <objc/runtime.h>
#include <libproc.h>
#include <unistd.h>
#include <assert.h>
@interface FixtureApp : NSObject
@property pid_t processIdentifier;
@property(getter=isTerminated) BOOL terminated;
@property NSApplicationActivationPolicy activationPolicy;
@property NSString *bundleIdentifier,*localizedName;
@property NSDate *launchDate;
@property NSURL *bundleURL,*executableURL;
@end
@implementation FixtureApp @end
@interface FixtureBundle : NSObject
@property NSString *bundleIdentifier;
@property NSURL *executableURL;
@end
@implementation FixtureBundle @end
static NSMutableDictionary *apps,*processes,*audio;
static NSString *root,*ownerID,*helperID,*ownerExecutable;
static NSString *helperPath;
static BOOL failList=NO;
static id lookup(id cls,SEL cmd,pid_t pid){(void)cls;(void)cmd;return apps[@(pid)];}
static id bundle(id cls,SEL cmd,NSString *path){
    (void)cls;(void)cmd;
    if(![path isEqual:[helperPath stringByDeletingLastPathComponent].stringByDeletingLastPathComponent.stringByDeletingLastPathComponent])return nil;
    FixtureBundle *b=[FixtureBundle new];b.bundleIdentifier=helperID;b.executableURL=[NSURL fileURLWithPath:helperPath];return b;
}
int proc_pidinfo(int pid,int flavor,uint64_t arg,void *buffer,int size){
    (void)arg;NSDictionary *p=processes[@(pid)];
    if(!p || flavor!=PROC_PIDTBSDINFO || size!=sizeof(struct proc_bsdinfo))return 0;
    struct proc_bsdinfo *b=buffer;memset(b,0,size);b->pbi_pid=pid;b->pbi_ppid=[p[@"parent"] intValue];
    b->pbi_uid=[p[@"uid"] unsignedIntValue];b->pbi_ruid=b->pbi_uid;
    b->pbi_start_tvsec=[p[@"start"] unsignedLongLongValue];b->pbi_start_tvusec=1;return size;
}
int proc_pidpath(int pid,void *buffer,uint32_t size){
    NSString *path=processes[@(pid)][@"path"];if(!path)return 0;
    if(strlen(path.UTF8String)>=size)return 0;strcpy(buffer,path.UTF8String);return (int)strlen(buffer);
}
OSStatus AudioObjectGetPropertyDataSize(AudioObjectID object,const AudioObjectPropertyAddress *address,UInt32 qsize,const void *q,UInt32 *size){
    (void)object;(void)qsize;(void)q;
    if(failList || address->mSelector!=kAudioHardwarePropertyProcessObjectList)return -1;
    *size=(UInt32)audio.count*sizeof(AudioObjectID);return noErr;
}
OSStatus AudioObjectGetPropertyData(AudioObjectID object,const AudioObjectPropertyAddress *address,UInt32 qsize,const void *q,UInt32 *size,void *data){
    (void)qsize;NSDictionary *a=audio[@(object)];
    switch(address->mSelector){
      case kAudioHardwarePropertyProcessObjectList:{NSUInteger n=0;for(NSNumber *key in [audio.allKeys sortedArrayUsingSelector:@selector(compare:)])((AudioObjectID *)data)[n++]=key.unsignedIntValue;*size=(UInt32)n*sizeof(AudioObjectID);return noErr;}
      case kAudioHardwarePropertyTranslatePIDToProcessObject:
        for(NSNumber *key in audio)if([audio[key][@"pid"] intValue]==*(const pid_t *)q){*(AudioObjectID *)data=key.unsignedIntValue;return noErr;}return -1;
      case kAudioProcessPropertyPID:if(!a)return -1;*(pid_t *)data=[a[@"pid"] intValue];*size=sizeof(pid_t);return noErr;
      case kAudioProcessPropertyIsRunningOutput:if(!a)return -1;*(UInt32 *)data=[a[@"active"] unsignedIntValue];*size=sizeof(UInt32);return noErr;
      case kAudioProcessPropertyBundleID:if(!a)return -1;*(CFStringRef *)data=CFStringCreateCopy(NULL,(__bridge CFStringRef)a[@"bundle"]);*size=sizeof(CFStringRef);return noErr;
      default:return -1;
    }
}
static void proc(int pid,int parent,NSString *path){processes[@(pid)]=[@{@"parent":@(parent),@"path":path,@"start":@(pid),@"uid":@(getuid())} mutableCopy];}
static void object(int obj,int pid,NSString *bid){audio[@(obj)]=[@{@"pid":@(pid),@"bundle":bid,@"active":@1} mutableCopy];}
static void owner(int pid){
    FixtureApp *a=[FixtureApp new];a.processIdentifier=pid;a.activationPolicy=NSApplicationActivationPolicyRegular;
    a.bundleIdentifier=ownerID;a.localizedName=@"Chrome";a.launchDate=[NSDate dateWithTimeIntervalSince1970:pid];
    a.bundleURL=[NSURL fileURLWithPath:root];a.executableURL=[a.bundleURL URLByAppendingPathComponent:[@"Contents/MacOS/" stringByAppendingString:ownerExecutable]];
    apps[@(pid)]=a;proc(pid,1,a.executableURL.path);
}
static MBApplicationSource *selected(void){for(MBApplicationSource *s in [MBApplicationSource availableSourcesWithError:nil])if(s.pid==100)return s;return nil;}
int main(void){@autoreleasepool{
    BOOL dia=getenv("MACBEE_DIA_FIXTURE")!=NULL;BOOL generic=getenv("MACBEE_GENERIC_FIXTURE")!=NULL;
    root=dia?@"/Fixture/Dia.app":@"/Fixture/Google Chrome.app";
    ownerID=dia?@"company.thebrowser.dia":@"com.google.Chrome";
    helperID=dia?@"company.thebrowser.browser.helper":@"com.google.Chrome.helper";
    ownerExecutable=dia?@"Dia":@"Google Chrome";
    if(generic){root=@"/Fixture/Unlisted Player.app";ownerID=@"test.unlisted.app";helperID=@"test.unrelated.helper.id";ownerExecutable=@"Unlisted Player";}
    apps=[NSMutableDictionary dictionary];processes=[NSMutableDictionary dictionary];audio=[NSMutableDictionary dictionary];
    helperPath=[root stringByAppendingPathComponent:dia?@"Contents/Frameworks/ArcCore.framework/Versions/A/Helpers/Browser Helper (Aperitif).app/Contents/MacOS/Browser Helper (Aperitif)":@"Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper"];
    if(generic)helperPath=[root stringByAppendingPathComponent:@"Contents/Frameworks/Other Engine.framework/Services/Audio Worker.xpc/Contents/MacOS/Audio Worker"];
    Method appMethod=class_getClassMethod(NSRunningApplication.class,@selector(runningApplicationWithProcessIdentifier:));
    Method bundleMethod=class_getClassMethod(NSBundle.class,@selector(bundleWithPath:));
    IMP oldApp=method_setImplementation(appMethod,(IMP)lookup),oldBundle=method_setImplementation(bundleMethod,(IMP)bundle);
    owner(100);owner(200);proc(111,100,helperPath);proc(222,200,helperPath);
    object(42,111,helperID);object(43,222,helperID);
    // Even a helper advertised as regular must resolve to its browser owner.
    FixtureApp *helper=[FixtureApp new];helper.processIdentifier=111;helper.activationPolicy=NSApplicationActivationPolicyRegular;
    helper.bundleIdentifier=helperID;helper.localizedName=@"Fixture Helper";helper.bundleURL=[NSURL fileURLWithPath:[helperPath stringByDeletingLastPathComponent].stringByDeletingLastPathComponent.stringByDeletingLastPathComponent];helper.launchDate=[NSDate dateWithTimeIntervalSince1970:111];apps[@111]=helper;
    MBApplicationSource *s=selected();assert(s && s.pid==100 && [s.audioProcesses isEqual:@[@42]]);
    assert([s.name containsString:@"all app audio"]);assert([s revalidateRequiringOutput:YES error:nil]);
    // Same browser binary, different owning instance must not enter this tap.
    assert([MBApplicationSource availableSourcesWithError:nil].count==2);
    audio[@42][@"active"]=@0;assert(![s revalidateRequiringOutput:YES error:nil]);assert([s revalidateRequiringOutput:NO error:nil]);audio[@42][@"active"]=@1;
    processes[@111][@"start"]=@112;assert(![s revalidateRequiringOutput:NO error:nil]);processes[@111][@"start"]=@111;
    processes[@111][@"parent"]=@200;assert(![s revalidateRequiringOutput:NO error:nil]);assert(!selected());processes[@111][@"parent"]=@100;
    processes[@111][@"uid"]=@(getuid()+1);assert(!selected());processes[@111][@"uid"]=@(getuid());
    processes[@111][@"path"]=[helperPath stringByReplacingOccurrencesOfString:root withString:[root stringByAppendingString:@".evil"]];assert(!selected());processes[@111][@"path"]=helperPath;
    audio[@42][@"bundle"]=@"org.other.browser.helper";assert(!selected());audio[@42][@"bundle"]=helperID;
    // An unrelated helper, same display name or bundle prefix, is not enough.
    proc(333,100,@"/Other/Google Chrome Helper");object(44,333,helperID);assert([selected().audioProcesses isEqual:@[@42]]);
    proc(112,100,helperPath);object(46,112,helperID);
    assert(![s revalidateRequiringOutput:NO error:nil]);MBApplicationSource *two=selected();assert(([two.audioProcesses isEqual:@[@42,@46]]));
    [audio removeObjectForKey:@46];assert(![two revalidateRequiringOutput:NO error:nil]);
    object(41,100,ownerID);assert(([selected().audioProcesses isEqual:@[@41,@42]]));[audio removeObjectForKey:@41];
    // CoreAudio may attribute a child's bundle to Chrome itself; ancestry/path still required.
    audio[@42][@"bundle"]=ownerID;assert([selected().audioProcesses isEqual:@[@42]]);audio[@42][@"bundle"]=helperID;
    failList=YES;assert(![s revalidateRequiringOutput:NO error:nil]);failList=NO;
    FixtureApp *a=apps[@100];NSDate *date=a.launchDate;a.launchDate=[date dateByAddingTimeInterval:1];assert(![s revalidateRequiringOutput:NO error:nil]);a.launchDate=date;
    a.terminated=YES;assert(![s revalidateRequiringOutput:NO error:nil]);a.terminated=NO;
    // Lost/reused HAL ID also fails, even if the browser owner is unchanged.
    audio[@42][@"pid"]=@222;assert(![s revalidateRequiringOutput:NO error:nil]);
    method_setImplementation(appMethod,oldApp);method_setImplementation(bundleMethod,oldBundle);
    puts("browser ownership/PID-reuse/path/sibling/addition/exit fixtures passed; no live metadata or capture");
}return 0;}
