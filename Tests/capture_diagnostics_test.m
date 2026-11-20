/* Invoke only the pure input callback with owned arrays. No HAL/device/app. */
#import "../Sources/ControlledCapture/ControlledCapture.m"
#include <assert.h>
int main(void){@autoreleasepool{
 CaptureState state={0};atomic_init(&state.enabled,true);
 mb_pcm_ring_init(&state.ring,state.storage,MB_CAPACITY);
 float samples[16]={0};AudioBufferList input={.mNumberBuffers=1,.mBuffers={{.mNumberChannels=2,.mDataByteSize=sizeof(samples),.mData=samples}}};
 captureInput(0,NULL,&input,NULL,NULL,NULL,&state);
 assert(atomic_load(&state.callbacks)==1 && atomic_load(&state.frames)==8);
 assert(atomic_load(&state.nonzeroFloatFrames)==0 && atomic_load(&state.firstCallbackTicks)>0);
 uint64_t first=atomic_load(&state.firstCallbackTicks);
 samples[0]=.000001f;samples[3]=-.005f;
 captureInput(0,NULL,&input,NULL,NULL,NULL,&state);
 assert(atomic_load(&state.callbacks)==2 && atomic_load(&state.frames)==16);
 assert(atomic_load(&state.nonzeroFloatFrames)==2 && atomic_load(&state.firstCallbackTicks)==first);
 captureInput(0,NULL,NULL,NULL,NULL,NULL,&state);
 assert(atomic_load(&state.callbacks)==3 && atomic_load(&state.frames)==16 && atomic_load(&state.fault));
 assert(!atomic_load(&state.enabled));
 puts("callback/zero/nonzero/fault counters verified; no capture or hardware");
}return 0;}
