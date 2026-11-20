"""Pure synthetic-source generation and fail-closed diagnostic validation.
No device, network, permission, subprocess, or recording operations.
"""
import json
import math
import re
import struct

RATE = 48000
CHUNK_FRAMES = 960
PEAK = 163


def payload(chunk, silent=False):
    """20 ms stereo PCM; six 100 ms pulses after a 3 s silent pre-roll."""
    data = bytearray(CHUNK_FRAMES * 4)
    for i in range(CHUNK_FRAMES):
        offset = chunk * CHUNK_FRAMES + i - 3 * RATE
        phase = (offset % RATE) / RATE
        active = not silent and 150 <= chunk < 450 and phase < .1
        value = round(PEAK * math.sin(math.pi * phase / .1) ** 2 *
                      math.sin(2 * math.pi * 880 * phase)) if active else 0
        struct.pack_into('<hh', data, i * 4, value, value)
    return data


def stats(text, silent=False):
    rows = [json.loads(x) for x in re.findall(r'MACBEE_COREAUDIO (\{[^\n]+\})', text)]
    guards = [json.loads(x) for x in re.findall(r'MACBEE_GUARD_FINAL (\{[^\n]+\})', text)]
    if len(rows) != 1 or len(guards) != 1:
        raise RuntimeError('Expected one native queue and one cleanup report')
    row, guard = rows[0], guards[0]
    for key in ('time_errors', 'running_time_errors', 'priming_time_errors',
                'enqueue_errors', 'overlimit_samples'):
        if row.get(key) != 0:
            raise RuntimeError('Native diagnostic failure: ' + key)
    if row.get('priming_queries_skipped') != 2 or row.get('callbacks', 0) <= 2:
        raise RuntimeError('Playback did not advance beyond both prefill callbacks')
    if row.get('pulses') != (0 if silent else 6):
        raise RuntimeError('Missing or extra synthetic pulse')
    if silent:
        if row.get('nonzero_frames') != 0 or row.get('peak_int16') != 0:
            raise RuntimeError('Silent test received nonzero output')
    elif not 25000 <= row.get('nonzero_frames', 0) <= 30000 or row.get('peak_int16') != PEAK:
        raise RuntimeError('Synthetic output amplitude/content outside bounds')
    if guard.get('error') or guard.get('client_exit') != 0 or guard.get('reason') == '20-second-watchdog':
        raise RuntimeError('Output guardian did not finish cleanly')
    before, after = guard.get('before'), guard.get('after')
    if not before or not after:
        raise RuntimeError('Missing before/after output inventory')
    for key in ('device_id', 'sample_rate', 'input_streams', 'volume', 'mute'):
        if key not in before or before[key] != after.get(key):
            raise RuntimeError('Output setting changed: ' + key)
    return {'audio_queues': rows, 'guardian': guard}
