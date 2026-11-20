#!/usr/bin/env python3
"""Compile a separate, offline-only selected-app interaction preview. Never launch."""
import argparse,pathlib,plistlib,shutil,subprocess,tempfile
parser=argparse.ArgumentParser()
parser.add_argument('--registration-delay',type=int,default=0,choices=range(121),help='Offline worker delay in seconds; no HAL or network')
parser.add_argument('--manual-session',action='store_true',help='Exercise manual lifetime in the inert preview; never captures or plays')
args=parser.parse_args()
ROOT=pathlib.Path(__file__).resolve().parents[1]
APP=ROOT/'.local-app/Macbee App Preview.app'
if shutil.disk_usage(ROOT).free<1024**3:raise RuntimeError('1 GiB internal free-space guard')
BIN=APP/'Contents/MacOS';BIN.mkdir(parents=True,exist_ok=True)
info=dict(CFBundleIdentifier='io.github.podggg.macbee.applicationpreview',CFBundleName='Macbee App Preview',
 CFBundleDisplayName='Macbee App Preview',CFBundleExecutable='MacbeeAppPreview',CFBundlePackageType='APPL',
 CFBundleShortVersionString='0.0.1',CFBundleVersion='1',LSMinimumSystemVersion='14.2',LSUIElement=True,
 NSHighResolutionCapable=True,MacbeeUIOnly=True,MacbeeApplicationTestEnabled=False,MacbeePreviewRegistrationDelaySeconds=args.registration_delay,
 MacbeeManualSessionEnabled=args.manual_session)
(APP/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
with tempfile.TemporaryDirectory(prefix='macbee-preview-build-') as temp:
 obj=pathlib.Path(temp)/'pcm.o'
 subprocess.run(flags+['-std=c11','-c',str(ROOT/'Sources/AudioPipeline/application_pcm.c'),'-o',str(obj)],check=True,timeout=30)
 sources=['Sources/MacbeeApp/main.m','Sources/MacbeeApp/ApplicationPreview.m',
          'Sources/ControlledCapture/ApplicationSession.m','Sources/ControlledCapture/ApplicationSource.m']
 subprocess.run(flags+['-fobjc-arc','-DMACBEE_APPLICATION_UI=1','-DMACBEE_APPLICATION_PREVIEW=1','-framework','Cocoa','-framework','CoreAudio']+
                [str(ROOT/s) for s in sources]+[str(obj),'-o',str(BIN/'MacbeeAppPreview')],check=True,timeout=60)
subprocess.run(['codesign','--force','--sign','-',str(APP)],check=True,timeout=30)
subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True,timeout=30)
print('Offline build only:',APP)
