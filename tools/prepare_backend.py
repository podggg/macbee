#!/usr/bin/env python3
"""Approved, scoped download/extract only. Does not install or execute upstream code."""
import hashlib,json,math,os,pathlib,shutil,tarfile,urllib.request
from local_config import BACKEND_ROOT
ROOT=BACKEND_ROOT
LIMIT=4*1024**3
RESERVE=8*1024**3
CLUSTER=131072
SOURCES=[
 ('snapcast-v0.35.0.tar.gz','https://github.com/snapcast/snapcast/archive/refs/tags/v0.35.0.tar.gz','cb75a71479bf52910bf5f47ae8120ec41c89459b0d77d7cd560e674e437ef050',32*1024**2),
 ('boost_1_86_0.tar.bz2','https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.bz2','1bed88e40401b2cb7a1f76d4bab499e352fa4d0c5f31c0dbae64e24d34d7513b',127*1024**2)]

def allocated():
    return sum(p.stat().st_blocks*512 for p in ROOT.rglob('*') if not p.is_symlink())

def guard(extra=0):
    if allocated()+extra>LIMIT or shutil.disk_usage(ROOT).free-extra<RESERVE:
        raise RuntimeError('Capacity guard: retain files and stop, do not delete other work')

ROOT.mkdir(exist_ok=True)
(ROOT/'downloads').mkdir(exist_ok=True)
(ROOT/'logs').mkdir(exist_ok=True)
manifest=[]
for name,url,expected,maximum in SOURCES:
    destination=ROOT/'downloads'/name
    if not destination.exists():
        guard(maximum)
        digest=hashlib.sha256();length=0
        with urllib.request.urlopen(url,timeout=30) as response, destination.open('xb') as out:
            while chunk:=response.read(1024**2):
                length+=len(chunk)
                if length>maximum:raise RuntimeError('Unexpected archive size; retained for review')
                digest.update(chunk);out.write(chunk)
        actual=digest.hexdigest()
    else:
        actual=hashlib.file_digest(destination.open('rb'),'sha256').hexdigest()
        length=destination.stat().st_size
    if actual!=expected:raise RuntimeError('Checksum mismatch: '+name)
    print('Verified',name,length,actual,flush=True)
    with tarfile.open(destination) as archive:
        members=[];directories=set();estimate=0
        for member in archive.getmembers():
            path=pathlib.PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:raise RuntimeError('Unsafe archive path')
            wanted = name.startswith('snapcast') or (len(path.parts)>1 and (path.parts[1]=='boost' or path.parts[1]=='LICENSE_1_0.txt'))
            if not wanted:continue
            if member.isdir():directories.add(path);continue
            if not member.isfile():raise RuntimeError('Unexpected archive link/device '+member.name)
            members.append(member)
            estimate+=math.ceil(member.size/CLUSTER)*CLUSTER+CLUSTER # budget automatic AppleDouble companion
            directories.update(path.parents)
        estimate+=2*(len(directories)+4)*CLUSTER # directories may also acquire companions
        # Leave >= 512 MiB within the agreed maximum for compilation outputs.
        if allocated()+estimate>LIMIT-512*1024**2:raise RuntimeError('Extraction estimate exceeds safe build budget')
        guard(estimate)
        print('Extraction estimate',name,'files',len(members),'allocated_bytes',estimate,flush=True)
        for index,member in enumerate(members):
            if index%128==0:guard(64*1024**2)
            path=ROOT/member.name
            if path.exists():raise RuntimeError('Refusing to overwrite extracted source '+str(path))
            path.parent.mkdir(parents=True,exist_ok=True)
            source=archive.extractfile(member)
            with path.open('xb') as out:shutil.copyfileobj(source,out)
            os.chmod(path,0o644)
        manifest.append({'name':name,'url':url,'sha256':actual,'archive_bytes':length,'selected_files':len(members),'allocation_estimate':estimate})
    guard()
(ROOT/'logs'/'sources.json').write_text(json.dumps(manifest,indent=2))
print('Prepared bytes allocated:',allocated(),flush=True)
