// Exercise the production helper with synthetic descriptor metadata only.
// No network endpoints, real UID changes, SSH agent, keys or prompts.
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

static mode_t output_mode, channel_mode;
static uid_t channel_uid, peer_uid;
static pid_t peer_pid;
static int socket_calls, poll_calls;
static int fixture_fstat(int fd, struct stat *v) {assert(fd==1);memset(v,0,sizeof(*v));v->st_mode=output_mode;return 0;}
static int fixture_lstat(const char *p,struct stat *v) {(void)p;memset(v,0,sizeof(*v));v->st_mode=channel_mode;v->st_uid=channel_uid;return 0;}
static int fixture_uid(int fd,uid_t *uid,gid_t *gid) {(void)fd;*uid=peer_uid;*gid=0;return 0;}
static int fixture_pid(int fd,int level,int opt,void *p,socklen_t *n) {(void)fd;assert(level==SOL_LOCAL && opt==LOCAL_PEERPID && *n==sizeof(peer_pid));memcpy(p,&peer_pid,*n);return 0;}
static int fixture_socket(int a,int b,int c) {(void)a;(void)b;(void)c;socket_calls++;return 9;}
static int fixture_setsockopt(int a,int b,int c,const void *d,socklen_t e) {(void)a;(void)b;(void)c;(void)d;(void)e;return 0;}
static int fixture_fcntl(int a,int b,...) {(void)a;(void)b;return 0;}
static int fixture_connect(int a,const struct sockaddr *b,socklen_t c) {(void)a;(void)b;(void)c;return 0;}
static int fixture_close(int fd) {(void)fd;return 0;}
static int fixture_poll(struct pollfd *f,nfds_t n,int ms) {(void)f;(void)n;(void)ms;poll_calls++;return 0;}
static int fixture_rlimit(int a,const struct rlimit *b) {(void)a;(void)b;return 0;}
#define fstat fixture_fstat
#define lstat fixture_lstat
#define getpeereid fixture_uid
#define getsockopt fixture_pid
#define setsockopt fixture_setsockopt
#define socket fixture_socket
#define fcntl fixture_fcntl
#define connect fixture_connect
#define close fixture_close
#define poll fixture_poll
#define setrlimit fixture_rlimit
#define main askpass_entry
#include "../Sources/SSHLogin/askpass.c"
#undef main

static void setup(void) {
    output_mode=S_IFIFO|0600;channel_mode=S_IFSOCK|0600;
    channel_uid=peer_uid=getuid();peer_pid=12345;socket_calls=poll_calls=0;
}
static void check(int should_connect,int may_frame) {
    char *args[]={"fixture-helper","Synthetic prompt",NULL};
    assert(askpass_entry(2,args)!=0);
    assert(socket_calls==should_connect && poll_calls==may_frame);
}
int main(void) {
    setenv("MACBEE_ASKPASS_SOCKET","/fixture/nonexistent.sock",1);
    setenv("MACBEE_ASKPASS_OWNER","12345",1);unsetenv("SSH_ASKPASS_PROMPT");
    setup();check(1,1); // Positive control gets to I/O; the fixture never sends.
    setup();output_mode=S_IFREG|0600;check(0,0);
    setup();channel_mode=S_IFLNK|0600;check(0,0);
    setup();channel_mode=S_IFSOCK|0666;check(0,0);
    setup();channel_uid++;check(0,0);
    setup();peer_uid++;check(1,0);
    setup();peer_pid++;check(1,0);
    return 0;
}
