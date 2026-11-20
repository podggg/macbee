#ifndef MACBEE_PCM_RING_H
#define MACBEE_PCM_RING_H
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
/* Single producer/single consumer. Storage allocated before any audio callback.
 * No allocation, locks, file IO, network IO, or system audio APIs in this module.
 * Capacity is a power of two, at most 262144 stereo frames.
 * Caller must stop both endpoints before initializing/clearing it.
 */
typedef struct {
    float *samples;
    /* Optional, caller-owned sidecar. Slots use the same release/acquire
     * publication as PCM; read and trim can never separate a time from a frame. */
    uint64_t *capture_ns;
    size_t capacity_frames;
    _Atomic uint64_t read_frame;
    _Atomic uint64_t write_frame;
} mb_pcm_ring;
int mb_pcm_ring_init(mb_pcm_ring *ring, float *stereo_storage, size_t capacity_frames);
/* All-or-nothing enqueue. 0 means pressure/invalid input; caller must stop,
 * rather than silently lose time or block the real-time callback. */
int mb_pcm_ring_push(mb_pcm_ring *ring, const float *stereo, size_t frames);
/* Consumer only: on bounded system playback drop old whole stereo frames. */
size_t mb_pcm_ring_trim(mb_pcm_ring *ring,size_t threshold,size_t keep);
size_t mb_pcm_ring_pop(mb_pcm_ring *ring, float *stereo, size_t max_frames);
int mb_pcm_ring_push_timed(mb_pcm_ring *ring, const float *stereo, size_t frames, uint64_t first_frame_ns);
size_t mb_pcm_ring_pop_timed(mb_pcm_ring *ring, float *stereo, size_t max_frames, uint64_t *first_frame_ns);
void mb_pcm_ring_clear_stopped(mb_pcm_ring *ring);
#endif
