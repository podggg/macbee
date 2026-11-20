#include "controlled_pcm.h"
#include <math.h>
#include <string.h>
size_t mb_controlled_packet(const float *stereo,size_t frames,uint8_t *packet,size_t capacity) {
    if(!packet || frames>MB_CONTROLLED_MAX_FRAMES || capacity<8+frames*4 || (frames && !stereo))return 0;
    /* Fail closed. No clipping, channel folding, resampling, or partial packet. */
    for(size_t i=0;i<frames;i++) {
        float l=stereo[i*2],r=stereo[i*2+1];
        if(!isfinite(l) || !isfinite(r) || fabsf(l)>.0051f || fabsf(r)>.0051f || fabsf(l-r)>.0001f)return 0;
    }
    memcpy(packet,"MBP1",4);
    for(size_t i=0;i<4;i++)packet[4+i]=(uint8_t)((uint32_t)frames>>(8*i));
    for(size_t i=0;i<frames*2;i++) {
        int16_t value=(int16_t)lrintf(stereo[i]*32768.0f);
        packet[8+i*2]=(uint8_t)((uint16_t)value&255u);
        packet[9+i*2]=(uint8_t)((uint16_t)value>>8);
    }
    return 8+frames*4;
}
