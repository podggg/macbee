#ifndef MACBEE_APPLICATION_PCM_H
#define MACBEE_APPLICATION_PCM_H
#include <stddef.h>
#include <stdint.h>
/* Prepared, unlinked normal-amplitude conversion. No capture, IPC or playback.
 * Intentionally separate from the frozen -46 dBFS controlled-test encoder.
 * Input is validated 48 kHz interleaved Float32 stereo supplied by a future
 * explicitly selected-app adapter; this function cannot select any source.
 * No gain is applied. Finite full-scale overshoots saturate, never wrap.
 */
size_t mb_application_pcm16(const float *stereo,size_t frames,uint8_t *s16le,
                            size_t capacity,size_t *clipped_samples);
#endif
