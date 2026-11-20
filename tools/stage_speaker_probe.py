#!/usr/bin/env python3
"""Stage the existing 35 KiB readonly-inventory helper. Does not execute it."""
import hashlib,pathlib
from relay_runtime import BIN,guard
from local_config import PROBE_PATH
from speaker_transport import PROBE_HASH

def stage():
    guard(verify=True)
    source=PROBE_PATH
    destination=BIN/'macbee-tone-probe'
    data=source.read_bytes()
    if len(data)>128*1024 or hashlib.sha256(data).hexdigest()!=PROBE_HASH:
        raise RuntimeError('Approved inventory helper identity mismatch')
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes()!=data:raise RuntimeError('Retain unexpected destination')
    else:
        with destination.open('xb') as output:output.write(data)
        destination.chmod(0o500)
    print('Staged only; not executed. Allocated/free:',guard())

if __name__=='__main__':stage()
