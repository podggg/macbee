"""Make a small private-canary stage using an exact, already available source base.
The .tar.gz addendum is NOT standalone: both SHA-verified source archives are
required. Publish a full, standalone corresponding-source archive for a release.
No compiler, network, app launch, or live settings. Never modify the base stage.
"""
import argparse,gzip,hashlib,io,json,pathlib,shutil,subprocess,tarfile
from release_backend import ARCHIVES,FREE_MIN,NOTICE_FILES,compiler_provenance,digest,patches,verify_binary
from snapserver_clock_patch import PATH

ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE_SHA='7b5cc623762f70e3e8d2bb22a6de4542c85f23561e4ad6a224d4eb5ac1a0fc20'
PREFIX='macbee-backend-source/'

def encode(value):return (json.dumps(value,indent=2,sort_keys=True)+'\n').encode()

def stage(base,base_stage,server,header,output,cmake_build):
    output=output.resolve()
    if output.exists() or not output.is_relative_to((ROOT/'.local-app').resolve()):
        raise ValueError('Require a new scoped staging directory')
    if shutil.disk_usage(ROOT).free < FREE_MIN+24*1024**2:raise RuntimeError('Respect the 8.5 GiB reserve')
    old=json.loads((base_stage/'manifest.json').read_text())
    if base.is_symlink() or digest(base)!=BASE_SHA or old['source_archive']['sha256']!=BASE_SHA:
        raise ValueError('Not the verified original corresponding-source base')
    if server.is_symlink() or header.is_symlink():raise ValueError('Refuse linked candidate inputs')
    info=verify_binary(server,[b'/private/tmp/',b'/volumes/',b'/users/',b'macbee_inert',b'sigstop'])
    with tarfile.open(base) as tar:
        def take(name):return tar.extractfile(PREFIX+name).read()
        original_recipe=json.loads(take('tools/release-patches.json'))
        build=json.loads(take('build-manifest.json'))
        upstream=take('upstream/snapcast-v0.35.0.tar.gz')
        old_notice=take('notices/THIRD-PARTY-NOTICES.txt').decode()
    if hashlib.sha256(upstream).hexdigest()!=ARCHIVES['snapcast-v0.35.0.tar.gz']:raise ValueError('Upstream archive drift')
    with tarfile.open(fileobj=io.BytesIO(upstream),mode='r:gz') as tar:
        text=tar.extractfile('snapcast-0.35.0/'+PATH).read().decode()
    recipe=patches()
    if [list(row) for row in recipe if row[0]!=PATH]!=original_recipe:raise ValueError('Unrelated patch change')
    for path,before,after in recipe:
        if path!=PATH:continue
        if text.count(before)!=1:raise ValueError('Upstream patch does not apply exactly')
        text=text.replace(before,after)
    if text.encode()!=header.read_bytes():raise ValueError('Compiled source does not match reconstructed header')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise ValueError('Commit the verified source before staging')
    tc=dict(compiler_provenance(cmake_build),scope="rebuilt_snapserver",binary_sha256=info["sha256"],header_sha256=digest(header))
    build.update(toolchain=tc,reader_slot_version=1,reader_clock_version=3,source_commit=commit)
    build['binaries']['snapserver']=info
    note=old_notice.replace('the four modified source files','all modified source files')
    note+='\nFor this private canary the exact corresponding source is the verified base AND its matching reader-clock source addendum. A standalone release must include the complete source.\n'
    files={'BUILDING.md':(ROOT/'docs/RELEASE-BACKEND.md').read_bytes(),
           'tools/release_backend.py':(ROOT/'tools/release_backend.py').read_bytes(),
           'tools/release-patches.json':encode(recipe),
           'build-manifest.json':encode(build),'modified-sources/'+PATH:text.encode(),
           'notices/THIRD-PARTY-NOTICES.txt':note.encode(),
           'README-READER-CLOCK-ADDENDUM.txt':(
             'Use BOTH files. Verify base SHA256 '+BASE_SHA+' and the addendum SHA256 in the canary release manifest.\n'
             'Unpack the authenticated base first, then this addendum into the same NEW empty working parent. '
             'The same macbee-backend-source prefix intentionally supplies the updated documentation, complete patch '
             'recipe, modified reader header, matching notices and build manifest. All other original files remain. '
             'Follow BUILDING.md for a full clean, offline build.\n').encode()}
    output.mkdir(parents=True);(output/'bin').mkdir();(output/'licenses').mkdir()
    delta=output/'macbee-reader-clock-source-addendum.tar.gz'
    with delta.open('xb') as stream,gzip.GzipFile(fileobj=stream,mode='wb',mtime=0) as gz,tarfile.open(fileobj=gz,mode='w') as tar:
        for key,data in sorted(files.items()):
            i=tarfile.TarInfo(PREFIX+key);i.size=len(data);i.mode=0o644
            tar.addfile(i,io.BytesIO(data))
    with tarfile.open(delta) as tar:
        if {m.name for m in tar.getmembers()}!={PREFIX+x for x in files}:raise RuntimeError('Addendum membership mismatch')
        for key,v in files.items():
            if tar.extractfile(PREFIX+key).read()!=v:raise RuntimeError('Addendum readback differs')
    for key in ('snapserver','snapclient','snapclient-application'):
        src=server if key=='snapserver' else base_stage/'bin'/key
        expected=info['sha256'] if key=='snapserver' else old['binaries'][key]['sha256']
        if src.is_symlink() or digest(src)!=expected:raise ValueError('Mismatched binary')
        dst=output/'bin'/key;shutil.copyfile(src,dst);dst.chmod(0o755)
    for key in NOTICE_FILES:
        src=base_stage/'licenses'/key
        if src.is_symlink() or digest(src)!=old['files']['licenses/'+key]:raise ValueError('Mismatched license')
        (output/'licenses'/key).write_bytes(note.encode() if key=='THIRD-PARTY-NOTICES.txt' else src.read_bytes())
    descriptor={'type':'base-and-addendum','requires_both':True,'base':old['source_archive'],
                'addendum':{'name':delta.name,'bytes':delta.stat().st_size,'sha256':digest(delta)}}
    table={str(x.relative_to(output)):digest(x) for d in ('bin','licenses') for x in (output/d).iterdir()}
    manifest={'toolchain':tc,'reader_slot_version':1,'reader_clock_version':3,'source_archive':descriptor,'binaries':build['binaries'],'files':table}
    (output/'manifest.json').write_bytes(encode(manifest))
    return descriptor

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('base','base-stage','server','header','output','cmake-build'):p.add_argument('--'+key,type=pathlib.Path,required=True)
    a=p.parse_args();print(json.dumps(stage(a.base,a.base_stage,a.server,a.header,a.output,a.cmake_build),indent=2))
