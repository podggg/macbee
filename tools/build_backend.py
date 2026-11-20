#!/usr/bin/env python3
"""Scoped Snapcast feasibility build. No install target, downloads or services.
Run only after approved source preparation and capacity verification.
"""
import os,pathlib,resource,shutil,signal,subprocess,time
from local_config import BACKEND_ROOT
ROOT=BACKEND_ROOT
SOURCE=ROOT/'snapcast-0.35.0'
LIMIT=4*1024**3
STOP_AT=LIMIT-512*1024**2
RESERVE=8*1024**3

def allocated():
    total=ROOT.stat().st_blocks*512
    for base,dirs,files in os.walk(ROOT,followlinks=False):
        for name in dirs+files:
            p=pathlib.Path(base)/name
            if p.is_symlink():raise RuntimeError('Unexpected link in scoped tree')
            try: total+=p.stat().st_blocks*512
            except FileNotFoundError: pass # compiler temporary disappeared between listing and stat
    return total

def guard():
    usage=allocated();free=shutil.disk_usage(ROOT).free
    if usage>STOP_AT or free<RESERVE+512*1024**2:
        raise RuntimeError(f'Capacity guard: {usage} allocated, {free} free; retaining files')
    return usage,free

def child_limits():
    # Two compiler jobs, each unable to create a surprise multi-GiB cache file.
    resource.setrlimit(resource.RLIMIT_FSIZE,(128*1024**2,128*1024**2))

def run(command,name):
    guard()
    env=os.environ.copy();env['COPYFILE_DISABLE']='1' # not relied on for budgeting
    env['TMPDIR']=str(ROOT/'tmp')
    env['PKG_CONFIG_PATH']=''
    env['PKG_CONFIG_LIBDIR']=str(ROOT/'empty-pkgconfig')
    with (ROOT/'logs'/name).open('w') as log:
        p=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,
                           start_new_session=True,preexec_fn=child_limits)
        deadline=time.monotonic()+900
        try:
            while p.poll() is None:
                guard()
                if time.monotonic()>deadline:raise TimeoutError('15 minute stage limit')
                time.sleep(2)
        except BaseException:
            os.killpg(p.pid,signal.SIGTERM)
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
        if p.returncode:raise RuntimeError(f'{name} failed, exit {p.returncode}; inspect scoped log')
    print(name,'passed, bytes allocated/free',guard(),flush=True)

if __name__=='__main__':
    guard()
    (ROOT/'tmp').mkdir(exist_ok=True)
    (ROOT/'empty-pkgconfig').mkdir(exist_ok=True)
    cmake='/opt/homebrew/bin/cmake'
    options=['-DCMAKE_BUILD_TYPE=Release','-DCMAKE_OSX_DEPLOYMENT_TARGET=14.2',
             '-DCMAKE_CXX_FLAGS=-fno-modules','-DCMAKE_MAKE_PROGRAM=/usr/bin/make','-DBOOST_ROOT='+str(ROOT/'boost_1_86_0'),
             '-DBoost_NO_BOOST_CMAKE=ON','-DBUILD_SHARED_LIBS=OFF','-DBUILD_STATIC_LIBS=ON',
             '-DBUILD_CLIENT=ON','-DBUILD_SERVER=ON','-DBUILD_TESTS=OFF',
             '-DCLANG_TIDY=OFF','-DCPPCHECK_BINARY=OFF','-DCLANG_FORMAT=OFF',
             '-DCMAKE_INSTALL_PREFIX='+str(ROOT/'unused-install')]
    for feature in ('SSL','ALSA','FLAC','VORBIS','TREMOR','OPUS','AVAHI','EXPAT','PULSE','JACK','PIPEWIRE','SDL2'):
        options.append('-DBUILD_WITH_'+feature+'=OFF')
    run([cmake,'-S',str(SOURCE),'-B',str(ROOT/'build')]+options,'configure.log')
    run([cmake,'--build',str(ROOT/'build'),'--parallel','2'],'build.log')
