"""Private internal runtime for C2. Runtime validation never accesses the external build volume.
Only the staging command below reads the previously approved external binaries.
No process, socket, audio API or OS permission operation occurs in this module.
"""
import hashlib,os,pathlib,re,shutil,stat,tempfile
from local_config import BACKEND_ROOT,CONFIG_HOME,ASSET_ROOT

ROOT=CONFIG_HOME/'relay-runtime'
BIN=ASSET_ROOT/'bin' if ASSET_ROOT else ROOT/'bin'
MAX_BYTES=16*1024**2
PRUNE_TARGET_BYTES=8*1024**2
PRUNE_KEEP_RUNS=20
MIN_FREE=1024**3
RUN_DIR_RE=re.compile(r'^relay-(?:null|speaker|application|application-timing|system|configured)-\d{8}-\d{6}$')
HASHES={
 'snapserver':'2f008a2dfbcc7cbdc4c6027f12ade7252ba77afab86cf226462a44f21d038351',
 'snapclient':'e3683f4a044a14eab596ba0f38ad64ddcc8696478c14ea099383ef28ca5fe1b1',
}

def _tree_bytes(path):
    total=path.stat().st_blocks*512
    for base,dirs,files in os.walk(path,followlinks=False):
        for name in dirs+files:
            p=pathlib.Path(base)/name
            if p.is_symlink():raise RuntimeError('Unexpected runtime symlink')
            total+=p.stat().st_blocks*512
    return total

def prune_runs(keep=PRUNE_KEEP_RUNS,max_bytes=PRUNE_TARGET_BYTES):
    """Rotate completed relay-* run directories at session startup before capacity trips."""
    if not ROOT.is_dir() or ROOT.is_symlink():return 0
    st=ROOT.stat()
    if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:return 0
    total=_tree_bytes(ROOT)
    runs=[]
    for p in ROOT.iterdir():
        if not p.is_symlink() and p.is_dir() and RUN_DIR_RE.fullmatch(p.name):
            runs.append((p.stat().st_mtime_ns,p.name,p,_tree_bytes(p)))
    runs.sort()
    removed=0
    while runs and (len(runs)>keep or total>max_bytes):
        _,_,path,size=runs.pop(0)
        shutil.rmtree(path)
        total=max(0,total-size)
        removed+=1
    return removed

def guard(verify=False,*,prune=None):
    if ASSET_ROOT and not ROOT.exists():
        ROOT.mkdir(mode=0o700,parents=True)
    if not ROOT.is_dir() or ROOT.is_symlink():raise RuntimeError('Private relay runtime is not staged')
    if ROOT.stat().st_uid!=os.getuid() or stat.S_IMODE(ROOT.stat().st_mode)!=0o700:
        raise RuntimeError('Runtime must be a private owner-only directory')
    if verify if prune is None else prune:
        prune_runs()
    total=_tree_bytes(ROOT)
    free=shutil.disk_usage(ROOT).free
    if total>MAX_BYTES or free<MIN_FREE:raise RuntimeError('16 MiB runtime / 1 GiB free-space guard')
    if verify:
        hashes=HASHES
        if ASSET_ROOT:
            import json
            manifest=ASSET_ROOT/'backend-hashes.json'
            if manifest.is_symlink():raise RuntimeError('Unexpected bundled manifest link')
            hashes=json.loads(manifest.read_text())
            if set(hashes)!=set(HASHES):raise RuntimeError('Unexpected bundled backend identities')
        for name,digest in hashes.items():
            p=BIN/name
            if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
                raise RuntimeError('Runtime executable identity mismatch: '+name)
    return total,free

def stage():
    """Copy only the two verified, approved executables, never source/cache trees."""
    source=BACKEND_ROOT/'snapcast-0.35.0/bin'
    if ROOT.exists():return guard(verify=True)
    if shutil.disk_usage(ROOT.parent).free<MIN_FREE+MAX_BYTES:raise RuntimeError('Staging free-space guard')
    data={name:(source/name).read_bytes() for name in HASHES}
    if any(hashlib.sha256(data[n]).hexdigest()!=HASHES[n] for n in HASHES):
        raise RuntimeError('Approved source executable identity changed')
    if sum(map(len,data.values()))>8*1024**2:raise RuntimeError('Unexpected runtime binary size')
    ROOT.mkdir(mode=0o700);BIN.mkdir(mode=0o700)
    for name,content in data.items():
        with (BIN/name).open('xb') as out:out.write(content)
        (BIN/name).chmod(0o500)
    return guard(verify=True,prune=False)

def dry_run():
    usage,free=guard(verify=True,prune=False)
    # A tiny task-owned file checks private scratch usability, not an audio file.
    with tempfile.TemporaryDirectory(prefix='preflight-',dir=ROOT) as temp:
        p=pathlib.Path(temp)/'probe.txt';p.write_text('Macbee runtime preflight\n')
        if p.read_text()!='Macbee runtime preflight\n':raise RuntimeError('Scratch check failed')
    return dict(runtime_bytes=usage,internal_free=free,audio_opened=False,network_opened=False,
                executable_run=False,external_volume_access=False,app_binary_changed=False)

if __name__=='__main__':
    import argparse,json
    parser=argparse.ArgumentParser();parser.add_argument('--stage',action='store_true');args=parser.parse_args()
    if args.stage:stage()
    print(json.dumps(dry_run(),indent=2))
