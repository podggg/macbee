/* Offline CoreAudio shim test for Sources/tone_probe.c. No real HAL or hardware access. */
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    AudioObjectID id;
    pid_t pid;
    UInt32 running;
    OSStatus pid_status;
    OSStatus run_status;
} FakeProc;

static struct {
    AudioObjectID default_device;
    OSStatus default_status;
    UInt32 transport;
    OSStatus transport_status;
    const char *uid;
    OSStatus uid_status;
    UInt32 input_stream_bytes;
    UInt32 output_stream_count;
    Float64 sample_rate;
    UInt32 mute;
    Float32 volume;
    FakeProc procs[8];
    UInt32 proc_count;
    int list_size_calls;
    int list_get_calls;
    OSStatus fail_list_get_on_call[4];
    bool swap_procs_after_list_call;
    FakeProc swap_procs[8];
    UInt32 swap_proc_count;
} fake;

static void reset_fake(void) {
    memset(&fake, 0, sizeof(fake));
    fake.default_device = 84;
    fake.transport = kAudioDeviceTransportTypeBuiltIn;
    fake.uid = "BuiltInSpeakerDevice";
    fake.output_stream_count = 1;
    fake.sample_rate = 48000.0;
    fake.mute = 0;
    fake.volume = 0.25f;
}

OSStatus AudioObjectGetPropertyDataSize(
    AudioObjectID inObjectID,
    const AudioObjectPropertyAddress *inAddress,
    UInt32 inQualifierDataSize,
    const void *inQualifierData,
    UInt32 *outDataSize) {
    (void)inQualifierDataSize; (void)inQualifierData;
    if (!inAddress || !outDataSize) return kAudioHardwareIllegalOperationError;
    if (inObjectID == kAudioObjectSystemObject &&
        inAddress->mSelector == kAudioHardwarePropertyProcessObjectList) {
        fake.list_size_calls++;
        if (fake.swap_procs_after_list_call && fake.list_size_calls == 3) {
            memcpy(fake.procs, fake.swap_procs, sizeof(fake.procs));
            fake.proc_count = fake.swap_proc_count;
            fake.swap_procs_after_list_call = false;
        }
        *outDataSize = (UInt32)(fake.proc_count * sizeof(AudioObjectID));
        return noErr;
    }
    if (inObjectID == fake.default_device &&
        inAddress->mSelector == kAudioDevicePropertyStreams &&
        inAddress->mScope == kAudioObjectPropertyScopeInput) {
        *outDataSize = fake.input_stream_bytes;
        return noErr;
    }
    if (inObjectID == fake.default_device &&
        inAddress->mSelector == kAudioDevicePropertyStreams &&
        inAddress->mScope == kAudioObjectPropertyScopeOutput) {
        *outDataSize = (UInt32)(fake.output_stream_count * sizeof(AudioStreamID));
        return noErr;
    }
    return kAudioHardwareUnknownPropertyError;
}

OSStatus AudioObjectGetPropertyData(
    AudioObjectID inObjectID,
    const AudioObjectPropertyAddress *inAddress,
    UInt32 inQualifierDataSize,
    const void *inQualifierData,
    UInt32 *ioDataSize,
    void *outData) {
    (void)inQualifierDataSize; (void)inQualifierData;
    if (!inAddress || !ioDataSize || !outData) return kAudioHardwareIllegalOperationError;
    if (inObjectID == kAudioObjectSystemObject) {
        if (inAddress->mSelector == kAudioHardwarePropertyDefaultOutputDevice) {
            if (fake.default_status != noErr) return fake.default_status;
            if (*ioDataSize < sizeof(AudioObjectID)) return kAudioHardwareBadPropertySizeError;
            *(AudioObjectID *)outData = fake.default_device;
            *ioDataSize = sizeof(AudioObjectID);
            return noErr;
        }
        if (inAddress->mSelector == kAudioHardwarePropertyProcessObjectList) {
            int call = fake.list_get_calls++;
            if (call < 4 && fake.fail_list_get_on_call[call] != noErr) {
                return fake.fail_list_get_on_call[call];
            }
            UInt32 need = (UInt32)(fake.proc_count * sizeof(AudioObjectID));
            if (*ioDataSize < need) return kAudioHardwareBadPropertySizeError;
            AudioObjectID *dst = (AudioObjectID *)outData;
            for (UInt32 i = 0; i < fake.proc_count; i++) dst[i] = fake.procs[i].id;
            *ioDataSize = need;
            return noErr;
        }
    }
    if (inObjectID == fake.default_device) {
        if (inAddress->mSelector == kAudioDevicePropertyTransportType) {
            if (fake.transport_status != noErr) return fake.transport_status;
            *(UInt32 *)outData = fake.transport;
            *ioDataSize = sizeof(UInt32);
            return noErr;
        }
        if (inAddress->mSelector == kAudioDevicePropertyDeviceUID) {
            if (fake.uid_status != noErr) return fake.uid_status;
            if (!fake.uid) {
                *(CFStringRef *)outData = NULL;
                *ioDataSize = sizeof(CFStringRef);
                return noErr;
            }
            *(CFStringRef *)outData = CFStringCreateWithCString(kCFAllocatorDefault, fake.uid, kCFStringEncodingUTF8);
            *ioDataSize = sizeof(CFStringRef);
            return noErr;
        }
        if (inAddress->mSelector == kAudioDevicePropertyStreams &&
            inAddress->mScope == kAudioObjectPropertyScopeOutput) {
            AudioStreamID *streams = (AudioStreamID *)outData;
            for (UInt32 i = 0; i < fake.output_stream_count && i < 8; i++) streams[i] = 900 + i;
            *ioDataSize = (UInt32)(fake.output_stream_count * sizeof(AudioStreamID));
            return noErr;
        }
        if (inAddress->mSelector == kAudioDevicePropertyLatency ||
            inAddress->mSelector == kAudioDevicePropertySafetyOffset ||
            inAddress->mSelector == kAudioDevicePropertyBufferFrameSize) {
            *(UInt32 *)outData = 64;
            *ioDataSize = sizeof(UInt32);
            return noErr;
        }
        if (inAddress->mSelector == kAudioDevicePropertyMute) {
            *(UInt32 *)outData = fake.mute;
            *ioDataSize = sizeof(UInt32);
            return noErr;
        }
        if (inAddress->mSelector == kAudioDevicePropertyVolumeScalar) {
            *(Float32 *)outData = fake.volume;
            *ioDataSize = sizeof(Float32);
            return noErr;
        }
        if (inAddress->mSelector == kAudioDevicePropertyNominalSampleRate) {
            *(Float64 *)outData = fake.sample_rate;
            *ioDataSize = sizeof(Float64);
            return noErr;
        }
    }
    if (inObjectID == 900) {
        if (inAddress->mSelector == kAudioStreamPropertyVirtualFormat) {
            AudioStreamBasicDescription fmt = {0};
            fmt.mSampleRate = fake.sample_rate;
            fmt.mFormatID = kAudioFormatLinearPCM;
            fmt.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked;
            fmt.mBitsPerChannel = 32;
            fmt.mChannelsPerFrame = 2;
            fmt.mFramesPerPacket = 1;
            fmt.mBytesPerFrame = 8;
            fmt.mBytesPerPacket = 8;
            *(AudioStreamBasicDescription *)outData = fmt;
            *ioDataSize = sizeof(fmt);
            return noErr;
        }
        if (inAddress->mSelector == kAudioStreamPropertyLatency) {
            *(UInt32 *)outData = 128;
            *ioDataSize = sizeof(UInt32);
            return noErr;
        }
    }
    for (UInt32 i = 0; i < fake.proc_count; i++) {
        if (fake.procs[i].id == inObjectID) {
            if (inAddress->mSelector == kAudioProcessPropertyPID) {
                if (fake.procs[i].pid_status != noErr) return fake.procs[i].pid_status;
                *(pid_t *)outData = fake.procs[i].pid;
                *ioDataSize = sizeof(pid_t);
                return noErr;
            }
            if (inAddress->mSelector == kAudioProcessPropertyIsRunningOutput) {
                if (fake.procs[i].run_status != noErr) return fake.procs[i].run_status;
                *(UInt32 *)outData = fake.procs[i].running;
                *ioDataSize = sizeof(UInt32);
                return noErr;
            }
        }
    }
    return kAudioHardwareBadObjectError;
}

OSStatus AudioDeviceCreateIOProcID(AudioObjectID dev, AudioDeviceIOProc proc, void *data, AudioDeviceIOProcID *out) {
    (void)dev; (void)proc; (void)data; (void)out;
    return kAudioHardwareUnsupportedOperationError;
}
OSStatus AudioDeviceStart(AudioObjectID dev, AudioDeviceIOProcID proc) {
    (void)dev; (void)proc;
    return kAudioHardwareUnsupportedOperationError;
}
OSStatus AudioDeviceStop(AudioObjectID dev, AudioDeviceIOProcID proc) {
    (void)dev; (void)proc;
    return noErr;
}
OSStatus AudioDeviceDestroyIOProcID(AudioObjectID dev, AudioDeviceIOProcID proc) {
    (void)dev; (void)proc;
    return noErr;
}

#define main tone_probe_main
#include "../Sources/tone_probe.c"
#undef main

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    mach_timebase_info(&timebase);
    reset_fake();
    const char *mode = argv[1];
    if (strcmp(mode, "shift") == 0) {
        Probe p = {0};
        assert(inspect(&p) == 0);
        fake.default_device = 91;
        assert(inspect(&p) == 0);
        return 0;
    }
    if (strcmp(mode, "json_escape") == 0) {
        fake.uid = "Fake\"Speaker\\Line\nCtrl\x01" "End";
        Probe p = {0};
        assert(inspect(&p) == 0);
        return 0;
    }
    if (strcmp(mode, "vanished_procs") == 0) {
        fake.proc_count = 4;
        fake.procs[0] = (FakeProc){201, 5001, 1, noErr, noErr};
        fake.procs[1] = (FakeProc){202, 5002, 1, kAudioHardwareBadObjectError, noErr};
        fake.procs[2] = (FakeProc){203, 5003, 1, noErr, kAudioHardwareBadObjectError};
        fake.procs[3] = (FakeProc){204, 5004, 0, noErr, noErr};
        Probe p = {0};
        assert(inspect(&p) == 0);
        return 0;
    }
    if (strcmp(mode, "vanished_only") == 0) {
        fake.proc_count = 1;
        fake.procs[0] = (FakeProc){202, 5002, 1, kAudioHardwareBadObjectError, noErr};
        Probe p = {0};
        assert(inspect(&p) == 0);
        return 0;
    }
    if (strcmp(mode, "retry_growth") == 0) {
        fake.fail_list_get_on_call[0] = kAudioHardwareBadPropertySizeError;
        fake.proc_count = 2;
        fake.procs[0] = (FakeProc){201, 5001, 1, noErr, noErr};
        fake.procs[1] = (FakeProc){202, 5002, 1, noErr, kAudioHardwareUnknownPropertyError};
        fake.swap_procs_after_list_call = true;
        fake.swap_proc_count = 1;
        fake.swap_procs[0] = (FakeProc){201, 5001, 1, noErr, noErr};
        Probe p = {0};
        assert(inspect(&p) == 0);
        assert(fake.list_get_calls == 3);
        return 0;
    }
    if (strcmp(mode, "all_retries_fail") == 0) {
        fake.fail_list_get_on_call[0] = kAudioHardwareBadPropertySizeError;
        fake.fail_list_get_on_call[1] = kAudioHardwareBadPropertySizeError;
        fake.fail_list_get_on_call[2] = kAudioHardwareBadPropertySizeError;
        fake.proc_count = 1;
        fake.procs[0] = (FakeProc){201, 5001, 1, noErr, noErr};
        Probe p = {0};
        assert(inspect(&p) == 0);
        return 0;
    }
    if (strcmp(mode, "not_builtin") == 0) {
        fake.transport = kAudioDeviceTransportTypeBluetooth;
        Probe p = {0};
        assert(inspect(&p) == 1);
        return 0;
    }
    if (strcmp(mode, "uid_error") == 0) {
        fake.uid_status = kAudioHardwareBadObjectError;
        Probe p = {0};
        assert(inspect(&p) == 1);
        return 0;
    }
    if (strcmp(mode, "uid_empty") == 0) {
        fake.uid = "";
        Probe p = {0};
        assert(inspect(&p) == 1);
        return 0;
    }
    if (strcmp(mode, "coexist_vanished") == 0) {
        fake.proc_count = 1;
        fake.procs[0] = (FakeProc){201, 5001, 1, noErr, noErr};
        Probe p = {0};
        assert(inspect(&p) == 0);
        coexist_existing = true;
        fake.proc_count = 2;
        fake.procs[1] = (FakeProc){202, 5002, 1, kAudioHardwareBadObjectError, noErr};
        assert(active_other_output() == 0);
        fake.procs[1] = (FakeProc){203, 5005, 1, noErr, noErr};
        assert(active_other_output() == 1);
        return 0;
    }
    return 3;
}
