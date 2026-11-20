#include "controlled_pcm.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
int main(int argc,char **argv) {
    uint8_t packet[MB_CONTROLLED_PACKET_BYTES];float samples[960*2];
    if(argc==2 && !strcmp(argv[1],"fixture")) {
        for(int frame=0;frame<312000;frame+=960) {
            for(int i=0;i<960;i++) {
                int at=frame+i,within=at%48000;
                float value=(at<288000 && within<4800)?(float)(163*sin(2*3.141592653589793*880*within/48000)*pow(sin(3.141592653589793*within/4800),2)/32768):0;
                samples[2*i]=samples[2*i+1]=value;
            }
            size_t count=mb_controlled_packet(samples,960,packet,sizeof(packet));assert(count==3848);
            assert(fwrite(packet,1,count,stdout)==count);
        }
        assert(mb_controlled_packet(NULL,0,packet,sizeof(packet))==8);assert(fwrite(packet,1,8,stdout)==8);return 0;
    }
    memset(packet,77,sizeof(packet));float values[]={163.0f/32768,163.0f/32768,-163.0f/32768,-163.0f/32768};
    assert(mb_controlled_packet(values,2,packet,sizeof(packet))==16);
    const uint8_t expected[]={77,66,80,49,2,0,0,0,163,0,163,0,93,255,93,255};assert(!memcmp(packet,expected,16));
    uint8_t before[sizeof(packet)];memcpy(before,packet,sizeof(packet));
    values[2]=NAN;assert(!mb_controlled_packet(values,2,packet,sizeof(packet)));assert(!memcmp(before,packet,sizeof(packet)));
    values[2]=INFINITY;assert(!mb_controlled_packet(values,2,packet,sizeof(packet)));
    values[2]=.0052f;assert(!mb_controlled_packet(values,2,packet,sizeof(packet)));
    values[2]=0;assert(!mb_controlled_packet(values,2,packet,sizeof(packet)));
    assert(!mb_controlled_packet(values,4097,packet,sizeof(packet)));
    assert(!mb_controlled_packet(values,2,packet,8));assert(!mb_controlled_packet(NULL,2,packet,sizeof(packet)));
    assert(mb_controlled_packet(NULL,0,packet,sizeof(packet))==8);puts("controlled encoder bounds passed");
}
