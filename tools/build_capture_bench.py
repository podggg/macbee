#!/usr/bin/env python3
"""Compile-only controlled capture test app. This script never launches it."""
import pathlib,plistlib,shutil,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[1]
APP=ROOT/'.local-app/Macbee Capture Test.app'
if shutil.disk_usage(ROOT).free<512*1024**2:raise RuntimeError('512 MiB free-space guard')
BIN=APP/'Contents/MacOS';BIN.mkdir(parents=True,exist_ok=True)
info={'CFBundleIdentifier':'io.github.podggg.macbee.capturetest','CFBundleName':'Macbee Capture Test',
 'CFBundleDisplayName':'Macbee Capture Test','CFBundleExecutable':'MacbeeCaptureTest','CFBundlePackageType':'APPL',
 'CFBundleShortVersionString':'0.0.1','CFBundleVersion':'1','LSMinimumSystemVersion':'14.2','LSUIElement':True,
 'NSAudioCaptureUsageDescription':"Macbee captures only its own generated test sound to verify the source for dual-Mac audio. It does not use the microphone or save recordings."}
with (APP/'Contents/Info.plist').open('wb') as f:plistlib.dump(info,f)
flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
subprocess.run(flags+['-std=c11','-D_DARWIN_C_SOURCE','-framework','AudioToolbox','-framework','CoreAudio',
 str(ROOT/'Sources/ControlledSource/main.c'),'-o',str(BIN/'macbee-controlled-source')],check=True,timeout=60)
# Compile C separately: ARC is only meaningful for Objective-C.
obj=ROOT/'.local-app/capture-ring.o'
subprocess.run(flags+['-std=c11','-c',str(ROOT/'Sources/AudioPipeline/pcm_ring.c'),'-o',str(obj)],check=True,timeout=60)
subprocess.run(flags+['-fobjc-arc','-framework','Cocoa','-framework','CoreAudio',str(ROOT/'Sources/CaptureBench/main.m'),
 str(ROOT/'Sources/ControlledCapture/ControlledCapture.m'),str(obj),'-o',str(BIN/'MacbeeCaptureTest')],check=True,timeout=60)
for path in (BIN/'macbee-controlled-source',APP):subprocess.run(['codesign','--force','--sign','-',str(path)],check=True,timeout=30)
subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True,timeout=30)
print('Built only; not launched:',APP)
