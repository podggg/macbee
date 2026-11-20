#include "application_pcm.h"
#include <assert.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
static int value(const unsigned char *p){unsigned n=p[0]|((unsigned)p[1]<<8);return n>=32768?(int)n-65536:(int)n;}
int main(void){
    float in[]={0,0,1,-1,.5f,-.5f,2,-2,FLT_MAX,-FLT_MAX,.999999f,-.999999f};
    unsigned char out[24];size_t clipped=99;
    assert(mb_application_pcm16(in,6,out,sizeof(out),&clipped)==24);assert(clipped==4);
    const int expected[]={0,0,32767,-32768,16384,-16384,32767,-32768,32767,-32768,32767,-32768};
    for(size_t i=0;i<12;i++)assert(value(out+i*2)==expected[i]);
    unsigned char before[24];memcpy(before,out,sizeof(out));
    in[11]=NAN;clipped=99;assert(!mb_application_pcm16(in,6,out,sizeof(out),&clipped));
    assert(!memcmp(before,out,sizeof(out)));assert(clipped==99);
    in[11]=INFINITY;assert(!mb_application_pcm16(in,6,out,sizeof(out),&clipped));
    assert(!mb_application_pcm16(in,4097,out,sizeof(out),&clipped));
    assert(!mb_application_pcm16(in,6,out,23,&clipped));
    assert(!mb_application_pcm16(NULL,1,out,sizeof(out),&clipped));
    assert(!mb_application_pcm16(in,0,out,sizeof(out),&clipped));
    puts("normal PCM boundaries passed; no audio API or IO");return 0;
}
