#!/usr/bin/env python3
"""Compile-only selected-app candidate. No launch, capture, peer connection or install."""
import argparse,hashlib,pathlib,plistlib,shutil,subprocess,sys,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--output-dir',type=pathlib.Path,help='New task-scoped bundle directory under .local-app')
mode=parser.add_mutually_exclusive_group()
mode.add_argument('--browser-test',action='store_true',help='Compile a separate 10-second browser test bundle; never launches it')
mode.add_argument('--browser-diagnostics',action='store_true',help='Compile an alongside instrumented 10-second candidate; never replaces the frozen app')
mode.add_argument('--user-candidate',action='store_true',help='Compile a separately identified double-clickable, manual Start/Stop candidate; never launch it')
mode.add_argument('--system-candidate',action='store_true',help='Compile the isolated real System Audio candidate; never launch it')
args=parser.parse_args()
APP=ROOT/('.local-app/Macbee Candidate.app' if args.user_candidate else ('.local-app/Macbee Browser Diagnostics.app' if args.browser_diagnostics else ('.local-app/Macbee Browser Test.app' if args.browser_test else '.local-app/Macbee App Test.app')))
if args.system_candidate:APP=ROOT/'.local-app/Macbee Next.app'
if args.output_dir:
 folder=args.output_dir.resolve()
 if not folder.is_relative_to((ROOT/'.local-app').resolve()):raise RuntimeError('Build output must stay in .local-app')
 APP=folder/('Macbee.app' if args.user_candidate or args.system_candidate else APP.name)
if APP.exists():raise RuntimeError('Do not silently replace a permission-sensitive candidate')
if shutil.disk_usage(ROOT).free<1024**3:raise RuntimeError('1 GiB internal free-space guard')
BIN=APP/'Contents/MacOS';BIN.mkdir(parents=True)
executable='Macbee' if args.user_candidate or args.system_candidate else 'MacbeeAppTest'
info=dict(CFBundleIdentifier='io.github.podggg.macbee.applicationtest',CFBundleName='Macbee App Test',
 CFBundleDisplayName='Macbee App Test',CFBundleExecutable=executable,CFBundlePackageType='APPL',
 CFBundleShortVersionString='0.0.1',CFBundleVersion='1',LSMinimumSystemVersion='14.2',LSUIElement=True,
 NSHighResolutionCapable=True,MacbeeUIOnly=False,MacbeeApplicationTestEnabled=True,
 MacbeeDevelopmentRoot=str(ROOT),MacbeePythonExecutable=str(pathlib.Path(sys.executable).resolve()),
 NSAudioCaptureUsageDescription='Macbee shares only the app you explicitly select with your two Macs for a short test. No microphone or recording files.')
if args.browser_test or args.browser_diagnostics:
 info.update(CFBundleIdentifier='io.github.podggg.macbee.browsertest',CFBundleName='Macbee Browser Test',CFBundleDisplayName='Macbee Browser Test',MacbeeMaximumSharingSeconds=10)
if args.browser_diagnostics:
 info.update(CFBundleIdentifier='io.github.podggg.macbee.browserdiagnostics',CFBundleName='Macbee Browser Diagnostics',CFBundleDisplayName='Macbee Browser Diagnostics',CFBundleVersion='2')
if args.user_candidate:
 info.pop('MacbeeDevelopmentRoot');info.pop('MacbeePythonExecutable')
 info.update(CFBundleIdentifier='io.github.podggg.macbee.candidate',CFBundleName='Macbee',
  CFBundleDisplayName='Macbee',MacbeeUserLaunchableCandidate=True,MacbeeManualSessionEnabled=True,
  CFBundleIconFile='Macbee.icns',MacbeeReleaseVersion='1.0.1',CFBundleShortVersionString='1.0.1',CFBundleVersion='2',
  LSUIElement=False,NSAudioCaptureUsageDescription='Macbee shares audio from only the app you select with your configured Mac. No microphone or recording files.')
if args.system_candidate:
 info.pop('MacbeeDevelopmentRoot');info.pop('MacbeePythonExecutable')
 info.update(CFBundleIdentifier='io.github.podggg.macbee.systemcandidate',CFBundleName='Macbee',CFBundleDisplayName='Macbee',
  MacbeeUserLaunchableCandidate=True,MacbeeManualSessionEnabled=True,MacbeeApplicationTestEnabled=False,MacbeeSystemAudioEnabled=True,CFBundleIconFile='Macbee.icns',
  MacbeeBoundedSystemPacing=True,NSLocalNetworkUsageDescription='Find nearby SSH services when you open Add Mac. Macbee does not advertise a service.',
  NSBonjourServices=['_ssh._tcp'],MacbeeConfiguredOutputsEnabled=True,MacbeeReleaseVersion='1.2.6-dev',CFBundleShortVersionString='1.2.6',CFBundleVersion='10',LSUIElement=False,
  NSAudioCaptureUsageDescription='Shares all Mac audio, including notifications and calls, only with outputs you select. No microphone or recordings.')
(APP/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2',
 '-ffile-prefix-map='+str(ROOT)+'=/macbee-source','-fmacro-prefix-map='+str(ROOT)+'=/macbee-source']
with tempfile.TemporaryDirectory(prefix='macbee-app-build-') as temp:
 objects=[]
 for name in ('pcm_ring','application_pcm'):
  obj=pathlib.Path(temp)/(name+'.o');objects.append(str(obj))
  subprocess.run(flags+['-std=c11','-c',str(ROOT/'Sources/AudioPipeline'/(name+'.c')),'-o',str(obj)],check=True,timeout=30)
 sources=['Sources/MacbeeApp/main.m','Sources/ControlledCapture/ControlledCapture.m',
          'Sources/ControlledCapture/ApplicationSession.m','Sources/ControlledCapture/ApplicationSource.m']
 if args.system_candidate:sources[0]='Sources/MacbeeApp/SystemMain.m';sources.extend(['Sources/ControlledCapture/SystemAudioSource.m','Sources/MacbeeApp/SSHBrowse.m','Sources/MacbeeApp/AddMacSheet.m','Sources/SSHLogin/SSHSignIn.m','Sources/SSHLogin/SSHControlMaster.m'])
 if args.system_candidate:
  helper=BIN/'macbee-askpass'
  subprocess.run(flags+['-std=c11',str(ROOT/'Sources/SSHLogin/askpass.c'),'-o',str(helper)],check=True,timeout=30)
  subprocess.run(['codesign','--force','--sign','-',str(helper)],check=True,timeout=30)
 subprocess.run(flags+['-fobjc-arc','-DMACBEE_APPLICATION_UI=1','-framework','Cocoa','-framework','CoreAudio']+
                [str(ROOT/s) for s in sources]+objects+['-o',str(BIN/executable)],check=True,timeout=60)
subprocess.run(['codesign','--force','--sign','-',str(APP)],check=True,timeout=30)
subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True,timeout=30)
print('Built only, not launched:',APP)
print('SHA-256:',hashlib.sha256((BIN/executable).read_bytes()).hexdigest())
