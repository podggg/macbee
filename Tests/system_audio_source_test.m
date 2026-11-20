/* Every metadata lookup is interposed. Creating a tap or starting IO aborts. */
#import "../Sources/ControlledCapture/SystemAudioSource.h"
#import <CoreAudio/AudioHardwareTapping.h>
#include <libproc.h>
#include <assert.h>
#include <string.h>
#include <unistd.h>
static int mode=0;
int proc_pidinfo(int pid,int flavor,uint64_t arg,void *buffer,int size){
    (void)arg;assert(flavor==PROC_PIDTBSDINFO && size==sizeof(struct proc_bsdinfo));
    if(pid<1001 || pid>1003)return 0;
    struct proc_bsdinfo info={0};info.pbi_pid=pid;info.pbi_uid=info.pbi_ruid=getuid();
    info.pbi_ppid=pid==1001?(uint32_t)getpid():(uint32_t)pid-1;info.pbi_start_tvsec=100+(pid-1001);
    if(mode==1 && pid==1003)info.pbi_start_tvusec=7; // Same PID, different lifetime.
    if(mode==2 && pid==1003)info.pbi_ppid=9000;    // Other owner's renderer.
    if(mode==3 && pid==1003)info.pbi_uid++;      // Other user.
    if(mode==6 && pid==1001)info.pbi_ppid=9000;
    if(mode==7 && pid==1001)info.pbi_start_tvusec=8;
    memcpy(buffer,&info,sizeof(info));return sizeof(info);
}
int proc_pidpath(int pid,void *buffer,uint32_t size){
    const char *path=pid==1003?"/fixture/bin/renderer":"/fixture/bin/python";
    assert(size>strlen(path));strcpy(buffer,path);return (int)strlen(path);
}
OSStatus AudioObjectGetPropertyData(AudioObjectID object,const AudioObjectPropertyAddress *address,
    UInt32 qsize,const void *qualifier,UInt32 *size,void *value){
    if(address->mSelector==kAudioHardwarePropertyTranslatePIDToProcessObject){
        assert(object==kAudioObjectSystemObject && qsize==sizeof(pid_t) && *(pid_t *)qualifier==1003);
        assert(*size==sizeof(AudioObjectID));*(AudioObjectID *)value=mode==4?7002:7001;return noErr;
    }
    assert(address->mSelector==kAudioProcessPropertyPID && (object==7001 || object==7002));
    assert(*size==sizeof(pid_t));*(pid_t *)value=mode==5?9000:1003;return noErr;
}
OSStatus AudioHardwareCreateProcessTap(CATapDescription *description,AudioObjectID *tap){(void)description;(void)tap;abort();}
OSStatus AudioDeviceStart(AudioObjectID device,AudioDeviceIOProcID proc){(void)device;(void)proc;abort();}
int main(void){@autoreleasepool{
    NSError *error=nil;
    MBSystemAudioSource *source=[[MBSystemAudioSource alloc] initWithOwnedBackendPID:1001 guardianPID:1002
        rendererPID:1003 rendererExecutable:@"/fixture/bin/renderer" error:&error];
    assert(source && !error && source.excludedRenderer==7001);
    CATapDescription *d=[source inactiveDescription:&error];
    assert(d && d.privateTap && d.exclusive && d.muteBehavior==CATapMutedWhenTapped);
    assert(([d.processes isEqual:@[@7001]]));assert(d.deviceUID==nil);
    if(@available(macOS 26.0,*))assert(!d.processRestoreEnabled);
    for(mode=1;mode<=5;mode++){assert(![source revalidate:&error]);assert(![source inactiveDescription:nil]);}
    mode=0;assert([source revalidate:nil]);
    assert(![[MBSystemAudioSource alloc] initWithOwnedBackendPID:1001 guardianPID:1002 rendererPID:1003
        rendererExecutable:@"/fixture/bin/another-renderer" error:nil]);
    for(mode=2;mode<=3;mode++)assert(![[MBSystemAudioSource alloc] initWithOwnedBackendPID:1001 guardianPID:1002
        rendererPID:1003 rendererExecutable:@"/fixture/bin/renderer" error:nil]);
    mode=0;MBSystemAudioSource *remoteOnly=[[MBSystemAudioSource alloc] initWithoutLocalRendererForOwnedBackendPID:1001 error:nil];
    assert(remoteOnly && remoteOnly.excludedRenderer==0 && [remoteOnly inactiveDescription:nil].processes.count==0);
    mode=6;assert(![remoteOnly revalidate:nil]);assert(![[MBSystemAudioSource alloc] initWithoutLocalRendererForOwnedBackendPID:1001 error:nil]);
    mode=7;assert(![remoteOnly revalidate:nil]);
    puts("system descriptor and owned renderer identity verified; no live metadata, HAL mutation or audio");
}return 0;}
