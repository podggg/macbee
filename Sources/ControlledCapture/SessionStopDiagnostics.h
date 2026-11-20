/* Fixed-size, content-free latest/last-failure snapshots. No periodic disk IO. */
#import <Foundation/Foundation.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <errno.h>
static inline BOOL MBWriteStopSnapshot(NSString *directory,NSDictionary *record,BOOL failed) {
    NSData *data=[NSJSONSerialization dataWithJSONObject:record options:NSJSONWritingSortedKeys error:nil];
    if(!data || data.length>8192)return NO;
    int dir=open(directory.fileSystemRepresentation,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0)return NO;
    struct stat info;BOOL ok=fstat(dir,&info)==0 && S_ISDIR(info.st_mode) && info.st_uid==getuid() && (info.st_mode&0777)==0700;
    if(!ok){close(dir);return NO;}
    const char *names[]={"latest-native.json","last-native-failure.json"};
    for(unsigned i=0;i<(failed?2U:1U);i++){
        NSString *temporary=[@".snapshot-" stringByAppendingString:NSUUID.UUID.UUIDString];
        int out=openat(dir,temporary.UTF8String,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
        if(out<0){ok=NO;break;}
        const uint8_t *bytes=data.bytes;size_t left=data.length;
        while(left){ssize_t n=write(out,bytes,left);if(n<0 && errno==EINTR)continue;if(n<=0){ok=NO;break;}bytes+=n;left-=(size_t)n;}
        close(out);
        if(ok && renameat(dir,temporary.UTF8String,dir,names[i])!=0)ok=NO;
        if(!ok){unlinkat(dir,temporary.UTF8String,0);break;}
    }
    close(dir);return ok;
}
