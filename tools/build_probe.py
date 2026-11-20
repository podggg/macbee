#!/usr/bin/env python3
"""Compile one C file, no package manager, modules, Xcode CAS, or downloads."""
import os, pathlib, shutil, subprocess, sys, time
root = pathlib.Path(__file__).resolve().parents[1]
from local_config import PROBE_PATH
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else PROBE_PATH.parent).resolve()
out.mkdir(parents=True, exist_ok=True)
if shutil.disk_usage(out).free < 2 * 1024**3:
    sys.exit('Refusing build: less than 2 GiB free on output volume')
cmd = ['xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
       '-fno-modules', '-mmacosx-version-min=14.2',
       '-ffile-prefix-map=' + str(root) + '=/macbee-source',
       '-fmacro-prefix-map=' + str(root) + '=/macbee-source',
       '-framework', 'CoreAudio',
       '-framework', 'CoreFoundation', str(root/'Sources/tone_probe.c'), '-o', str(out/'macbee-tone-probe')]
p = subprocess.Popen(cmd, start_new_session=True)
try:
    deadline = time.monotonic() + 120
    while p.poll() is None:
        size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
        if size > 128 * 1024**2 or shutil.disk_usage(out).free < 1024**3 or time.monotonic() > deadline:
            os.killpg(p.pid, 15)
            raise RuntimeError('Build guard stopped compiler; files retained for inspection')
        time.sleep(.2)
    if p.returncode: sys.exit(p.returncode)
    print(out/'macbee-tone-probe')
finally:
    if p.poll() is None: os.killpg(p.pid, 15)
