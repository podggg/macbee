[English](README.md) | [简体中文](README.zh-CN.md)

# Macbee

**One sound. Your chosen Macs.** Synchronize **Entire Mac** audio across multiple Apple Silicon Macs over your local network via SSH — with no virtual audio drivers, no Snapcast installation, and no Homebrew required for users.

Macbee captures system-wide output on your sender Mac (including notifications and calls, with no microphone access and no saved audio files) and plays it through built-in speakers on your selected Macs on a shared timeline.

> **Do I need to install Snapcast?**
>
> - **Using prebuilt `Macbee.app`: NO.** Neither Mac needs Snapcast or Homebrew installed. `Macbee.app` bundles custom-patched `snapserver`, `snapclient`, `snapclient-application`, `macbee-tone-probe`, and `macbee-askpass`. Receiver Macs do not even need `Macbee.app` installed — when you add a Mac inside Macbee, it automatically stages the three small receiver components into `~/Library/Application Support/Macbee Receiver/` over SSH.
> - **Building from source (developers): YES (pinned source tarballs only).** Source builds use [`tools/release_backend.py`](tools/release_backend.py) with the pinned Snapcast `v0.35.0` and Boost `1.86.0` tarballs, Xcode Command Line Tools, and CMake. **Do not substitute `brew install snapcast`**: Macbee applies custom V3 pipe-clock/pacing recovery, Darwin playback-worker QoS, and CoreAudio instrumentation patches that upstream builds do not include.

## Scope and Requirements

Macbee currently focuses solely on **Entire Mac** audio sharing across Apple Silicon Macs. (Single-app `One App` isolation was removed in `1.2.6-dev`. Macbee does not support Bluetooth speaker output, iOS receivers, or universal video lip-sync.)

| Role | Requirements |
| --- | --- |
| **Sender Mac** | • Apple Silicon (`arm64`) running **macOS 14.2 or later**<br>• Logged-in desktop session<br>• **Built-in Speakers** set as default output at **48 kHz** stereo (with zero input streams on that output device)<br>• System `/usr/bin/python3` **3.9 or later** *(provided by macOS / Xcode Command Line Tools if prompted)* |
| **Receiver Mac(s)**<br>*(up to 15 remote Macs + `This Mac` = 16 outputs max)* | • Apple Silicon (`arm64`) running **macOS 14.2 or later**<br>• Logged-in desktop GUI session (matching the SSH user)<br>• **Built-in Speakers** set as default output at **48 kHz** stereo<br>• **Python 3.9 or later** (`python3` in the remote SSH shell `PATH`, e.g., `/usr/bin/python3` via Xcode Command Line Tools)<br>• **Remote Login** (SSH) enabled in macOS System Settings — no `Macbee.app` install needed |

---

## Quick Start (Prebuilt `Macbee.app`)

### 1. Install `Macbee.app` on the sender Mac

1. Download `Macbee-*.zip` (for example `Macbee-1.2.6-dev-arm64.zip`) from [GitHub Releases](https://github.com/podggg/macbee/releases) and unpack it.
2. Drag `Macbee.app` into `/Applications`.
3. **Ad-hoc signing note:** Current `1.2.6-dev` builds are ad-hoc signed, not Developer ID signed or notarized. If macOS blocks launching a downloaded copy, open **System Settings → Privacy & Security**, scroll to the security prompt, and click **Open Anyway**. Never disable Gatekeeper or System Integrity Protection (SIP).

### 2. Turn on Remote Login on each receiver Mac

1. On the receiving Mac, open **System Settings → General → Sharing** and enable **Remote Login** (confirm your user account is allowed access).
2. Make sure the receiver Mac is logged into its desktop GUI session and its default output in **System Settings → Sound** is **Built-in Speakers** (48 kHz).
3. If you are unsure of your short account username on that Mac, run `whoami` in Terminal on the receiver Mac (for example, `alex`).

### 3. Open Macbee and click **Add Mac…**

1. Open `Macbee.app` on the sender Mac. Macbee starts idle — opening the app never captures audio or connects to receivers.
2. Click **Add Mac…**. macOS may ask for **Local Network** permission so Macbee can browse nearby `_ssh._tcp` services (this is optional; `~/.ssh/config` aliases and manual entry still work if you decline).
3. In the **Add a Mac** sheet, select your destination in any of three ways:
   - **Nearby Mac** (Bonjour `_ssh._tcp`): choose a discovered Mac (`<host> · Nearby Mac`). Your own computer is marked **This Mac** and cannot be added; Macs already paired are marked **Added**. Edit **MAC USERNAME** if your account short name on the receiver differs from the sender.
   - **SSH config alias**: choose an existing alias from `~/.ssh/config` (`<user>@<host> · SSH config` or `SSH config alias`).
   - **Enter address manually…**: select this row and type the destination under **SSH ADDRESS** as `user@host`, `user@host:port`, or an alias — for example `alex@192.168.1.20`, `alex@mac-mini.local`, or bracketed IPv6 `alex@[fe80::1]:22`. Do **not** include the `ssh ` prefix.
4. Optionally customize **DEVICE NAME** (up to 60 characters; this is the label shown in **OUTPUTS**).

### 4. Sign in with **Connect & Add**

1. Click **Connect & Add**.
2. **First-time host key confirmation:** Macbee runs system `/usr/bin/ssh` with `StrictHostKeyChecking=ask` and shows the **Trust this SSH host key?** sheet with the `SHA256:...` fingerprint. Compare the fingerprint against your receiver Mac, then click **Trust and Continue** (if a host key ever changes unexpectedly, Macbee refuses the connection and never edits `known_hosts`).
3. **Password or key passphrase:** Enter your receiver account password or SSH key passphrase in **Sign in to the selected SSH destination** and click **Continue**. Secrets go strictly through the bundled `macbee-askpass` helper to `/usr/bin/ssh` and are never written to disk, config, or logs.
4. *(Optional)* Check **Remember passphrase in Keychain** (off by default) if you want Macbee to save an SSH key passphrase into macOS Keychain via `ssh-add --apple-use-keychain`. Account passwords are never stored in Keychain or config.
5. Macbee verifies the receiver and automatically stages its three small components into `~/Library/Application Support/Macbee Receiver/<digest>/` on the receiver Mac. Newly added Macs appear **unchecked** in **OUTPUTS** and no audio starts yet.

### 5. Choose outputs and click **Start**

1. Under **OUTPUTS**, select the checkboxes for the Macs you want to include (`This Mac` and/or your added receivers). You can click **Check** on any remote row beforehand to confirm it shows **Available**.
2. Click **Start**. On your first **Start**, grant **System Audio Recording** permission when macOS prompts.
3. Once status switches to **Waiting for audio** (or **Playing**), play audio in any app on the sender Mac. Macbee handles silence and app switches cleanly without restarting, and adjusting system volume or mute does not stop the session.
4. Click **Stop** (or close the window / quit Macbee) when you are done. Stop and Quit cleanly stop all renderers and close SSH control masters before releasing the system audio tap.

---

## Troubleshooting & Everyday Notes

- **AirPods or Bluetooth headphones connect (`default_not_builtin`):** Macbee enforces a built-in speaker safety policy (`48 kHz` stereo Built-in Speakers with zero input streams) on both sender and receivers. If AirPods, Bluetooth headphones, or an external USB device become the macOS default output, Start refuses or an active session stops safely (`default_not_builtin` / `unsupported_format` / `output_device_has_input_stream`). Switch macOS Sound output back to Built-in Speakers before clicking **Start**.
- **macOS asks for permissions again after an upgrade:** Because prebuilt `Macbee.app` is ad-hoc signed, replacing `/Applications/Macbee.app` with a newer build updates its code-signing identity. Re-grant **Open Anyway**, **Local Network**, or **System Audio Recording** if prompted.
- **Receiver offline, asleep, or expired SSH session:**
  - If a receiver's SSH `ControlMaster` expired (for example after restarting Macbee or waking from sleep), clicking **Check** or **Start** automatically re-opens the in-app **Sign in to the selected SSH destination** sheet (`auth_required`) so you can sign in again without re-adding the Mac.
  - If a receiver is sitting at the macOS login/lock screen with no active console session (`receiver_needs_logged_in_session`), log in on that Mac's screen first.
  - If one remote receiver drops mid-session, its badge switches to **Offline** while the remaining selected outputs keep playing (unless `This Mac` or the shared server stops).
- **Uninstall & clean up:**
  - **Sender Mac:** quit Macbee, move `/Applications/Macbee.app` to Trash, and delete `~/Library/Application Support/Macbee/` (contains `local-config.json`, `diagnostics/`, and `relay-runtime/`).
  - **Receiver Mac:** delete `~/Library/Application Support/Macbee Receiver/` (and optionally disable **Remote Login** in **System Settings → General → Sharing**).
  - Ephemeral SSH control sockets live only in `/tmp/macbee-ctl.*` and `/tmp/macbee-auth.*` while active and are cleaned up on Stop and Quit.

---

## Build from Source (Developers)

### Prerequisites

- Apple Silicon Mac, **macOS 14.2 or later**, `/usr/bin/python3` **3.9+**, `/usr/bin/make`
- Xcode Command Line Tools (`xcrun clang` / `clang++`)
- `cmake` (e.g., `brew install cmake` — build tool only; **do not** substitute `brew install snapcast`)

### 1. Build the patched Snapcast backend (`tools/release_backend.py`)

Create a clean, empty, real absolute build directory `$BUILD_ROOT` (no symlinks; volume must keep at least **8.5 GiB** free, and `$BUILD_ROOT` stays under **3.5 GiB** allocated, including a ~2.25 GiB check during initial Boost header extraction). Place the two exact pinned upstream tarballs inside `$BUILD_ROOT/downloads/`:

| Archive | Upstream URL | Required SHA-256 |
| --- | --- | --- |
| `snapcast-v0.35.0.tar.gz` | `https://github.com/snapcast/snapcast/archive/refs/tags/v0.35.0.tar.gz` | `cb75a71479bf52910bf5f47ae8120ec41c89459b0d77d7cd560e674e437ef050` |
| `boost_1_86_0.tar.bz2` | `https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.bz2` | `1bed88e40401b2cb7a1f76d4bab499e352fa4d0c5f31c0dbae64e24d34d7513b` |

Verify checksums, run the read-only `--plan` check, and build offline:

```sh
mkdir -p "$BUILD_ROOT/downloads"
curl -fL -o "$BUILD_ROOT/downloads/snapcast-v0.35.0.tar.gz" \
  https://github.com/snapcast/snapcast/archive/refs/tags/v0.35.0.tar.gz
curl -fL -o "$BUILD_ROOT/downloads/boost_1_86_0.tar.bz2" \
  https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.bz2
shasum -a 256 "$BUILD_ROOT/downloads/snapcast-v0.35.0.tar.gz" "$BUILD_ROOT/downloads/boost_1_86_0.tar.bz2"

python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)" --plan
python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)"
```

This produces `$BUILD_ROOT/release-build/staging/` (`bin/snapserver`, `bin/snapclient`, `bin/snapclient-application`, `licenses/`, `manifest.json`) and `$BUILD_ROOT/release-build/macbee-backend-corresponding-source.tar.gz`.

### 2. Build the read-only device probe (`tools/build_probe.py`)

```sh
python3 tools/build_probe.py .local-app/probe-build
shasum -a 256 .local-app/probe-build/macbee-tone-probe
```

The built probe's SHA-256 must match `PROBE_HASH` in [`tools/speaker_transport.py`](tools/speaker_transport.py) (`8c5d81e619757e74e5d3406647e1603966563542171ad6cb74ecae874ada35e8`).

### 3. Package `Macbee.app` (`tools/package_release.py --system-candidate`)

`--output` must point to a new (non-existing) directory under `.local-app/`:

```sh
python3 tools/package_release.py \
  --system-candidate \
  --backend "$BUILD_ROOT/release-build/staging" \
  --probe .local-app/probe-build/macbee-tone-probe \
  --output .local-app/release-1.2.6-build10
```

This creates `.local-app/release-1.2.6-build10/Macbee.app`, `Macbee-1.2.6-dev-arm64.zip`, `SHA256SUMS`, `bundle-sha256.json`, and `release-manifest.json` (and verifies zero host-path leaks and strict ad-hoc signature).

### 4. Run offline tests

```sh
python3 -m unittest discover -s Tests -p 'test_*.py'
python3 -Bu tools/test_pacing_headless.py python
python3 -Bu tools/test_pacing_headless.py native
```

To also run the backend-stage verification lane against your V3 stage:

```sh
MACBEE_PACING_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_OFFLINE_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_QOS_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_QOS_ARCHIVE="$BUILD_ROOT/downloads/snapcast-v0.35.0.tar.gz" \
python3 -Bu tools/test_pacing_headless.py stage
```

---

## Architecture & Further Docs

- [Architecture Overview](docs/ARCHITECTURE.md) — CoreAudio tap, V3 pacing recovery, SSH ControlMaster, and safety guardians
- [Offline Backend Release & Corresponding Source](docs/RELEASE-BACKEND.md) — Snapcast/Boost build recipe and GPLv3 distribution checklist
- [Candidate Behavior & Known Limits (`1.2.6-dev`)](docs/NEXT-USER-GUIDE.md) — bundled in-app Read Me
- [Macbee Setup & Diagnostics Skill](skills/macbee-setup/SKILL.md) — agent workflow for setup, builds, and triage

---

## Licenses & Third-Party Notices

- **Macbee first-party code:** Licensed under **`GPL-3.0-or-later`** — see [`LICENSE`](LICENSE) and [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).
- **Snapcast `0.35.0` (bundled & modified backend binaries — `snapserver`, `snapclient`, `snapclient-application`):** Licensed under **`GPL-3.0-or-later`** ([upstream source](https://github.com/snapcast/snapcast/tree/v0.35.0)). Existing upstream copyright notices and file-level GPLv3 §5(a) modification notices are retained, and Macbee's modifications are provided under `GPL-3.0-or-later` without warranty. Per GPLv3 §6(d), every binary release must be accompanied by its matching **`macbee-backend-corresponding-source.tar.gz`** archive on the same [GitHub Releases](https://github.com/podggg/macbee/releases) page with equal access and no added restrictions.
- **Boost `1.86.0` (headers used when building Snapcast):** Licensed under **Boost Software License 1.0 (`BSL-1.0`)**.
- **Bundled header-only components inside Snapcast:** `aixlog` `1.5.1`, `popl` `1.3.1`, and `nlohmann/json` `3.11.3` are licensed under the **MIT License**.
- **Direct distribution only (not on the Mac App Store):** Because Macbee and its bundled Snapcast backend are licensed under `GPL-3.0-or-later`, Macbee is distributed directly via [GitHub Releases](https://github.com/podggg/macbee/releases) with matching corresponding source and is incompatible with Mac App Store distribution terms.

Packaged `Macbee.app` bundles include full license texts and notices in `Macbee.app/Contents/Resources/Licenses/` (`GPL-3.0.txt`, `BSL-1.0.txt`, `MIT-components.txt`, `THIRD-PARTY-NOTICES.txt`).
