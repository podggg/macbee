#!/usr/bin/env python3
"""Compile-only full-range client from existing approved objects. Never launch.
Only a new copy of the CoreAudio source/object is changed; C3 stays untouched.
No download, CMake configure, install, service, remote transfer, or Xcode CAS.
"""
import hashlib,json,pathlib,shlex,subprocess
from build_backend import ROOT,SOURCE,guard,run
from relay_runtime import ROOT as RUNTIME,BIN,MAX_BYTES,guard as runtime_guard
BASE_SHA='bb3ce244e36486f4a93297eb1b4f46a5ad02efe512d168cf3891050d52f348d8'

def prepared_source(original):
    if original.count('if(magnitude>164)')!=1:raise RuntimeError('Synthetic source clamp identity changed')
    return original.replace('Diagnostic safety ceiling: only this agreed low-amplitude synthetic test.',
        'Selected-app full-range S16 path. No extra gain or synthetic amplitude clamp.').replace('if(magnitude>164)','if(magnitude>32768)')

def main():
    guard();runtime_guard(verify=True)
    output=ROOT/'application-renderer';output.mkdir(exist_ok=True)
    original=SOURCE/'client/player/coreaudio_player.cpp';raw=original.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=BASE_SHA:raise RuntimeError('Expected approved diagnostic source not found')
    cpp=output/'coreaudio_player.cpp';cpp.write_text(prepared_source(raw.decode()))
    obj=output/'coreaudio_player.cpp.o';binary=output/'snapclient-application'
    flags={}
    for line in (ROOT/'build/client/CMakeFiles/snapclient.dir/flags.make').read_text().splitlines():
        if ' = ' in line:
            key,value=line.split(' = ',1);flags[key]=shlex.split(value)
    command=['/usr/bin/c++']+flags['CXX_DEFINES']+flags['CXX_INCLUDES']+flags['CXX_FLAGS']+[
        '-I'+str(SOURCE/'client/player'),'-c',str(cpp),'-o',str(obj)]
    run(command,'application-compile.log')
    link=shlex.split((ROOT/'build/client/CMakeFiles/snapclient.dir/link.txt').read_text())
    expected='CMakeFiles/snapclient.dir/player/coreaudio_player.cpp.o'
    if link.count(expected)!=1 or link.count('-o')!=1:raise RuntimeError('Unexpected existing link command')
    link[link.index(expected)]=str(obj);link[link.index('-o')+1]=str(binary)
    link=[str(ROOT/'build/client'/x) if x.startswith('CMakeFiles/') else x for x in link]
    run(link,'application-link.log')
    if original.read_bytes()!=raw:raise RuntimeError('Original diagnostic source changed unexpectedly')
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    manifest=dict(sha256=digest,format='48000:16:2',stop_mode='wait-for-process-exit',
                  base_source_sha256=BASE_SHA,patched_source_sha256=hashlib.sha256(cpp.read_bytes()).hexdigest(),
                  note='Compiled only. Never launched. No acoustic or ordinary playback validation.')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    # Copy only the new executable, with the same private-runtime capacity guard.
    total,_=runtime_guard(verify=True)
    if total+binary.stat().st_size+8192>MAX_BYTES:raise RuntimeError('Private runtime capacity guard')
    destination=BIN/binary.name
    if destination.exists():raise RuntimeError('Do not silently replace an existing selected-app renderer')
    with destination.open('xb') as target:target.write(binary.read_bytes())
    destination.chmod(0o500)
    (RUNTIME/'application-client.json').write_text(json.dumps(manifest,indent=2))
    runtime_guard(verify=True)
    print(json.dumps(manifest,indent=2));print('Compiled and privately staged locally only:',destination)
if __name__=='__main__':main()
