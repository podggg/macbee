#!/usr/bin/env python3
"""Verify exact archives/source and enumerate only generated AppleDouble metadata.
--remove-appledouble is reserved for the owner's separately approved cleanup.
Never follows links. Never deletes archives, source, directories, or another tree.
"""
import argparse,hashlib,json,os,pathlib,tarfile
from local_config import BACKEND_ROOT
ROOT=BACKEND_ROOT
ARCHIVES={
 'snapcast-v0.35.0.tar.gz':'cb75a71479bf52910bf5f47ae8120ec41c89459b0d77d7cd560e674e437ef050',
 'boost_1_86_0.tar.bz2':'1bed88e40401b2cb7a1f76d4bab499e352fa4d0c5f31c0dbae64e24d34d7513b'}
def digest(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def verify():
 all_sources=hashlib.sha256();count=0
 for name,expected in ARCHIVES.items():
  path=ROOT/'downloads'/name
  if path.is_symlink() or digest(path)!=expected:raise RuntimeError('Archive verification failed')
  with tarfile.open(path) as archive:
   for m in archive.getmembers():
    p=pathlib.PurePosixPath(m.name)
    selected=name.startswith('snapcast') or (len(p.parts)>1 and p.parts[1] in ('boost','LICENSE_1_0.txt'))
    if not selected or not m.isfile():continue
    actual=ROOT/m.name
    if p.is_absolute() or '..' in p.parts or actual.is_symlink():raise RuntimeError('Unexpected source path')
    expected_file=hashlib.file_digest(archive.extractfile(m),'sha256').hexdigest()
    if digest(actual)!=expected_file:raise RuntimeError('Source mismatch: '+m.name)
    all_sources.update(m.name.encode());all_sources.update(bytes.fromhex(expected_file));count+=1
 return {'source_files':count,'source_manifest_sha256':all_sources.hexdigest()}
def inventory():
 metadata=[];allocated=0
 for base,dirs,files in os.walk(ROOT,followlinks=False):
  for name in dirs+files:
   path=pathlib.Path(base)/name
   if path.is_symlink():raise RuntimeError('Unexpected symlink: '+str(path))
   allocated+=path.stat().st_blocks*512
   if name.startswith('._'):
    if not path.is_file() or not path.with_name(name[2:]).exists():raise RuntimeError('Unmatched metadata: '+str(path))
    with path.open('rb') as f:
     if f.read(4)!=b'\x00\x05\x16\x07':raise RuntimeError('Not AppleDouble: '+str(path))
    metadata.append(path)
 return metadata,allocated
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--remove-appledouble',action='store_true');args=p.parse_args()
 if ROOT.is_symlink() or ROOT.resolve()!=ROOT:raise RuntimeError('Root is not exact expected real directory')
 before=verify();metadata,total=inventory()
 print(json.dumps({'before':before,'allocated_bytes':total,'appledouble_files':len(metadata),'appledouble_allocated_bytes':sum(p.stat().st_blocks*512 for p in metadata)},indent=2),flush=True)
 if args.remove_appledouble:
  for path in metadata:path.unlink()
  after=verify();remaining,total=inventory()
  if after!=before:raise RuntimeError('Post-cleanup source validation failed')
  print(json.dumps({'after':after,'allocated_bytes':total,'remaining_metadata_files':len(remaining)},indent=2),flush=True)
