#include "pcm_ring.h"
#include <assert.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
static mb_pcm_ring ring;
static float storage[128];
static void *producer(void *unused) {
    (void)unused;
    for(int i=1;i<=100000;i++) {
        float frame[2]={(float)i,(float)-i};
        while(!mb_pcm_ring_push(&ring,frame,1)) sched_yield();
    }
    return NULL;
}
static void stamped_capture_blocks(void) {
    mb_pcm_ring r;float pcm[128]={0},out[128];uint64_t stamps[64],first=0;
    const uint64_t base=4000000000000ULL;
    assert(mb_pcm_ring_init(&r,pcm,64));r.capture_ns=stamps;
    // Real wire times at 48 kHz, with an intentionally missing 5 ms callback.
    assert(!mb_pcm_ring_push_timed(&r,storage,24,0));
    assert(mb_pcm_ring_push_timed(&r,storage,24,base));
    assert(mb_pcm_ring_push_timed(&r,storage,24,base+5500000));
    assert(mb_pcm_ring_pop_timed(&r,out,12,&first)==12 && first==base);
    assert(mb_pcm_ring_pop_timed(&r,out,20,&first)==12 && first==base+250000);
    assert(mb_pcm_ring_trim(&r,18,12)==12);
    assert(mb_pcm_ring_pop_timed(&r,out,64,&first)==12 && first==base+5750000);
    // Published data and timestamps straddle the physical end of the ring.
    for(unsigned k=0;k<5;k++){
        assert(mb_pcm_ring_push_timed(&r,storage,48,base+10000000+k*1000000));
        assert(mb_pcm_ring_trim(&r,40,24)==24);
        assert(mb_pcm_ring_pop_timed(&r,out,64,&first)==24 && first==base+10500000+k*1000000);
    }
    mb_pcm_ring_clear_stopped(&r);
}
int main(void) {
    stamped_capture_blocks();
    assert(!mb_pcm_ring_init(NULL,storage,64));
    assert(!mb_pcm_ring_init(&ring,storage,0));
    assert(!mb_pcm_ring_init(&ring,storage,63));
    assert(mb_pcm_ring_init(&ring,storage,64));
    float data[128]={0},out[128]={0};
    assert(!mb_pcm_ring_pop(&ring,out,64));
    assert(mb_pcm_ring_push(&ring,data,64));
    assert(!mb_pcm_ring_push(&ring,data,1));
    assert(mb_pcm_ring_pop(&ring,out,64)==64);
    for(int i=0;i<64;i++){data[i*2]=(float)i;data[i*2+1]=(float)-i;}
    assert(mb_pcm_ring_push(&ring,data,64));
    assert(mb_pcm_ring_trim(&ring,64,4)==0);
    assert(mb_pcm_ring_trim(&ring,32,4)==60);
    assert(mb_pcm_ring_pop(&ring,out,64)==4 && out[0]==60 && out[7]==-63);
    data[0]=NAN;
    assert(!mb_pcm_ring_push(&ring,data,1));
    assert(mb_pcm_ring_pop(&ring,out,1)==0);
    pthread_t thread;assert(pthread_create(&thread,NULL,producer,NULL)==0);
    for(int i=1;i<=100000;i++) {
        while(!mb_pcm_ring_pop(&ring,out,1)) sched_yield();
        assert(out[0]==(float)i && out[1]==(float)-i);
    }
    assert(pthread_join(thread,NULL)==0);
    assert(mb_pcm_ring_pop(&ring,out,1)==0);
    mb_pcm_ring_clear_stopped(&ring);
    for(size_t i=0;i<128;i++) assert(storage[i]==0);
    puts("ring: empty/full/overflow/nonfinite/wraparound/100000 ordered concurrent frames/clear passed");
}
