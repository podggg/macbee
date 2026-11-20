#ifndef MACBEE_CONTROLLED_PCM_H
#define MACBEE_CONTROLLED_PCM_H
#include <stddef.h>
#include <stdint.h>
/* C2 diagnostic protocol only, not an ordinary-audio encoder. A packet carries
 * MBP1, uint32 little-endian frame count, then interleaved 48 kHz S16LE stereo.
 * All samples are validated before writing. Zero frames is the explicit END.
 * This pure function does not open audio, files, pipes or sockets. */
#define MB_CONTROLLED_MAX_FRAMES 4096u
#define MB_CONTROLLED_PACKET_BYTES (8u + 4u * MB_CONTROLLED_MAX_FRAMES)
size_t mb_controlled_packet(const float *stereo, size_t frames,
                            uint8_t *packet, size_t capacity);
#endif
