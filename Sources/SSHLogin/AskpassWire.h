// Private local framing for an OpenSSH askpass reply. No logging or persistence.
#ifndef MACBEE_ASKPASS_WIRE_H
#define MACBEE_ASKPASS_WIRE_H
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

enum { MB_PROMPT_MAX=4096, MB_SECRET_MAX=1000 };
static inline uint64_t mb_now_ms(void) {
    struct timespec t; if (clock_gettime(CLOCK_MONOTONIC,&t)) return 0;
    return (uint64_t)t.tv_sec*1000+(uint64_t)t.tv_nsec/1000000;
}
static inline int mb_io(int fd, void *raw, size_t len, int sending, uint64_t deadline) {
    unsigned char *data=(unsigned char *)raw;
    while (len) {
        uint64_t now=mb_now_ms(); if (!now || now>=deadline) return -1;
        struct pollfd p={.fd=fd,.events=(short)(sending?POLLOUT:POLLIN)};
        int ready=poll(&p,1,(int)(deadline-now)); if (ready<0 && errno==EINTR) continue;
        if (ready<=0 || (p.revents&POLLNVAL)) return -1;
        ssize_t n=sending?send(fd,data,len,MSG_DONTWAIT):recv(fd,data,len,MSG_DONTWAIT);
        if (n<0 && (errno==EINTR || errno==EAGAIN)) continue;
        if (n<=0) return -1;
        data+=n;len-=(size_t)n;
    }
    return 0;
}
static inline int mb_peer_pid(int fd, pid_t *out) {
    uid_t u;gid_t g;socklen_t n=sizeof(*out);
    if (getpeereid(fd,&u,&g) || u!=getuid()) return -1;
    return getsockopt(fd,SOL_LOCAL,LOCAL_PEERPID,out,&n) || n!=sizeof(*out) || *out<=1 ? -1:0;
}
static inline void mb_wipe(void *raw, size_t len) {
    volatile unsigned char *p=raw;while(len--) *p++=0;
}
#endif
