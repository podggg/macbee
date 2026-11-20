#!/usr/bin/env python3
"""Compile-only C2 candidate, never replace or launch a permission-tested app.
The separately named unsigned-development identity may need sender permission
when the owner later authorizes the exact combined test. This does not bypass it.
"""
import argparse,hashlib,pathlib,plistlib,shutil,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--speakers',action='store_true');args=parser.parse_args()
name='Macbee Speaker Test' if args.speakers else 'Macbee Relay Test'
APP=ROOT/'.local-app'/(name+'.app')
if APP.exists():raise RuntimeError('Retain the candidate identity: do not silently rebuild an existing bundle')
if shutil.disk_usage(ROOT).free<512*1024**2:raise RuntimeError('512 MiB free-space guard')
helper=ROOT/'.local-app/Macbee Capture Test.app/Contents/MacOS/macbee-controlled-source'
if hashlib.sha256(helper.read_bytes()).hexdigest()!='edcf29b37628e498ee6d60afaab3bcb1e6ac43bb6418ca673c24dd532116c96a':
    raise RuntimeError('Frozen controlled source hash mismatch')
BIN=APP/'Contents/MacOS';BIN.mkdir(parents=True)
info=dict(CFBundleIdentifier='io.github.podggg.macbee.speakertest' if args.speakers else 'io.github.podggg.macbee.relaytest',CFBundleName=name,
 CFBundleDisplayName=name,CFBundleExecutable='MacbeeRelayTest',CFBundlePackageType='APPL',
 CFBundleShortVersionString='0.0.1',CFBundleVersion='1',LSMinimumSystemVersion='14.2',LSUIElement=True,
 NSHighResolutionCapable=True,MacbeeDevelopmentRoot=str(ROOT),MacbeePythonExecutable=str(pathlib.Path(sys.executable).resolve()),
 MacbeeUIOnly=False,MacbeeControlledSpeakerTest=args.speakers,
 NSAudioCaptureUsageDescription=("Macbee captures only its own generated test sound to play it through both of your Macs. No microphone, other app audio, or recordings." if args.speakers else "Macbee captures only its own synthetic test helper to send it to silent test sinks on your two Macs. No microphone, ordinary app audio, or recordings."))
(APP/'Contents/Info.plist').write_bytes(plistlib.dumps(info));shutil.copy2(helper,BIN/'macbee-controlled-source')
flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
objects=[]
for source in ('pcm_ring','controlled_pcm'):
    obj=ROOT/'.local-app'/('relay-'+source+'.o');objects.append(str(obj))
    subprocess.run(flags+['-std=c11','-c',str(ROOT/'Sources/AudioPipeline'/(source+'.c')),'-o',str(obj)],check=True,timeout=30)
subprocess.run(flags+['-fobjc-arc','-DMACBEE_CONTROLLED_RELAY=1','-framework','Cocoa','-framework','CoreAudio',
 str(ROOT/'Sources/MacbeeApp/main.m'),str(ROOT/'Sources/ControlledCapture/ControlledRelay.m'),
 str(ROOT/'Sources/ControlledCapture/ControlledCapture.m')]+objects+['-o',str(BIN/'MacbeeRelayTest')],check=True,timeout=60)
subprocess.run(['codesign','--force','--sign','-',str(APP)],check=True,timeout=30)
subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True,timeout=30)
print('Built only; not launched:',APP)
print('Executable SHA-256:',hashlib.sha256((BIN/'MacbeeRelayTest').read_bytes()).hexdigest())
