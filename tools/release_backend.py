#!/usr/bin/env python3
"""Offline, capacity-guarded, isolated Snapcast release build. Never launch/install.
The two upstream archives and instrumentation patch specifications are the inputs.
No network access, dependency installation, original source/object edits, or audio.
"""
import argparse
import ast
import hashlib
import io
import json
import os
import plistlib
from pathlib import Path, PurePosixPath
import re
import resource
import shlex
import shutil
import signal
import socket
import subprocess
import tarfile
import time

ARCHIVES = {
    'snapcast-v0.35.0.tar.gz': 'cb75a71479bf52910bf5f47ae8120ec41c89459b0d77d7cd560e674e437ef050',
    'boost_1_86_0.tar.bz2': '1bed88e40401b2cb7a1f76d4bab499e352fa4d0c5f31c0dbae64e24d34d7513b',
}
BASE_SHA = '5f2722e1c09dc739b500d9f3b2e722c9b973aedbfd13a8da30299789a68f5e97'
FULL_SHA = '929376a2cae582218e4c50edff12378972f9867af3caea3717d2189c5db7d81b'
DARWIN_WORKER_QOS_VERSION = 1
DISABLED_FEATURES = ('SSL', 'ALSA', 'FLAC', 'VORBIS', 'TREMOR', 'OPUS', 'AVAHI',
                     'EXPAT', 'PULSE', 'JACK', 'PIPEWIRE', 'SDL2', 'SOXR')
STOP_AT = 3584 * 1024**2
FREE_MIN = 8704 * 1024**2
ESTIMATE = 400 * 1024**2
NOTICE_FILES = ('GPL-3.0.txt', 'BSL-1.0.txt', 'MIT-components.txt', 'THIRD-PARTY-NOTICES.txt')
GPL_HEADER_END = (
    '    You should have received a copy of the GNU General Public License\n'
    '    along with this program.  If not, see <http://www.gnu.org/licenses/>.\n'
    '***/\n'
)
MODIFICATION_NOTICE_DATE = '2026-11-19'
MODIFICATION_NOTICE_RE = re.compile(
    r'^// Modified for Macbee \(https://github\.com/podggg/macbee\), \d{4}-\d{2}-\d{2}:$',
    re.M,
)
MODIFICATION_NOTICES = {
    'client/player/file_player.hpp': (
        '// Added bounded synthetic null-sink diagnostic counter members.'
    ),
    'client/player/file_player.cpp': (
        '// Added synthetic null-sink pattern counters and Darwin playback-worker QoS promotion.'
    ),
    'client/player/coreaudio_player.hpp': (
        '// Initialized lastChunkTick and added CoreAudio diagnostic/priming state members.'
    ),
    'client/player/coreaudio_player.cpp': (
        '// Added CoreAudio metadata counters, startup priming-clock handling, tick\n'
        '// initialization, fail-closed runtime timing guard, synthetic diagnostic ceiling,\n'
        '// and Darwin playback-worker QoS promotion.'
    ),
    'server/streamreader/asio_stream.hpp': (
        '// Added opt-in V3 bounded pipe-clock telemetry, stale-media fail-closed guards,\n'
        '// presentation-slot bridging, and bounded silence recovery for local stdin pipes.'
    ),
    'server/snapserver.cpp': (
        '// Added Darwin playback-worker QoS promotion for Asio server I/O threads.'
    ),
}
MIT = '''Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
'''


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def darwin_configure_options(root,work,boost):
    flags = '-fno-modules ' + ' '.join(shlex.quote(option + str(root) + '=/macbee-build')
        for option in ('-ffile-prefix-map=', '-fmacro-prefix-map=', '-fdebug-prefix-map='))
    options = [
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_OSX_SYSROOT=macosx', '-DCMAKE_OSX_DEPLOYMENT_TARGET=14.2',
        '-DCMAKE_OSX_ARCHITECTURES=', '-DCMAKE_CXX_FLAGS=' + flags,
        '-DCMAKE_C_FLAGS=' + flags, '-DCMAKE_MAKE_PROGRAM=/usr/bin/make',
        '-DBOOST_ROOT=' + str(boost), '-DBoost_NO_BOOST_CMAKE=ON',
        '-DBUILD_SHARED_LIBS=OFF', '-DBUILD_STATIC_LIBS=ON', '-DBUILD_CLIENT=ON',
        '-DBUILD_SERVER=ON', '-DBUILD_TESTS=OFF', '-DCLANG_TIDY=OFF',
        '-DCPPCHECK_BINARY=OFF', '-DCLANG_FORMAT=OFF',
        '-DCMAKE_INSTALL_PREFIX=' + str(work / 'unused-install'),
    ]
    options += ['-DBUILD_WITH_' + name + '=OFF' for name in DISABLED_FEATURES]
    return options


def compiler_provenance(build):
    """Use the configured compiler and the actual target -isysroot, not today's default SDK."""
    build=Path(build)
    files=list((build/'CMakeFiles').glob('*/CMakeCXXCompiler.cmake'))
    if len(files)!=1:raise ValueError('Expected one configured C++ compiler')
    meta=files[0].read_text()
    def field(name,pattern):
        match=re.findall(r'^set\(CMAKE_CXX_COMPILER_'+name+' "('+pattern+r')"\)$',meta,re.M)
        if len(match)!=1:raise ValueError('Incomplete compiler provenance')
        return match[0]
    compiler=field('ID','AppleClang')
    version=field('VERSION',r'[0-9]+(?:[.][0-9]+){1,4}')
    flags_file=build/'server/CMakeFiles/snapserver.dir/flags.make'
    rows=re.findall(r'^CXX_FLAGS = (.*)$',flags_file.read_text(),re.M)
    flags=shlex.split(rows[0]) if len(rows)==1 else []
    if flags.count('-isysroot')!=1 or flags.index('-isysroot')+1>=len(flags):
        raise ValueError('Missing exact target SDK')
    sdk=Path(flags[flags.index('-isysroot')+1])
    settings=plistlib.loads((sdk/'SDKSettings.plist').read_bytes())
    sdk_version=settings.get('Version','')
    if not re.fullmatch(r'[0-9]+(?:[.][0-9]+){1,3}',sdk_version):raise ValueError('Invalid SDK version')
    return dict(compiler=compiler,compiler_version=version,sdk='macosx',sdk_version=sdk_version,
                compiler_metadata_sha256=digest(files[0]),target_flags_sha256=digest(flags_file))


def allocation(root):
    total = root.stat().st_blocks * 512
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(base) / name
            if p.is_symlink():
                raise RuntimeError('Unexpected symlink in build scope')
            try:
                total += p.stat().st_blocks * 512
            except FileNotFoundError:
                pass  # Compiler temporary removed after enumeration.
    return total


def guard(root, extra=0):
    used, free = allocation(root), shutil.disk_usage(root).free
    if used + extra > STOP_AT or free - extra < FREE_MIN:
        raise RuntimeError('Capacity guard: retaining files, not continuing')
    return {'allocated_bytes': used, 'free_bytes': free}


def modification_notice_block(rel):
    summary = MODIFICATION_NOTICES.get(rel)
    if not summary:
        raise RuntimeError('Missing GPLv3 Section 5(a) modification notice for ' + rel)
    return (
        f'\n// Modified for Macbee (https://github.com/podggg/macbee), {MODIFICATION_NOTICE_DATE}:\n'
        f'{summary}\n'
        '// Licensed under GPL-3.0-or-later; upstream copyright notices retained above.\n'
    )


def modification_notice_specs(paths):
    return [[rel, GPL_HEADER_END, GPL_HEADER_END + modification_notice_block(rel)]
            for rel in sorted(set(paths))]


def verify_modification_notice(rel, text):
    header_pos = text.find(GPL_HEADER_END)
    if header_pos < 0:
        raise RuntimeError('Missing preserved upstream GPL header in ' + rel)
    top = '\n'.join(text.splitlines()[:30])
    match = MODIFICATION_NOTICE_RE.search(top)
    if not match or match.start() < header_pos + len(GPL_HEADER_END):
        raise RuntimeError('Missing prominent GPLv3 Section 5(a) modification notice in ' + rel)
    if 'GPL-3.0-or-later' not in top:
        raise RuntimeError('Missing GPL-3.0-or-later notice in ' + rel)


def apply_file_patches(rel, text, specs):
    changes = [(old, new) for path, old, new in specs if path == rel]
    if not changes:
        return text
    for old, new in changes:
        if text.count(old) != 1:
            raise RuntimeError('Unexpected upstream patch target in ' + rel)
        text = text.replace(old, new)
    verify_modification_notice(rel, text)
    return text


def make_full_range_coreaudio(source_text):
    replacements = (
        ('// initialization, fail-closed runtime timing guard, synthetic diagnostic ceiling,',
         '// initialization, fail-closed runtime timing guard, full-range S16 playback,'),
        ('Diagnostic safety ceiling: only this agreed low-amplitude synthetic test.',
         'Selected-app full-range S16 path. No extra gain or synthetic amplitude clamp.'),
        ('if(magnitude>164)', 'if(magnitude>32768)'),
    )
    result = source_text
    for old, new in replacements:
        if result.count(old) != 1:
            raise RuntimeError('Unexpected full-range patch target')
        result = result.replace(old, new)
    verify_modification_notice('full-range/coreaudio_player.cpp', result)
    return result


def render_third_party_notices():
    return f'''Macbee distribution and third-party notices
Macbee: Copyright (C) 2026 podggg.
GPL-3.0-or-later; full GPLv3 supplied in GPL-3.0.txt. No warranty.
Source: https://github.com/podggg/macbee

Snapcast 0.35.0: Copyright (C) 2014-2025 Johannes Pohl and contributors.
GPL-3.0-or-later; full GPLv3 supplied in GPL-3.0.txt. No warranty.
Source: https://github.com/snapcast/snapcast/tree/v0.35.0
Boost 1.86.0: Boost Software License 1.0 (BSL-1.0.txt).
https://www.boost.org/users/history/version_1_86_0.html
Bundled aixlog, popl, and nlohmann/json: MIT; see MIT-components.txt.

Macbee backend modifications, {MODIFICATION_NOTICE_DATE}: bounded null-sink and CoreAudio
metadata counters, priming-clock handling, initialization, full-range S16
playback variant, bounded pipe-clock recovery, Darwin playback-worker QoS,
GPLv3 Section 5(a) file-level modification notices, and sanitized isolated build recipe.
Existing upstream notices are retained. Snapcast modifications are supplied
under GPL-3.0-or-later, without warranty, as part of this derivative backend.

Corresponding source: macbee-backend-corresponding-source.tar.gz accompanying
the same release. It contains both exact upstream archives, the complete patch
specification, all modified source files, full-range variant, and build
recipe/flags. Source must remain freely accessible to each binary recipient.
No restriction on recipients' GPL rights is added by any distribution.
Apple system frameworks and general-purpose build tools are not distributed.
'''


def patches():
    folder = Path(__file__).resolve().parent
    if (folder / 'release-patches.json').is_file():
        return json.loads((folder / 'release-patches.json').read_text())
    result = []
    for name in ('instrument_null_sink.py', 'instrument_coreaudio.py'):
        tree = ast.parse((folder / name).read_text())
        calls = sorted((node for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'replace'), key=lambda node: node.lineno)
        for call in calls:
            path = call.args[0]
            if not (isinstance(path, ast.BinOp) and isinstance(path.op, ast.Div)
                    and isinstance(path.left, ast.Name) and path.left.id == 'ROOT'):
                raise RuntimeError('Unsupported patch expression')
            result.append([ast.literal_eval(path.right), ast.literal_eval(call.args[1]),
                           ast.literal_eval(call.args[2])])
    from snapserver_clock_patch import specs as reader_clock_specs
    result.extend([list(item) for item in reader_clock_specs()])
    from snapcast_qos_patch import specs as worker_qos_specs
    result.extend([list(item) for item in worker_qos_specs()])
    return modification_notice_specs(item[0] for item in result) + result


def extract(archive, destination, top, selected=None):
    """Only regular files/directories, no traversal, links, or arbitrary tar metadata."""
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as tar:
        for member in tar:
            p = PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != top:
                raise RuntimeError('Unsafe archive path')
            rel = p.parts[1:]
            if selected is not None and rel and rel[0] not in selected:
                continue
            if member.isdir():
                (destination.joinpath(*rel)).mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target = destination.joinpath(*rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open('xb') as out:
                    shutil.copyfileobj(source, out)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            else:
                raise RuntimeError('Archive contains a non-regular entry')


def verify_boost(root):
    """Prove that every used pre-existing Boost header is the pinned source."""
    expected = set()
    with tarfile.open(root / 'downloads/boost_1_86_0.tar.bz2') as tar:
        for member in tar:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2 or parts[1] not in ('boost', 'LICENSE_1_0.txt') or not member.isfile():
                continue
            expected.add('/'.join(parts[1:]))
            if (root / member.name).read_bytes() != tar.extractfile(member).read():
                raise RuntimeError('Pinned Boost source mismatch')
    actual = {str(p.relative_to(root / 'boost_1_86_0')) for p in (root / 'boost_1_86_0').rglob('*')
              if p.is_file() and not p.name.startswith('._') and p.name != '.DS_Store'}
    if actual != expected:
        raise RuntimeError('Unexpected file in Boost header tree')


def child_limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))


def run(command, name, root, work, env, cwd=None):
    guard(root)
    with (work / 'logs' / (name + '.log')).open('w') as log:
        proc = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True, preexec_fn=child_limit)
        deadline = time.monotonic() + 900
        try:
            while proc.poll() is None:
                guard(root)
                if time.monotonic() > deadline:
                    raise RuntimeError('15-minute build stage deadline')
                time.sleep(2)
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            raise
        if proc.returncode:
            raise RuntimeError(name + ' failed; retained private stage log')
    print(name + ' passed', json.dumps(guard(root)), flush=True)


def snapshot_originals(root):
    # Only the existing frozen build/source/client inputs. New release paths excluded.
    result = {}
    for name in ('snapcast-0.35.0', 'build', 'application-renderer', 'timing-renderer'):
        base = root / name
        if not base.exists():
            continue
        for p in base.rglob('*'):
            if p.is_file():
                stat = p.stat()
                result[str(p.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, digest(p))
    return result


def forbidden(root):
    tokens = [str(root), str(Path.home()), Path.home().name, socket.gethostname()]
    if len(root.parts) > 2 and root.parts[1] == 'Volumes':
        tokens.append(root.parts[2])
    return [t.encode().lower() for t in tokens if len(t) > 3]


def verify_binary(path, tokens):
    raw = path.read_bytes()
    lower = raw.lower()
    if b'/private/tmp/' in lower:raise RuntimeError('Temporary build path in release binary')
    if any(t in lower for t in tokens) or re.search(rb'/(?:Users|Volumes|private/var/folders)/', raw):
        raise RuntimeError('Private build identity found in ' + path.name)
    description = subprocess.check_output(['/usr/bin/file', '-b', str(path)], text=True)
    if 'Mach-O 64-bit executable arm64' not in description:
        raise RuntimeError('Unexpected executable architecture')
    output = subprocess.check_output(['/usr/bin/otool', '-L', str(path)], text=True)
    libraries = [line.strip().split(' (', 1)[0] for line in output.splitlines()[1:]]
    if not all(x.startswith(('/usr/lib/', '/System/Library/Frameworks/')) for x in libraries):
        raise RuntimeError('Non-system dynamic dependency')
    return {'sha256': digest(path), 'architecture': 'arm64', 'dynamic_libraries': libraries}


def package(root, work, app, specs, manifest):
    source = work / 'source'
    notices = work / 'notices'
    notices.mkdir(exist_ok=True)
    shutil.copyfile(source / 'LICENSE', notices / 'GPL-3.0.txt')
    shutil.copyfile(root / 'boost_1_86_0/LICENSE_1_0.txt', notices / 'BSL-1.0.txt')
    copyrights = (
        'aixlog 1.5.1: Copyright (c) 2017-2021 Johannes Pohl; bundled header (C) 2017-2025 Johannes Pohl',
        'popl 1.3.1 (header label): Copyright (c) 2015-2016 Johannes Pohl; bundled header (C) 2015-2021 Johannes Pohl',
        'nlohmann/json 3.11.3: Copyright (c) 2013-2022 Niels Lohmann; '
        'bundled header SPDX-FileCopyrightText: 2013-2023 Niels Lohmann',
    )
    (notices / 'MIT-components.txt').write_text('\n'.join(copyrights) + '\n\n' + MIT)
    (notices / 'THIRD-PARTY-NOTICES.txt').write_text(render_third_party_notices())
    folder = Path(__file__).resolve().parent
    docs = folder.parent / 'docs/RELEASE-BACKEND.md'
    if not docs.exists():
        docs = folder.parent / 'BUILDING.md'
    inputs = {}
    for name in ARCHIVES:
        inputs['upstream/' + name] = root / 'downloads' / name
    inputs['tools/release_backend.py'] = Path(__file__).resolve()
    inputs['BUILDING.md'] = docs
    for name in NOTICE_FILES:
        inputs['notices/' + name] = notices / name
    for rel in sorted({item[0] for item in specs}):
        verify_modification_notice(rel, (source / rel).read_text())
        inputs['modified-sources/' + rel] = source / rel
    verify_modification_notice('full-range/coreaudio_player.cpp', (app / 'coreaudio_player.cpp').read_text())
    inputs['modified-sources/full-range/coreaudio_player.cpp'] = app / 'coreaudio_player.cpp'
    extra = {
        'tools/release-patches.json': json.dumps(specs, indent=2).encode(),
        'build-manifest.json': json.dumps(manifest, indent=2).encode(),
    }
    target = work / 'macbee-backend-corresponding-source.tar.gz'
    guard(root, sum(p.stat().st_size for p in inputs.values()) + 8 * 1024**2)
    with target.open('xb') as out, tarfile.open(fileobj=out, mode='w:gz') as tar:
        for name, path in sorted(inputs.items()):
            info = tarfile.TarInfo('macbee-backend-source/' + name)
            info.size = path.stat().st_size
            info.mode = 0o644
            with path.open('rb') as data:
                tar.addfile(info, data)
        for name, data in sorted(extra.items()):
            info = tarfile.TarInfo('macbee-backend-source/' + name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    # No caches, private logs/config, binaries, SSH details, or archive owner identity.
    with tarfile.open(target) as tar:
        for item in tar:
            if any(p.startswith('._') or p == '.DS_Store' for p in PurePosixPath(item.name).parts):
                raise RuntimeError('AppleDouble metadata in source asset')
            if item.uid or item.gid or item.uname or item.gname:
                raise RuntimeError('Unexpected archive owner metadata')
            if item.name.endswith(('.tar.gz', '.tar.bz2')):
                continue  # Inputs already pinned by public upstream hashes.
            data = tar.extractfile(item).read().lower()
            if any(token in data for token in forbidden(root)):
                raise RuntimeError('Private identity in corresponding source asset')
    return {'sha256': digest(target), 'bytes': target.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='existing dedicated offline build scope')
    parser.add_argument('--cmake', default='cmake')
    parser.add_argument('--plan', action='store_true', help='read-only archive/capacity preflight')
    parser.add_argument('--resume', action='store_true', help='resume a failed configuration before any binary was produced')
    args = parser.parse_args()
    root = args.root
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        raise RuntimeError('Root must be an existing real absolute dedicated directory')
    for name, sha in ARCHIVES.items():
        p = root / 'downloads' / name
        if p.is_symlink() or digest(p) != sha:
            raise RuntimeError('Upstream archive verification failed')
    work, app = root / 'release-build', root / 'release-application-renderer'
    if work.exists() or app.exists():
        if not args.resume or not (work / 'source').is_dir() or not app.is_dir():
            raise RuntimeError('Release output already exists; never overwrite')
        if any((work / 'source/bin').glob('*')) or any(app.iterdir()) or (work / 'backend-manifest.json').exists():
            raise RuntimeError('Resume is limited to configuration failure before binary outputs')
    elif args.resume:
        raise RuntimeError('No failed configuration to resume')
    specs = patches()
    print('preflight', json.dumps(guard(root, ESTIMATE)), 'estimated additional bytes', ESTIMATE, flush=True)
    if args.plan:
        return
    before = snapshot_originals(root)
    work.mkdir(exist_ok=args.resume); app.mkdir(exist_ok=args.resume)
    for name in ('logs', 'tmp', 'empty-pkgconfig'):
        (work / name).mkdir(exist_ok=args.resume)
    source, build = work / 'source', work / 'build'
    if not args.resume:
        extract(root / 'downloads/snapcast-v0.35.0.tar.gz', source, 'snapcast-0.35.0')
    boost = root / 'boost_1_86_0'
    if not boost.exists():
        # A recipient's clean offline scope: only needed Boost headers + license.
        # Estimate includes the pre-existing headers in maintainer use; guard first.
        guard(root, 2300 * 1024**2)
        extract(root / 'downloads/boost_1_86_0.tar.bz2', boost, 'boost_1_86_0',
                {'boost', 'LICENSE_1_0.txt'})
    verify_boost(root)
    for rel, old, new in ([] if args.resume else specs):
        p = source / rel
        if PurePosixPath(rel).is_absolute() or '..' in PurePosixPath(rel).parts:
            raise RuntimeError('Unsafe patch path')
        text = p.read_text()
        if text.count(old) != 1:
            raise RuntimeError('Unexpected upstream patch target')
        p.write_text(text.replace(old, new))
    # Verify every archive file against the complete patch recipe, including resume.
    with tarfile.open(root / 'downloads/snapcast-v0.35.0.tar.gz') as tar:
        for member in tar:
            if not member.isfile():
                continue
            rel = '/'.join(PurePosixPath(member.name).parts[1:])
            data = tar.extractfile(member).read()
            if any(path == rel for path, _, _ in specs):
                data = apply_file_patches(rel, data.decode(), specs).encode()
            if (source / rel).read_bytes() != data:
                raise RuntimeError('Prepared source differs from exact reconstruction')
    cpp = source / 'client/player/coreaudio_player.cpp'
    if digest(cpp) != BASE_SHA:
        raise RuntimeError('Approved diagnostic source identity mismatch')
    options = darwin_configure_options(root,work,boost)
    env = os.environ.copy()
    for name in ('CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LIBRARY_PATH',
                 'CPATH', 'CPLUS_INCLUDE_PATH', 'C_INCLUDE_PATH', 'CC', 'CXX'):
        env.pop(name, None)
    env.update(COPYFILE_DISABLE='1', TMPDIR=str(work / 'tmp'),
               PKG_CONFIG_PATH='', PKG_CONFIG_LIBDIR=str(work / 'empty-pkgconfig'))
    run([args.cmake, '-S', str(source), '-B', str(build)] + options,
        'configure', root, work, env)
    run([args.cmake, '--build', str(build), '--parallel', '2'], 'build', root, work, env)
    full = make_full_range_coreaudio(cpp.read_text())
    patched = app / 'coreaudio_player.cpp'; patched.write_text(full)
    if digest(patched) != FULL_SHA:
        raise RuntimeError('Approved full-range source identity mismatch')
    settings = {}
    for line in (build / 'client/CMakeFiles/snapclient.dir/flags.make').read_text().splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            settings[key] = shlex.split(value)
    obj, binary = app / 'coreaudio_player.cpp.o', app / 'snapclient-application'
    command = ['/usr/bin/c++'] + settings['CXX_DEFINES'] + settings['CXX_INCLUDES'] + settings['CXX_FLAGS']
    command += ['-I' + str(source / 'client/player'), '-c', str(patched), '-o', str(obj)]
    run(command, 'application-compile', root, work, env)
    link = shlex.split((build / 'client/CMakeFiles/snapclient.dir/link.txt').read_text())
    expected = 'CMakeFiles/snapclient.dir/player/coreaudio_player.cpp.o'
    if link.count(expected) != 1 or link.count('-o') != 1:
        raise RuntimeError('Unexpected linker recipe')
    link[link.index(expected)] = str(obj)
    link[link.index('-o') + 1] = str(binary)
    run(link, 'application-link', root, work, env, cwd=build / 'client')
    manifest = {
        'toolchain': compiler_provenance(build), 'reader_slot_version': 1, 'reader_clock_version': 3, 'upstream_archives': ARCHIVES, 'format': '48000:16:2', 'buffer_ms': 500,
        'darwin_worker_qos_version': DARWIN_WORKER_QOS_VERSION,
        'stop_mode': 'wait-for-process-exit', 'base_source_sha256': BASE_SHA,
        'patched_source_sha256': FULL_SHA, 'deployment_target': '14.2',
        'build': 'Release, arm64, -fno-modules, file/macro/debug prefix maps',
        'disabled_features': list(DISABLED_FEATURES), 'parallel_jobs': 2,
        'validation': 'compile and static inspection only; no audio or executable launch',
        'binaries': {},
    }
    for path in (source / 'bin/snapserver', source / 'bin/snapclient', binary):
        manifest['binaries'][path.name] = verify_binary(path, forbidden(root))
    if snapshot_originals(root) != before:
        raise RuntimeError('Original source/build/client changed during isolated build')
    (work / 'backend-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    archive = package(root, work, app, specs, manifest)
    stage = work / 'staging'
    (stage / 'bin').mkdir(parents=True)
    (stage / 'licenses').mkdir()
    for path in (source / 'bin/snapserver', source / 'bin/snapclient', binary):
        shutil.copyfile(path, stage / 'bin' / path.name)
        (stage / 'bin' / path.name).chmod(0o755)
    for name in NOTICE_FILES:
        shutil.copyfile(work / 'notices' / name, stage / 'licenses' / name)
    release_files = ['bin/' + name for name in ('snapserver', 'snapclient', 'snapclient-application')]
    release_files += ['licenses/' + name for name in NOTICE_FILES]
    files = {name: digest(stage / name) for name in release_files}
    (stage / 'manifest.json').write_text(json.dumps({'files': files,
        'toolchain': manifest['toolchain'], 'reader_slot_version': 1, 'reader_clock_version': 3, 'source_archive': {'name': 'macbee-backend-corresponding-source.tar.gz', **archive},
        'darwin_worker_qos_version': manifest['darwin_worker_qos_version'],
        'binaries': manifest['binaries']}, indent=2) + '\n')
    print(json.dumps({'binaries': manifest['binaries'], 'source_archive': archive,
                      'originals_unchanged': True, 'capacity': guard(root)}, indent=2), flush=True)


if __name__ == '__main__':
    main()
