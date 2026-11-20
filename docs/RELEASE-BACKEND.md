# Offline backend release build and corresponding source

Scope: Snapcast 0.35.0 and Boost 1.86.0, Apple Silicon macOS 14.2 or later.
The build script does not download dependencies, install, launch a client/server,
open a listener, capture audio, or change the original build/source/client files.
It creates only `release-build/` and `release-application-renderer/` in a dedicated
scope, plus Boost headers when preparing a clean recipient scope. All logs are
private build artifacts and are **not** part of the release.

## Build prerequisites

Use an existing Python 3.9 or later, Apple command-line C/C++ tools/macOS SDK,
CMake, and `/usr/bin/make`. The maintainer build used Apple Clang, SDK 27.2,
CMake 4.0.3, arm64, macOS deployment target 14.2, and two compiler jobs.
Only Apple system libraries/frameworks are dynamically linked. No Homebrew
runtime library or Python package is required by these three executables.
A different compiler/SDK can produce different binary hashes. The release configure command explicitly sets `-DCMAKE_OSX_SYSROOT=macosx`; CMake 4 may otherwise leave the target sysroot unspecified.

The corresponding-source archive includes the exact upstream source archives,
complete literal patch specifications, all modified source files, and the
full-range variant. The original GPL/Boost/MIT notices are included. The build
script reconstructs the modified sources without running the old local scripts
or depending on a developer checkout, credentials, SSH configuration, or paths.

## Clean offline reconstruction

Unpack `macbee-backend-corresponding-source.tar.gz`. In its extracted directory,
choose an **existing, otherwise empty, real absolute directory** on a volume
with sufficient free space, and call it `$BUILD_ROOT`. No symlinks are allowed.

```sh
mkdir "$BUILD_ROOT/downloads"
cp upstream/snapcast-v0.35.0.tar.gz upstream/boost_1_86_0.tar.bz2 "$BUILD_ROOT/downloads/"
python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)" --plan
python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)"
```

`--plan` verifies archives and the minimum additional allocation, without writing.
The build rejects pre-existing release output directories rather than overwriting.
An explicit `--resume` is permitted only after configuration failed before any
binary output; it re-verifies every prepared upstream source file before proceeding.
It extracts only the needed Boost headers/license when they are absent. It stops
if the dedicated scope exceeds 3.5 GiB allocated or volume free space falls below
8.5 GiB, preserving at least the agreed 8 GiB reserve and staying below 4 GiB.
A conservative 400 MiB additional allowance is checked before the maintainer
build. Clean header extraction separately checks a 2300 MiB allowance. Filesystem
allocation units vary: a guard failure is not permission to expand this scope.
Each compiler/build stage is limited to 15 minutes and each child-created file to
128 MiB. A failed stage retains files and logs for review; it never auto-retries
or deletes another build. Do not rerun after any binary has been produced. The narrow configuration-only
resume does not authorize replacing a previous executable.

Build options: Release; arm64; deployment 14.2; `-fno-modules`; file/macro/debug
prefix maps to `/macbee-build`; static internal libraries; client/server enabled;
tests, optional SSL/ALSA/FLAC/VORBIS/TREMOR/OPUS/AVAHI/EXPAT/PULSE/JACK/PIPEWIRE/
SDL2/SOXR disabled. The full-range variant changes only the approved CoreAudio
source in a separate object and links the same newly built objects. The original
low-amplitude diagnostic client remains separate. No Snapcast protocol or 500 ms
application buffer change is introduced by packaging.

Outputs:
- `release-build/source/bin/snapserver`
- `release-build/source/bin/snapclient` (low-amplitude diagnostic/guard identity)
- `release-application-renderer/snapclient-application` (full-range S16 playback)
- `release-build/backend-manifest.json` (hashes/system dependency inspection)
- `release-build/notices/`
- `release-build/staging/{bin,licenses,manifest.json}` (seven-file allowlist; no AppleDouble metadata)
- `release-build/macbee-backend-corresponding-source.tar.gz`

Do not use the legacy diagnostic client as the ordinary-music renderer. Copy only
the allowlisted executables, notices and manifest into the app packaging stage.
Use the explicit manifest/filename allowlist, never recursive directory copying.
External filesystems may generate `._` metadata beside valid files; none belongs
in the source archive, app, or release manifest.
Never bundle the build cache/logs, external root, private configuration, SSH
identity, private receiver alias, or developer checkout. Keep the independent
probe/native-app source in the Macbee release source archive. Runtime packaging
must set hashes to the newly built executables; old frozen clients are untouched.

## Distribution checklist

This is an engineering compliance checklist, not legal advice. Modified Snapcast
is GPL-3.0-or-later and retains its authors' notices. Provide its complete
corresponding-source asset beside the binary asset on the **same release**, free
of additional charge, with equal access for every binary recipient (GPLv3 §6(d)).
A private release does not remove source/notice obligations or recipients' GPL
rights to redistribute. Do not add terms that restrict those rights.

Include the supplied GPLv3, Boost and MIT notices with the app. The source archive
contains the complete pinned upstream sources and exact modification/build
recipe, not just links to third-party projects or the first-party source zip.
Keep it available with the matching binary release. This direct-source route is
not a three-year written source offer. Apple's system frameworks and ordinary
compiler/toolchain components are not included in the source archive. Licensing
of independently communicating first-party app components is a separate owner
choice; this checklist does not assert that launching a GPL subprocess alone
makes every first-party component GPL.

References: https://www.gnu.org/licenses/gpl-3.0.en.html (especially §§1, 5, 6(d));
https://www.gnu.org/licenses/gpl-faq.html;
https://www.boost.org/LICENSE_1_0.txt.


### Matching local backend for configured outputs

The pinned upstream archives remain unchanged. The opt-in stdin reader recipe
in `tools/snapserver_clock_patch.py` is included by `release_backend.py` in
the complete sequential replacement recipe and resulting modified source
in the corresponding-source archive. Non-opted-in Snapcast streams retain
upstream timing. A matching stage advertises `reader_clock_version: 3` and `reader_slot_version: 1`
(the slot header is private to the local pipe, not a network protocol change);
the build-8 candidate also requires `darwin_worker_qos_version: 1` for the rebuilt server and clients.
The worker recipe is `tools/snapcast_qos_patch.py`. Packaging refuses older stages. Do not reuse the old
source archive for a binary built with this recipe. Before distribution,
reconstruct/package in a new clean scope using the documented build command,
verify the new source archive/hash and binaries together, and retain the old
binary and matching source archive for rollback.

For the historical reader-only canary (not the rebuilt build-8 backend), the
`package_reader_clock_source_addendum.py` helper can build a small stage from
the exact previously retained 1.2.1 source base, its authenticated binary
stage, the new reader binary and its compiled header. It checks the pinned
archive hash, reconstructs the modified source, requires a clean commit, and
verifies both archive/stage readback. Supply the exact CMake object root used
for the incremental build with `--cmake-build`. The source and stage manifests
record the configured AppleClang version, target SDK version (from the actual
compiler flags), and hashes of the compiler metadata and flags. The incremental
receipt identifies the rebuilt server/header; it does not invent provenance
for retained client binaries. Its manifest labels the base and addendum
as **both required**, not interchangeable. Unpack their matching top-level
directories in that order into a new empty parent to rebuild. Do not distribute
the addendum on its own. The ordinary public release method remains the
standalone corresponding-source archive.
