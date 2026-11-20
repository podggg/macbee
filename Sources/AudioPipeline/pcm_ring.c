#include "pcm_ring.h"
#include <math.h>
#include <string.h>

int mb_pcm_ring_init(mb_pcm_ring *ring,float *storage,size_t capacity) {
    if(!ring || !storage || !capacity || (capacity & (capacity-1)) || capacity>262144) return 0;
    ring->samples=storage;ring->capture_ns=NULL;ring->capacity_frames=capacity;
    atomic_init(&ring->read_frame,0);atomic_init(&ring->write_frame,0);
    mb_pcm_ring_clear_stopped(ring);return 1;
}
int mb_pcm_ring_push(mb_pcm_ring *ring,const float *stereo,size_t frames) {
    return mb_pcm_ring_push_timed(ring,stereo,frames,0);
}
int mb_pcm_ring_push_timed(mb_pcm_ring *ring,const float *stereo,size_t frames,uint64_t first_ns) {
    if(!ring || !ring->samples || !stereo || frames>ring->capacity_frames) return 0;
    if(ring->capture_ns && (!first_ns || UINT64_MAX-first_ns<(uint64_t)frames*1000000000/48000))return 0;
    uint64_t write=atomic_load_explicit(&ring->write_frame,memory_order_relaxed);
    uint64_t read=atomic_load_explicit(&ring->read_frame,memory_order_acquire);
    if(write-read>ring->capacity_frames || frames>ring->capacity_frames-(write-read)) return 0;
    for(size_t i=0;i<frames*2;i++) if(!isfinite(stereo[i])) return 0;
    for(size_t i=0;i<frames;i++) {
        size_t slot=(size_t)((write+i)%ring->capacity_frames)*2;
        memcpy(ring->samples+slot,stereo+i*2,2*sizeof(float));
        if(ring->capture_ns)ring->capture_ns[slot/2]=first_ns+(uint64_t)i*1000000000/48000;
    }
    atomic_store_explicit(&ring->write_frame,write+frames,memory_order_release);
    return 1;
}
size_t mb_pcm_ring_trim(mb_pcm_ring *ring,size_t threshold,size_t keep) {
    if(!ring || !ring->samples || keep>threshold)return 0;
    uint64_t r=atomic_load_explicit(&ring->read_frame,memory_order_relaxed);
    uint64_t w=atomic_load_explicit(&ring->write_frame,memory_order_acquire);
    if(w-r>ring->capacity_frames || w-r<=threshold)return 0;
    atomic_store_explicit(&ring->read_frame,w-keep,memory_order_release);
    return (size_t)(w-r-keep);
}
size_t mb_pcm_ring_pop(mb_pcm_ring *ring,float *stereo,size_t max_frames) {
    return mb_pcm_ring_pop_timed(ring,stereo,max_frames,NULL);
}
size_t mb_pcm_ring_pop_timed(mb_pcm_ring *ring,float *stereo,size_t max_frames,uint64_t *first_ns) {
    if(first_ns)*first_ns=0;
    if(!ring || !ring->samples || !stereo || (first_ns && !ring->capture_ns)) return 0;
    uint64_t read=atomic_load_explicit(&ring->read_frame,memory_order_relaxed);
    uint64_t write=atomic_load_explicit(&ring->write_frame,memory_order_acquire);
    uint64_t available=write-read;
    if(available>ring->capacity_frames) return 0;
    size_t count=available<max_frames?(size_t)available:max_frames;
    if(first_ns && count)*first_ns=ring->capture_ns[read % ring->capacity_frames];
    for(size_t i=0;i<count;i++) {
        size_t slot=(size_t)((read+i)%ring->capacity_frames)*2;
        // A packet may span adjacent HAL callbacks, but not a capture gap.
        // Treat a deviation of one complete 48 kHz sample as discontinuous.
        if(first_ns && i){
            uint64_t observed=ring->capture_ns[slot/2],expected=*first_ns+(uint64_t)i*1000000000/48000;
            if((observed>expected?observed-expected:expected-observed)>=20834){count=i;break;}
        }
        memcpy(stereo+i*2,ring->samples+slot,2*sizeof(float));
    }
    atomic_store_explicit(&ring->read_frame,read+count,memory_order_release);
    return count;
}
void mb_pcm_ring_clear_stopped(mb_pcm_ring *ring) {
    if(!ring || !ring->samples) return;
    if(ring->capture_ns)memset(ring->capture_ns,0,ring->capacity_frames*sizeof(*ring->capture_ns));
    volatile float *data=ring->samples;
    for(size_t i=0;i<ring->capacity_frames*2;i++) data[i]=0;
    atomic_store(&ring->read_frame,0);atomic_store(&ring->write_frame,0);
}
