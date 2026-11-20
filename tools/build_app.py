#!/usr/bin/env python3
"""Build a native local diagnostic UI without Xcode/SwiftPM caches or installs."""
import argparse,pathlib,plistlib,shutil,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
# Tiny app bundle uses APFS: exFAT AppleDouble files are not signable bundle resources.
parser=argparse.ArgumentParser();parser.add_argument('--preview',action='store_true');args=parser.parse_args()
APP=ROOT/'.local-app'/('Macbee Preview.app' if args.preview else 'Macbee.app')
APP.parent.mkdir(parents=True,exist_ok=True)
if shutil.disk_usage(APP.parent).free<512*1024**2:raise RuntimeError('512 MiB internal free-space guard')
(APP/'Contents/MacOS').mkdir(parents=True,exist_ok=True)
info={'CFBundleIdentifier':'io.github.podggg.macbee.preview' if args.preview else 'io.github.podggg.macbee.dev','CFBundleName':'Macbee',
 'CFBundleDisplayName':'Macbee Preview' if args.preview else 'Macbee','CFBundleExecutable':'Macbee','CFBundlePackageType':'APPL',
 'CFBundleShortVersionString':'0.0.1','CFBundleVersion':'1','LSMinimumSystemVersion':'14.2',
 'LSUIElement':True,'NSHighResolutionCapable':True,'MacbeeDevelopmentRoot':str(ROOT),
 'MacbeePythonExecutable':str(pathlib.Path(sys.executable).resolve()),'MacbeeUIOnly':args.preview}
with (APP/'Contents/Info.plist').open('wb') as f:plistlib.dump(info,f)
subprocess.run(['xcrun','clang','-fobjc-arc','-fno-modules','-O2','-Wall','-Wextra','-Werror',
 '-mmacosx-version-min=14.2','-framework','Cocoa',str(ROOT/'Sources/MacbeeApp/main.m'),
 '-o',str(APP/'Contents/MacOS/Macbee')],check=True,timeout=120)
subprocess.run(['codesign','--force','--sign','-',str(APP)],check=True,timeout=30)
print(APP)
