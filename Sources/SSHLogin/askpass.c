// Only answer one prompt from the user-authorized, owned system SSH child.
// stdout is OpenSSH's private response pipe, never a terminal or a log file.
#include "AskpassWire.h"
#include <fcntl.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <limits.h>
int main(int argc,char **argv) {
    struct rlimit no_core={0,0}; (void)setrlimit(RLIMIT_CORE,&no_core);
    const char *path=getenv("MACBEE_ASKPASS_SOCKET"), *owner=getenv("MACBEE_ASKPASS_OWNER");
    char *end=NULL; errno=0; long id=owner?strtol(owner,&end,10):0;
    if (argc!=2 || !path || !owner || !*owner || !end || *end || errno || id<2 || id>INT_MAX) return 1;
    if (!argv[1] || !*argv[1] || strlen(argv[1])>MB_PROMPT_MAX) return 1;
    struct stat pipecheck, socketcheck;
    if (fstat(STDOUT_FILENO,&pipecheck) || !S_ISFIFO(pipecheck.st_mode) ||
        lstat(path,&socketcheck) || !S_ISSOCK(socketcheck.st_mode) ||
        socketcheck.st_uid!=getuid() || (socketcheck.st_mode & 0777)!=0600) return 1;
    struct sockaddr_un a={0}; if (strlen(path)>=sizeof(a.sun_path)) return 1;
    a.sun_family=AF_UNIX;strlcpy(a.sun_path,path,sizeof(a.sun_path));
    int fd=socket(AF_UNIX,SOCK_STREAM,0),one=1; if (fd<0) return 1;
    if (setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof(one)) ||
        fcntl(fd,F_SETFL,O_NONBLOCK)) {close(fd);return 1;}
    if (connect(fd,(struct sockaddr *)&a,sizeof(a))) {
        if(errno!=EINPROGRESS) {close(fd);return 1;}
        struct pollfd p={.fd=fd,.events=POLLOUT};int error=0;socklen_t size=sizeof(error);
        if(poll(&p,1,3000)<=0 || getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&size) || error){close(fd);return 1;}
    }
    pid_t server=0;
    if (mb_peer_pid(fd,&server) || server!=(pid_t)id) {close(fd);return 1;}
    uint64_t deadline=mb_now_ms()+300000;
    const char *hint=getenv("SSH_ASKPASS_PROMPT");
    // Unknown generic confirmations and notifications are not password prompts.
    if (hint && *hint && strcmp(hint,"confirm") && strcmp(hint,"none")) {close(fd);return 1;}
    uint32_t header[2]={htonl(hint&&*hint?(!strcmp(hint,"confirm")?1:2):0),htonl((uint32_t)strlen(argv[1]))};
    unsigned char answer[MB_SECRET_MAX+1]={0};int result=1;uint32_t bytes=0;
    if (!mb_io(fd,header,sizeof(header),1,deadline) &&
        !mb_io(fd,argv[1],strlen(argv[1]),1,deadline) &&
        !mb_io(fd,&bytes,sizeof(bytes),0,deadline)) {
        bytes=ntohl(bytes);
        if (bytes>0 && bytes<=MB_SECRET_MAX && !mb_io(fd,answer,bytes,0,deadline) &&
            !memchr(answer,'\n',bytes) && !memchr(answer,'\r',bytes) && !memchr(answer,0,bytes)) {
            answer[bytes]='\n';size_t done=0;
            while(done<bytes+1U){ssize_t n=write(STDOUT_FILENO,answer+done,bytes+1-done);if(n<0&&errno==EINTR)continue;if(n<=0)break;done+=(size_t)n;}
            if(done==bytes+1U)result=0;
        }
    }
    mb_wipe(answer,sizeof(answer));close(fd);return result;
}
