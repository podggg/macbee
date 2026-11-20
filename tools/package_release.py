#!/usr/bin/env python3
"""Stage a local early-access bundle only. No install, launch, upload or capture."""
import argparse,hashlib,json,pathlib,plistlib,shutil,subprocess,sys
from build_icon import build_icon
from local_config import PROBE_PATH
from speaker_transport import PROBE_HASH

ROOT=pathlib.Path(__file__).resolve().parents[1]
VERSION='1.0.1'
MODULES=('ssh_discovery.py','ssh_control.py','session_diagnostics.py','local_config.py','relay_runtime.py','null_transport.py','speaker_transport.py',
         'application_protocol.py','application_transport.py','run_application_relay.py','audio_guardian.py')
SYSTEM_MODULES=('reader_clock.py','receiver_setup.py','receiver_registry.py','configured_transport.py','system_audio_protocol.py','system_audio_transport.py','run_system_relay.py')
LICENSES=('GPL-3.0.txt','BSL-1.0.txt','MIT-components.txt','THIRD-PARTY-NOTICES.txt')

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def package(backend,output,probe=PROBE_PATH,system_candidate=False):
    backend=backend.resolve();output=output.resolve()
    if not output.is_relative_to((ROOT/'.local-app').resolve()):raise RuntimeError('Package output must remain scoped')
    if output.exists():raise RuntimeError('Preserve an existing release candidate')
    if shutil.disk_usage(ROOT).free<1024**3:raise RuntimeError('1 GiB free-space guard')
    output.mkdir(parents=True)
    backend_manifest=json.loads((backend/'manifest.json').read_text())
    if system_candidate and (backend_manifest.get('reader_clock_version')!=3 or backend_manifest.get('reader_slot_version')!=1):raise RuntimeError('Matching bounded server reader is required')
    if system_candidate:
        if backend_manifest.get('darwin_worker_qos_version')!=1:
            raise RuntimeError('Matching rebuilt Snapserver and Snapclients with Darwin worker QoS are required')
        toolchain=backend_manifest.get('toolchain') or {}
        if toolchain.get('compiler')!='AppleClang' or not toolchain.get('compiler_version') or not toolchain.get('sdk_version'):
            raise RuntimeError('Matching native compiler and SDK provenance is required')
    version='1.2.6-dev' if system_candidate else VERSION
    subprocess.run([sys.executable,str(ROOT/'tools/build_application_candidate.py'),'--system-candidate' if system_candidate else '--user-candidate','--output-dir',str(output)],check=True)
    app=output/'Macbee.app';resources=app/'Contents/Resources';(resources/'tools').mkdir(parents=True);(resources/'bin').mkdir()
    for name in MODULES+(SYSTEM_MODULES if system_candidate else ()):shutil.copyfile(ROOT/'tools'/name,resources/'tools'/name)
    for name in ('snapserver','snapclient','snapclient-application'):
        source=backend/'bin'/name
        if source.is_symlink() or not source.is_file():raise RuntimeError('Missing sanitized backend: '+name)
        if digest(source)!=backend_manifest['binaries'][name]['sha256']:raise RuntimeError('Backend manifest mismatch: '+name)
        shutil.copyfile(source,resources/'bin'/name);(resources/'bin'/name).chmod(0o755)
    probe=pathlib.Path(probe)
    if probe.is_symlink() or digest(probe)!=PROBE_HASH:
        raise RuntimeError('Expected read-only device probe is required')
    shutil.copyfile(probe,resources/'bin'/probe.name);(resources/'bin'/probe.name).chmod(0o755)
    hashes={name:digest(resources/'bin'/name) for name in ('snapserver','snapclient')}
    (resources/'backend-hashes.json').write_text(json.dumps(hashes,indent=2))
    (resources/'application-client.json').write_text(json.dumps(dict(sha256=digest(resources/'bin/snapclient-application'),
        format='48000:16:2',stop_mode='wait-for-process-exit'),indent=2))
    (resources/'Licenses').mkdir()
    for name in LICENSES:
        source=backend/'licenses'/name
        if source.is_symlink() or digest(source)!=backend_manifest['files']['licenses/'+name]:
            raise RuntimeError('License manifest mismatch: '+name)
        shutil.copyfile(source,resources/'Licenses'/name)
    shutil.copyfile(ROOT/'docs/NEXT-USER-GUIDE.md',resources/'Read Me.md')
    build_icon(resources/'Macbee.icns')
    # Scan all shipped bytes, not only source text, before signing or archiving.
    needles=(b'/Users/',b'/Volumes/',b'/private/tmp/')
    for path in app.rglob('*'):
        if path.is_symlink():raise RuntimeError('Unexpected release symlink')
        if path.name.startswith('._') or path.name=='.DS_Store':raise RuntimeError('Unexpected release metadata')
        if path.is_file() and any(needle in path.read_bytes() for needle in needles):
            raise RuntimeError('Private build detail in '+str(path.relative_to(app)))
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if 'MacbeeDevelopmentRoot' in info or 'MacbeePythonExecutable' in info:raise RuntimeError('Development runtime leak')
    if info.get('MacbeeReleaseVersion')!=version or info.get('CFBundleShortVersionString')!=('1.2.6' if system_candidate else version):
        raise RuntimeError('Package and application versions differ')
    if system_candidate and (info.get('CFBundleVersion')!='10' or info.get('MacbeeBoundedSystemPacing') is not True):raise RuntimeError('Sender recovery gate or build number mismatch')
    if system_candidate and (info.get('MacbeeSystemAudioEnabled') is not True or info.get('MacbeeApplicationTestEnabled') is not False):raise RuntimeError('System candidate source gates mismatch')
    if system_candidate and (info.get('NSBonjourServices')!=['_ssh._tcp'] or not info.get('NSLocalNetworkUsageDescription')):
        raise RuntimeError('System candidate is missing local network discovery metadata')
    if system_candidate:
        askpass=app/'Contents/MacOS/macbee-askpass'
        if askpass.is_symlink() or not askpass.is_file() or not (askpass.stat().st_mode & 0o111):
            raise RuntimeError('Bundled SSH askpass helper is missing')
    if info.get('MacbeeManualSessionEnabled') is not True or 'MacbeeMaximumSharingSeconds' in info:
        raise RuntimeError('User release must use explicit manual Start/Stop')
    subprocess.run(['codesign','--force','--sign','-',str(app)],check=True,timeout=30)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,timeout=30)
    manifest={str(path.relative_to(app)):digest(path) for path in app.rglob('*') if path.is_file()}
    (output/'bundle-sha256.json').write_text(json.dumps(manifest,indent=2,sort_keys=True))
    archive=output/('Macbee-'+version+'-arm64.zip')
    subprocess.run(['ditto','-c','-k','--keepParent','--norsrc','--noextattr',str(app),str(archive)],check=True,timeout=30)
    (output/'SHA256SUMS').write_text(digest(archive)+'  '+archive.name+'\n')
    (output/'release-manifest.json').write_text(json.dumps(dict(source_commit=subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),archive_sha256=digest(archive),
        source_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
        backend_source=backend_manifest['source_archive'],backend_toolchain=backend_manifest.get('toolchain'),architecture='arm64',macos_minimum='14.2',
        python='/usr/bin/python3 3.9+',receiver='Existing configured SSH receiver required',
        signing='Ad-hoc; not Developer ID signed or notarized'),indent=2))
    print(json.dumps(dict(app=str(app),archive=str(archive),sha256=digest(archive),bytes=archive.stat().st_size)))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backend',type=pathlib.Path,required=True);parser.add_argument('--output',type=pathlib.Path,required=True)
    parser.add_argument('--probe',type=pathlib.Path,default=PROBE_PATH)
    parser.add_argument('--system-candidate',action='store_true',help='Stage isolated 1.2.6-dev configured-output candidate, never install or launch')
    args=parser.parse_args();package(args.backend,args.output,args.probe,args.system_candidate)
