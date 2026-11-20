#include "application_pcm.h"
#include <math.h>
size_t mb_application_pcm16(const float *stereo,size_t frames,uint8_t *output,
                            size_t capacity,size_t *clipped_samples) {
    if(!stereo || !output || !clipped_samples || !frames || frames>4096 || capacity<frames*4)return 0;
    for(size_t i=0;i<frames*2;i++)if(!isfinite(stereo[i]))return 0;
    size_t clipped=0;
    for(size_t i=0;i<frames*2;i++) {
        float sample=stereo[i];int32_t value;
        if(sample>=1){value=32767;if(sample>1)clipped++;}
        else if(sample<=-1){value=-32768;if(sample<-1)clipped++;}
        else {value=(int32_t)lrintf(sample*32768);if(value>32767)value=32767;}
        output[i*2]=(uint8_t)((uint16_t)value&255u);
        output[i*2+1]=(uint8_t)((uint16_t)value>>8);
    }
    *clipped_samples=clipped;return frames*4;
}
