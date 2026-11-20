#import "ApplicationSource.h"
#include <libproc.h>
#include <unistd.h>
@interface MBApplicationSource ()
@property(nonatomic,readwrite) pid_t pid;
@property(nonatomic,readwrite) AudioObjectID audioProcess;
@property(nonatomic,readwrite,copy) NSArray<NSNumber *> *audioProcesses;
@property(nonatomic,readwrite,copy) NSString *bundleID;
@property(nonatomic,readwrite,copy) NSString *name;
@property(nonatomic,readwrite,copy) NSDate *launchDate;
@property(nonatomic,copy) NSArray<NSDictionary *> *members;
@property(nonatomic,copy) NSDictionary *ownerIdentity;
@end
static BOOL sourceFail(NSError **error,NSString *message) {
    if(error)*error=[NSError errorWithDomain:@"MacbeeApplicationSource" code:1 userInfo:@{NSLocalizedDescriptionKey:message}];return NO;
}
static BOOL scalar(AudioObjectID object,AudioObjectPropertySelector selector,void *result,UInt32 size) {
    AudioObjectPropertyAddress address={selector,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    UInt32 actual=size;return AudioObjectGetPropertyData(object,&address,0,NULL,&actual,result)==noErr && actual==size;
}
static NSString *bundleID(AudioObjectID object) {
    CFStringRef value=NULL;if(!scalar(object,kAudioProcessPropertyBundleID,&value,sizeof(value)) || !value)return nil;
    return CFBridgingRelease(value);
}
static NSArray<NSNumber *> *audioObjects(NSError **error) {
    AudioObjectPropertyAddress address={kAudioHardwarePropertyProcessObjectList,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    UInt32 size=0;
    if(AudioObjectGetPropertyDataSize(kAudioObjectSystemObject,&address,0,NULL,&size) || size%sizeof(AudioObjectID) || size>4096*sizeof(AudioObjectID)){
        sourceFail(error,@"The audio app list is unavailable.");return nil;
    }
    if(!size)return @[];
    NSMutableData *data=[NSMutableData dataWithLength:size];
    if(AudioObjectGetPropertyData(kAudioObjectSystemObject,&address,0,NULL,&size,data.mutableBytes) || size>data.length || size%sizeof(AudioObjectID)){
        sourceFail(error,@"The audio app list changed. Try again.");return nil;
    }
    NSMutableArray *result=[NSMutableArray array];const AudioObjectID *objects=data.bytes;
    for(size_t i=0;i<size/sizeof(AudioObjectID);i++)[result addObject:@(objects[i])];return result;
}
/* Public kernel identity only: no argv, environment, windows, URLs or media.
 * A PID alone is not an identity. Retain its start time and resolved executable.
 */
static NSDictionary *processIdentity(pid_t pid) {
    struct proc_bsdinfo info={0};char path[PROC_PIDPATHINFO_MAXSIZE]={0};
    if(pid<=0 || proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&info,sizeof(info))!=sizeof(info) || info.pbi_pid!=(uint32_t)pid ||
       info.pbi_uid!=getuid() || info.pbi_ruid!=getuid() || !info.pbi_start_tvsec || proc_pidpath(pid,path,sizeof(path))<=0)return nil;
    NSString *executable=[[NSString stringWithUTF8String:path] stringByResolvingSymlinksInPath];
    if(!executable.isAbsolutePath)return nil;
    return @{@"pid":@(pid),@"parent":@(info.pbi_ppid),@"seconds":@(info.pbi_start_tvsec),
             @"microseconds":@(info.pbi_start_tvusec),@"path":executable};
}
static BOOL regularApp(NSRunningApplication *app) {
    return app && !app.terminated && app.activationPolicy==NSApplicationActivationPolicyRegular &&
        app.bundleIdentifier.length && app.launchDate && ![app.bundleIdentifier hasPrefix:@"io.github.podggg.macbee"];
}
/* One resolver for bundled apps, not a provider table. A direct-child kernel
 * relationship plus a canonical nested executable and exact bundle identity
 * establish ownership. Unbundled/reparented helpers are not guessed. */
static NSDictionary *applicationOwner(NSRunningApplication *app) {
    if(!regularApp(app))return nil;
    NSDictionary *identity=processIdentity(app.processIdentifier);if(!identity)return nil;
    NSString *root=app.bundleURL.URLByResolvingSymlinksInPath.path;
    NSString *executable=app.executableURL.URLByResolvingSymlinksInPath.path;
    if(!identity || !root.length || !executable.length || ![identity[@"path"] isEqual:executable] ||
       ![executable hasPrefix:[root stringByAppendingString:@"/Contents/MacOS/"]])return nil;
    NSMutableDictionary *result=[identity mutableCopy];result[@"root"]=root;result[@"bundle"]=app.bundleIdentifier;return result;
}
static BOOL belongsToApplication(NSDictionary *identity,NSString *audioBundle,NSDictionary *owner) {
    if(!identity || !owner || !audioBundle.length)return NO;
    if([identity[@"pid"] isEqual:owner[@"pid"]])return [audioBundle isEqual:owner[@"bundle"]] && [identity[@"path"] isEqual:owner[@"path"]];
    // Supported route: the application's direct child audio service, not arbitrary
    // descendants, another application instance, a name prefix or a bundle wildcard.
    if(![identity[@"parent"] isEqual:owner[@"pid"]] ||
       [identity[@"seconds"] unsignedLongLongValue]<[owner[@"seconds"] unsignedLongLongValue])return NO;
    NSString *path=identity[@"path"],*root=owner[@"root"];
    if(![path hasPrefix:[root stringByAppendingString:@"/Contents/"]])return NO;
    NSString *helper=[path stringByDeletingLastPathComponent];
    while(helper.length>root.length && ![helper.pathExtension isEqual:@"app"] && ![helper.pathExtension isEqual:@"xpc"])
        helper=helper.stringByDeletingLastPathComponent;
    if(helper.length<=root.length)return NO;
    NSBundle *bundle=[NSBundle bundleWithPath:helper];NSString *helperID=bundle.bundleIdentifier;
    if(!helperID.length || ![bundle.executableURL.URLByResolvingSymlinksInPath.path isEqual:path])return NO;
    return [audioBundle isEqual:helperID] || [audioBundle isEqual:owner[@"bundle"]];
}
static NSDictionary *member(AudioObjectID object,NSDictionary *owner,BOOL requireOutput) {
    pid_t pid=0;UInt32 active=0;
    if(!scalar(object,kAudioProcessPropertyPID,&pid,sizeof(pid)) || pid==getpid() ||
       !scalar(object,kAudioProcessPropertyIsRunningOutput,&active,sizeof(active)) || (requireOutput && !active))return nil;
    NSDictionary *identity=processIdentity(pid);NSString *identifier=bundleID(object);
    if(!belongsToApplication(identity,identifier,owner))return nil;
    return @{@"object":@(object),@"identity":identity,@"bundle":identifier};
}
@implementation MBApplicationSource
+ (NSArray<MBApplicationSource *> *)availableSourcesWithError:(NSError **)error {
    NSArray<NSNumber *> *objects=audioObjects(error);if(!objects)return @[];
    NSMutableArray *sources=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
    for(NSNumber *number in objects) {
        AudioObjectID object=number.unsignedIntValue;pid_t pid=0;UInt32 active=0;
        if(!scalar(object,kAudioProcessPropertyPID,&pid,sizeof(pid)) || pid<=0 || pid==getpid() ||
           !scalar(object,kAudioProcessPropertyIsRunningOutput,&active,sizeof(active)) || !active)continue;
        NSRunningApplication *app=[NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        NSString *identifier=bundleID(object);
        NSDictionary *identity=processIdentity(pid);
        NSRunningApplication *parent=identity?[NSRunningApplication runningApplicationWithProcessIdentifier:[identity[@"parent"] intValue]]:nil;
        NSDictionary *parentIdentity=applicationOwner(parent);
        if(belongsToApplication(identity,identifier,parentIdentity))app=parent;
        else if(!regularApp(app) || (identity && [app.bundleURL.URLByResolvingSymlinksInPath.path containsString:@".app/"]))continue;
        if(!regularApp(app) || [seen containsObject:@(app.processIdentifier)])continue;
        MBApplicationSource *source=[self new];source.pid=app.processIdentifier;
        source.bundleID=app.bundleIdentifier;source.name=app.localizedName?:app.bundleIdentifier;source.launchDate=app.launchDate;
        NSDictionary *owner=applicationOwner(app);
        if(owner){
            NSMutableArray *members=[NSMutableArray array],*ids=[NSMutableArray array];
            for(NSNumber *candidate in objects){NSDictionary *m=member(candidate.unsignedIntValue,owner,YES);if(m){[members addObject:m];[ids addObject:candidate];}}
            if(!members.count || members.count>16)continue;
            source.ownerIdentity=owner;source.members=members;source.audioProcesses=ids;
            source.audioProcess=[ids.firstObject unsignedIntValue];
            if(pid!=app.processIdentifier || members.count>1)source.name=[source.name stringByAppendingString:@" (all app audio)"];
        }else{
            if(pid!=app.processIdentifier || ![identifier isEqual:app.bundleIdentifier])continue;
            source.audioProcess=object;source.audioProcesses=@[@(object)];
        }
        // Recheck immediately so a partially changed enumeration never becomes a selection.
        if(![source revalidateRequiringOutput:YES error:nil])continue;
        [sources addObject:source];[seen addObject:@(source.pid)];
    }
    return [sources sortedArrayUsingComparator:^NSComparisonResult(MBApplicationSource *a,MBApplicationSource *b){return [a.name localizedCaseInsensitiveCompare:b.name];}];
}
- (BOOL)revalidateRequiringOutput:(BOOL)output error:(NSError **)error {
    NSRunningApplication *app=[NSRunningApplication runningApplicationWithProcessIdentifier:self.pid];
    if(self.pid<=0 || self.pid==getpid() || !regularApp(app) ||
       ![app.bundleIdentifier isEqual:self.bundleID] || ![app.launchDate isEqual:self.launchDate])
        return sourceFail(error,@"The selected app changed or quit. Choose it again.");
    if(self.ownerIdentity){
        if(![applicationOwner(app) isEqual:self.ownerIdentity])return sourceFail(error,@"The selected app identity changed. Choose it again.");
        BOOL anyOutput=NO;
        for(NSDictionary *saved in self.members){
            AudioObjectID object=[saved[@"object"] unsignedIntValue];NSDictionary *current=member(object,self.ownerIdentity,NO);
            pid_t pid=[saved[@"identity"][@"pid"] intValue];AudioObjectID translated=0;UInt32 size=sizeof(translated),active=0;
            AudioObjectPropertyAddress address={kAudioHardwarePropertyTranslatePIDToProcessObject,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
            if(![current isEqual:saved] || AudioObjectGetPropertyData(kAudioObjectSystemObject,&address,sizeof(pid),&pid,&size,&translated) ||
               size!=sizeof(translated) || translated!=object)return sourceFail(error,@"The app audio process changed. Choose it again.");
            if(scalar(object,kAudioProcessPropertyIsRunningOutput,&active,sizeof(active)) && active)anyOutput=YES;
        }
        NSArray *objects=audioObjects(error);if(!objects)return NO;
        for(NSNumber *object in objects){
            if(member(object.unsignedIntValue,self.ownerIdentity,YES) && ![self.audioProcesses containsObject:object])
                return sourceFail(error,@"The app added an audio process. Stop and choose it again.");
        }
        if(output && !anyOutput)return sourceFail(error,@"Start audio in the selected app, then try again.");
        return YES;
    }
    pid_t actual=0;UInt32 active=0;AudioObjectID translated=0;UInt32 size=sizeof(translated);pid_t pid=self.pid;
    AudioObjectPropertyAddress address={kAudioHardwarePropertyTranslatePIDToProcessObject,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    if(AudioObjectGetPropertyData(kAudioObjectSystemObject,&address,sizeof(pid),&pid,&size,&translated) ||
       size!=sizeof(translated) || translated!=self.audioProcess || !scalar(self.audioProcess,kAudioProcessPropertyPID,&actual,sizeof(actual)) ||
       actual!=self.pid || ![bundleID(self.audioProcess) isEqual:self.bundleID])
        return sourceFail(error,@"The selected audio process changed. Choose it again.");
    if(output && (!scalar(self.audioProcess,kAudioProcessPropertyIsRunningOutput,&active,sizeof(active)) || !active))
        return sourceFail(error,@"Start audio in the selected app, then try again.");
    return YES;
}
@end
