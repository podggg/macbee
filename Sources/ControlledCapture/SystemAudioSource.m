#import "SystemAudioSource.h"
#include <libproc.h>
#include <unistd.h>

@interface MBSystemAudioSource ()
@property(nonatomic,readwrite) AudioObjectID excludedRenderer;
@property(nonatomic,copy) NSArray<NSDictionary *> *ownedChain;
@end

static BOOL fail(NSError **error) {
    if(error)*error=[NSError errorWithDomain:@"MacbeeSystemAudioSource" code:1
        userInfo:@{NSLocalizedDescriptionKey:@"The owned local speaker renderer changed. Stop before reconnecting."}];
    return NO;
}
static NSDictionary *identity(pid_t pid) {
    struct proc_bsdinfo info={0};char path[PROC_PIDPATHINFO_MAXSIZE]={0};
    if(pid<=0 || proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&info,sizeof(info))!=sizeof(info) ||
       info.pbi_pid!=(uint32_t)pid || info.pbi_uid!=getuid() || info.pbi_ruid!=getuid() ||
       !info.pbi_start_tvsec || proc_pidpath(pid,path,sizeof(path))<=0)return nil;
    NSString *executable=[[NSString stringWithUTF8String:path] stringByResolvingSymlinksInPath];
    if(!executable.isAbsolutePath)return nil;
    return @{@"pid":@(pid),@"parent":@(info.pbi_ppid),@"seconds":@(info.pbi_start_tvsec),
             @"microseconds":@(info.pbi_start_tvusec),@"path":executable};
}
static AudioObjectID audioProcess(pid_t pid) {
    AudioObjectID object=0;UInt32 size=sizeof(object);
    AudioObjectPropertyAddress address={kAudioHardwarePropertyTranslatePIDToProcessObject,
        kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    if(AudioObjectGetPropertyData(kAudioObjectSystemObject,&address,sizeof(pid),&pid,&size,&object) ||
       size!=sizeof(object) || object==kAudioObjectUnknown)return 0;
    address.mSelector=kAudioProcessPropertyPID;pid_t actual=0;size=sizeof(actual);
    if(AudioObjectGetPropertyData(object,&address,0,NULL,&size,&actual) || size!=sizeof(actual) || actual!=pid)return 0;
    return object;
}
@implementation MBSystemAudioSource
- (instancetype)initWithoutLocalRendererForOwnedBackendPID:(pid_t)backend error:(NSError **)error {
    self=[super init];if(!self)return nil;NSDictionary *b=identity(backend);
    if(!b || [b[@"parent"] intValue]!=getpid()){fail(error);return nil;}
    self.ownedChain=@[b];self.excludedRenderer=0;return self;
}
- (instancetype)initWithOwnedBackendPID:(pid_t)backend guardianPID:(pid_t)guardian
                           rendererPID:(pid_t)renderer rendererExecutable:(NSString *)executable error:(NSError **)error {
    self=[super init];if(!self)return nil;
    NSDictionary *b=identity(backend),*g=identity(guardian),*r=identity(renderer);
    NSString *expected=executable.stringByResolvingSymlinksInPath;
    if(!b || !g || !r || backend==guardian || guardian==renderer || backend==renderer ||
       [b[@"parent"] intValue]!=getpid() || [g[@"parent"] intValue]!=backend || [r[@"parent"] intValue]!=guardian ||
       !expected.isAbsolutePath || ![r[@"path"] isEqual:expected] ||
       [g[@"seconds"] unsignedLongLongValue]<[b[@"seconds"] unsignedLongLongValue] ||
       [r[@"seconds"] unsignedLongLongValue]<[g[@"seconds"] unsignedLongLongValue]){fail(error);return nil;}
    self.ownedChain=@[b,g,r];self.excludedRenderer=audioProcess(renderer);
    if(!self.excludedRenderer || ![self revalidate:error]){fail(error);return nil;}
    return self;
}
- (BOOL)revalidate:(NSError **)error {
    if(self.ownedChain.count!=1 && self.ownedChain.count!=3)return fail(error);
    if(self.ownedChain.count==3 && !self.excludedRenderer)return fail(error);
    for(NSDictionary *saved in self.ownedChain)
        if(![identity([saved[@"pid"] intValue]) isEqual:saved])return fail(error);
    if(self.ownedChain.count==1)return self.excludedRenderer==0 ? YES : fail(error);
    pid_t renderer=[self.ownedChain.lastObject[@"pid"] intValue];
    return audioProcess(renderer)==self.excludedRenderer ? YES : fail(error);
}
- (CATapDescription *)inactiveDescription:(NSError **)error {
    if(![self revalidate:error])return nil;
    CATapDescription *description=[[CATapDescription alloc]
        initStereoGlobalTapButExcludeProcesses:self.excludedRenderer?@[@(self.excludedRenderer)]:@[]];
    description.name=@"Macbee system audio excluding owned local renderer";
    description.privateTap=YES;description.exclusive=YES;
    description.muteBehavior=CATapMutedWhenTapped;
    if(@available(macOS 26.0,*))description.processRestoreEnabled=NO;
    return description;
}
@end
