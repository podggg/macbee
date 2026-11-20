// Test-only process: invoke the real askpass helper like OpenSSH would. Take
// only a digest of the synthetic expected response; never print the response.
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc!=2) return 31;
    const char *helper=getenv("SSH_ASKPASS"),*expected=getenv("FIXTURE_EXPECTED_SHA256");
    if(!helper || !expected) return 32;
    if(getenv("FIXTURE_DELAY"))usleep(350000);
    int f[2];if(pipe(f))return 33;pid_t pid=fork();
    if(pid<0)return 34;
    if(pid==0) {close(f[0]);dup2(f[1],1);close(f[1]);execl(helper,helper,argv[1],(char*)0);_exit(127);}
    close(f[1]);unsigned char b[1024]={0};size_t used=0;ssize_t got;
    while(used<sizeof(b) && (got=read(f[0],b+used,sizeof(b)-used))>0)used+=(size_t)got;
    close(f[0]);int status;while(waitpid(pid,&status,0)<0 && errno==EINTR){}
    if(!strcmp(expected,"CANCEL"))return used==0 && WIFEXITED(status) && WEXITSTATUS(status)!=0 ? 0:35;
    if(!used || b[used-1]!='\n' || !WIFEXITED(status) || WEXITSTATUS(status)!=0)return 36;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];char hex[65]={0};
    CC_SHA256(b,(CC_LONG)(used-1),digest);for(size_t i=0;i<sizeof(b);i++)((volatile unsigned char *)b)[i]=0;
    for(unsigned i=0;i<sizeof(digest);i++)snprintf(hex+2*i,3,"%02x",digest[i]);
    return strcmp(expected,hex)==0?0:37;
}
