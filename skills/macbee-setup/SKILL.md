---
name: macbee-setup
description: Guide first-time installation, receiver Mac pairing over SSH, source builds with patched Snapcast v0.35.0 and Boost 1.86.0, and disconnect/audio diagnostics for Macbee on Apple Silicon Macs. Use when setting up Macbee.app, adding a receiver Mac, building from source, or triaging Macbee setup and session-stop errors.
---

# Macbee Setup & Diagnostics Skill

Use this skill to help users install, configure, build, or troubleshoot **Macbee** (Entire Mac audio sharing across Apple Silicon Macs on macOS 14.2+).

## Strict Safety Guardrails

Follow these non-negotiable boundaries before running any command or advising the user:

1. **Never bypass macOS security controls.** Never disable Gatekeeper (`spctl --master-disable`), disable System Integrity Protection (`csrutil`), or strip quarantine flags to silence an ad-hoc build warning. Direct the user to **System Settings → Privacy & Security → Open Anyway** for prebuilt ad-hoc `Macbee.app` releases.
2. **Never auto-accept or rewrite SSH host keys.** Never pass `StrictHostKeyChecking=no` or `UserKnownHostsFile=/dev/null`, and never delete/overwrite `known_hosts` entries automatically. First-time trust must happen through Macbee's **Trust this SSH host key?** sheet (after comparing the `SHA256:...` fingerprint) or the user's own verification; changed host keys must be refused and investigated.
3. **Never touch, log, echo, or persist passwords or passphrases.** Never ask the user to paste an SSH password or key passphrase into chat, scripts, config files, or environment variables. Interactive credentials belong strictly in Macbee's secure `NSSecureTextField` prompt (`macbee-askpass`).
4. **Never start live audio or alter system audio/SSH settings unasked.** Do not start sharing, play audio, toggle **Remote Login**, change macOS output routing/volume, or overwrite `/Applications/Macbee.app` without explicit user confirmation.
5. **Never substitute `brew install snapcast`.** Prebuilt `Macbee.app` bundles custom-patched Snapcast binaries and requires zero Snapcast/Homebrew install; source builds must use [`tools/release_backend.py`](../../tools/release_backend.py) with the pinned tarballs.
6. **Stay strictly truthful about scope.** `Macbee.app` (`1.2.6-dev`) shares **Entire Mac** audio only (`One App` was removed in `1.2.6-dev`). Do not claim Bluetooth speaker output, iOS receivers, or universal video lip-sync. Do not expose private paths, usernames, IPs, or aliases in repo files or summaries.

---

## Decision Tree

Ask or inspect which workflow the user needs:

1. **First-time setup with prebuilt `Macbee.app`** → Go to [Path A: Prebuilt Setup and Pairing](#path-a-prebuilt-setup-and-pairing) after running the [Read-Only Preflight Checklist](#read-only-preflight-checklist).
2. **Building from source / packaging a release** → Go to [Path B: Build from Source](#path-b-build-from-source).
3. **Troubleshooting a failed Check, Add Mac…, Start, or mid-session disconnect** → Go to [Path C: Diagnostic Triage](#path-c-diagnostic-triage).

---

## Read-Only Preflight Checklist

Run these read-only checks first. Only run SSH commands against a remote Mac when the user explicitly asks to inspect an already-trusted host (always pass `-o BatchMode=yes -o StrictHostKeyChecking=yes`).

### 1. Sender Mac readiness (local)

```sh
# Hardware, macOS version (>= 14.2), and system Python (>= 3.9)
uname -m
sw_vers -productVersion
/usr/bin/python3 --version

# Verify logged-in console UID matches current user
stat -f '%u' /dev/console && id -u

# Verify Built-in Speakers at 48 kHz with 0 input streams (read-only)
/Applications/Macbee.app/Contents/Resources/bin/macbee-tone-probe --inspect
```

Expected `--inspect` output contains `"event":"ready"`, `"sample_rate":48000`, `"input_streams":0`, and a non-empty `"device_uid"`. If it returns `{"event":"error","reason":"default_not_builtin",...}` or `"unsupported_format"`, have the user switch **System Settings → Sound → Output** to **Built-in Speakers** (48 kHz).

### 2. Receiver Mac readiness

Check on the receiver Mac locally, or via read-only `BatchMode=yes` SSH if the user already has a trusted connection:

```sh
# Architecture (arm64), macOS (>= 14.2), Python (>= 3.9 in SSH PATH, e.g. /usr/bin/python3), logged-in GUI session, short username
uname -m && sw_vers -productVersion && command -v python3 && python3 --version && whoami
stat -f '%u' /dev/console && id -u

# Check Remote Login (SSH) state (read-only)
launchctl print system/com.openssh.sshd >/dev/null 2>&1 && echo "sshd loaded" || echo "sshd not loaded"
```

Note: `stat -f '%u' /dev/console` must match `id -u` (non-root GUI user logged in); otherwise Macbee reports `receiver_needs_logged_in_session`. Receiver Macs do **not** need `Macbee.app`, Snapcast, or Homebrew installed.

### 3. Inspect Macbee's saved outputs and SSH discovery (read-only)

Run against the installed `/Applications/Macbee.app` (or a repo checkout):

```sh
# List saved outputs and selection state (sanitized IDs/names only)
MACBEE_RUNTIME_HOME="$HOME/Library/Application Support/Macbee" \
MACBEE_ASSET_ROOT="/Applications/Macbee.app/Contents/Resources" \
/usr/bin/python3 -B -E -s /Applications/Macbee.app/Contents/Resources/tools/receiver_setup.py --list

# Preview ~/.ssh/config aliases and normalized configured keys without invoking ssh -G
MACBEE_RUNTIME_HOME="$HOME/Library/Application Support/Macbee" \
MACBEE_ASSET_ROOT="/Applications/Macbee.app/Contents/Resources" \
/usr/bin/python3 -B -E -s /Applications/Macbee.app/Contents/Resources/tools/receiver_setup.py --discover

# Verify an existing configured receiver over batch SSH (read-only)
MACBEE_RUNTIME_HOME="$HOME/Library/Application Support/Macbee" \
MACBEE_ASSET_ROOT="/Applications/Macbee.app/Contents/Resources" \
/usr/bin/python3 -B -E -s /Applications/Macbee.app/Contents/Resources/tools/receiver_setup.py --check <receiver-id>
```

If `--check <receiver-id>` returns `{"ok": false, ..., "code": "auth_required"}`, that does **not** mean the receiver is misconfigured — it means no passwordless key/agent/ControlMaster is active, so the user simply needs to click **Check** or **Start** inside `Macbee.app` to sign in through the in-app SSH sheet.

---

## Path A: Prebuilt Setup and Pairing

Walk the user through these exact UI steps:

1. **Install sender app:** Download `Macbee-*.zip` from [GitHub Releases](https://github.com/podggg/macbee/releases), unpack it, and move `Macbee.app` to `/Applications`. If macOS blocks the ad-hoc build on launch, use **System Settings → Privacy & Security → Open Anyway**.
2. **Prepare receiver Mac:** On the receiving Mac, enable **System Settings → General → Sharing → Remote Login**, keep its desktop session logged in, and set default output to **Built-in Speakers** (48 kHz). Note the short username (`whoami`).
3. **Add the receiver in Macbee:**
   - Open `Macbee.app` and click **Add Mac…** (optionally grant **Local Network** permission for Bonjour `_ssh._tcp` discovery; aliases and manual entry still work if declined).
   - Pick a **Nearby Mac** (edit **MAC USERNAME** if needed; the local machine appears disabled as **This Mac**), pick an `~/.ssh/config` alias, or choose **Enter address manually…** and enter `user@host` or `user@host:port` (e.g., `alex@192.168.1.20` or `alex@mac-mini.local`, without an `ssh ` prefix).
   - Optionally customize **DEVICE NAME**, then click **Connect & Add**.
4. **Trust & sign in once:**
   - On first connection, verify the `SHA256:...` fingerprint in **Trust this SSH host key?** and click **Trust and Continue**.
   - Enter the password or key passphrase in **Sign in to the selected SSH destination** and click **Continue**. Leave **Remember passphrase in Keychain** unchecked unless saving a key passphrase via `ssh-add --apple-use-keychain`.
   - Macbee automatically stages `snapclient-macbee-application`, `macbee-tone-probe`, and `audio_guardian-configured-v1.py` into `~/Library/Application Support/Macbee Receiver/<suffix>/` on the receiver.
5. **Select & share:** Check the desired rows under **OUTPUTS** (newly added Macs start unchecked, max 16 total outputs), click **Start** (grant **System Audio Recording** on first Start), wait for **Waiting for audio** / **Playing**, and click **Stop** when done.

---

## Path B: Build from Source

Use this path when building `Macbee.app` (`1.2.6-dev`, Build `10`) from the repository.

### 1. Build the V3 backend (`tools/release_backend.py`)

Choose an empty, real absolute directory `$BUILD_ROOT` (no symlinks; keep **>= 8.5 GiB** volume free space and **<= 3.5 GiB** inside `$BUILD_ROOT`). Place the exact pinned archives in `$BUILD_ROOT/downloads/`:

- `snapcast-v0.35.0.tar.gz` — `https://github.com/snapcast/snapcast/archive/refs/tags/v0.35.0.tar.gz`
  SHA-256: `cb75a71479bf52910bf5f47ae8120ec41c89459b0d77d7cd560e674e437ef050`
- `boost_1_86_0.tar.bz2` — `https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.bz2`
  SHA-256: `1bed88e40401b2cb7a1f76d4bab499e352fa4d0c5f31c0dbae64e24d34d7513b`

```sh
python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)" --plan
python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)"
```

Verify `$BUILD_ROOT/release-build/staging/manifest.json` reports `reader_clock_version: 3`, `reader_slot_version: 1`, and `darwin_worker_qos_version: 1`. Distribute `macbee-backend-corresponding-source.tar.gz` alongside any binary release per GPL-3.0-or-later (see [RELEASE-BACKEND.md](../../docs/RELEASE-BACKEND.md)).

### 2. Build `macbee-tone-probe` and package `Macbee.app`

```sh
python3 tools/build_probe.py .local-app/probe-build
shasum -a 256 .local-app/probe-build/macbee-tone-probe
# Expected SHA-256 on main: 8c5d81e619757e74e5d3406647e1603966563542171ad6cb74ecae874ada35e8

python3 tools/package_release.py \
  --system-candidate \
  --backend "$BUILD_ROOT/release-build/staging" \
  --probe .local-app/probe-build/macbee-tone-probe \
  --output .local-app/release-1.2.6-build10
```

### 3. Verify offline test suites

```sh
python3 -m unittest discover -s Tests -p 'test_*.py'
python3 -Bu tools/test_pacing_headless.py python
python3 -Bu tools/test_pacing_headless.py native
```

Optional stage lane:

```sh
MACBEE_PACING_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_OFFLINE_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_QOS_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_QOS_ARCHIVE="$BUILD_ROOT/downloads/snapcast-v0.35.0.tar.gz" \
python3 -Bu tools/test_pacing_headless.py stage
```

---

## Path C: Diagnostic Triage

### 1. Where diagnostic files live

Read these bounded, content-free local artifacts on the sender Mac (do not share raw private hosts/paths from `local-config.json`):

- **Native stop snapshots:**
  - `~/Library/Application Support/Macbee/diagnostics/latest-native.json`
  - `~/Library/Application Support/Macbee/diagnostics/last-native-failure.json`
  - Key fields: `first_stop.first_cause`, `first_stop.first_backend_cause`, `first_stop.fault_reason`, `first_stop.pipe_errno`, `first_stop.max_tick_gap`, `first_stop.max_pump_gap`, `cleanup_verified`.
- **Per-session backend logs & snapshots (newest directory):**
  - `~/Library/Application Support/Macbee/relay-runtime/relay-configured-*/backend-first-stop.json`
  - `~/Library/Application Support/Macbee/relay-runtime/relay-configured-*/backend-final.json`
  - `~/Library/Application Support/Macbee/relay-runtime/relay-configured-*/local-client.log`
  - `~/Library/Application Support/Macbee/relay-runtime/relay-configured-*/<receiver-id>-client.log`

Quick read-only inspection snippet:

```sh
cat "$HOME/Library/Application Support/Macbee/diagnostics/latest-native.json"
LATEST_RUN=$(ls -td "$HOME/Library/Application Support/Macbee/relay-runtime/relay-configured-"* 2>/dev/null | head -n 1)
echo "Latest run: $LATEST_RUN"
[ -n "$LATEST_RUN" ] && cat "$LATEST_RUN/backend-first-stop.json" 2>/dev/null
[ -n "$LATEST_RUN" ] && grep -E 'speaker inventory|speaker safety|speaker setting|MACBEE_GUARD_FINAL' "$LATEST_RUN/"*-client.log 2>/dev/null
```

### 2. Error & reason-code triage table

| Signal / Code | Source | Meaning & Action |
| --- | --- | --- |
| `default_not_builtin` | `macbee-tone-probe` / `audio_guardian.py` | Default output on sender or receiver is not Built-in Speakers (often AirPods, Bluetooth headphones, or USB DAC connecting). Switch macOS Sound output back to **Built-in Speakers** (48 kHz) before clicking **Start**. |
| `unsupported_format` / `unsupported_default_output` / `output_device_has_input_stream` / `expected_one_output_stream` | `tone_probe.c` / `receiver_setup.py` | Default output must be 48 kHz stereo Float32 with 0 input streams and 1 output stream. Check **Audio MIDI Setup** (`48,000 Hz`, `2ch 32-bit Float`) and ensure no aggregate/virtual/mic-bearing output is default. |
| `auth_required` | `ssh_control.py` / `receiver_setup.py` / `run_system_relay.py` | Batch SSH failed authentication or ControlMaster expired. Click **Check** or **Start** in Macbee to re-open the **Sign in to the selected SSH destination** sheet; check username and Remote Login access if it keeps failing. |
| `host_key_unverified` | `ssh_control.py` / `SSHSignIn.m` | Remote host key is unknown or changed in `~/.ssh/known_hosts`. Never auto-overwrite. Have the user verify the server's real key fingerprint out-of-band before updating `known_hosts` manually if legitimate. |
| `receiver_needs_logged_in_session` | `receiver_setup.py` | Receiver Mac is at the login/lock screen or `/dev/console` belongs to a different user than the SSH login. Log into the receiver Mac's desktop GUI session. |
| `receiver_needs_setup` | `receiver_setup.py` / `configured_transport.py` | Receiver components in `~/Library/Application Support/Macbee Receiver/<suffix>/` are missing or mismatched (for instance, `audio_guardian-configured-v1.py` after an app upgrade). `Check` is read-only and **Add Mac…** rejects already-configured receivers, so quit Macbee, remove the stale entry from `~/Library/Application Support/Macbee/local-config.json` (preserving `0600` mode), reopen Macbee, and re-add via **Add Mac…** so it stages fresh matching components into `~/Library/Application Support/Macbee Receiver/<suffix>/` (or update the staged files to match bundled digests). |
| `duplicate_receiver_identity` | `receiver_setup.py` | Target Mac matches the sender's `IOPlatformUUID` digest or an already-configured receiver under another IP/alias. Do not add the sender as a receiver (`This Mac` is already listed). |
| `output_pacing_stall` / `sender_pacing_recovery_limit` / `source_backlog` | `run_system_relay.py` / `session_diagnostics.py` | Sender relay loop delayed > 580 ms or exceeded 4 recovery episodes in 10 s (e.g., sender sleep/wake or extreme preemption). Check `max_loop_gap`, `max_pump_gap`, and `pacing_recoveries`. |
| `server_media_stale` / `server_read_timeout` / `server_pacing_recovery_limit` / `pcm_backpressure` | `snapserver_clock_patch.py` / `reader_clock.py` | Local `snapserver` read clock stalled > 250 ms (`server_read_timeout`), exceeded 35 slots / 720 ms bridge cap (`server_media_stale`), or exceeded 4 recoveries in 10 s. Check `reader_max_unacked_frames` and `reader_last_report_age_ms`. |
| `source_health_timeout` / `capture_health_failed` / `capture_timestamp_invalid` / `capture_fault` | `ApplicationSession.m` / `ControlledCapture.m` | Check `fault_reason` in `latest-native.json`: `1`=format, `2`=ring overflow, `3`=source identity, `4`=aggregate not running, `5`=watchdog, `6`=teardown, `7`=invalid HAL input timestamp. Re-check System Audio Recording permission if capture fails immediately on Start. |
| `transport_peer_exit` / `lease-expired` / `renderer-exit` | `configured_transport.py` / `audio_guardian.py` | Inspect `transport.peer` (`local`, `remote`, `server`), `transport.exit` (`255` indicates SSH disconnect), and `MACBEE_GUARD_FINAL` in `*-client.log`. If a single remote receiver drops mid-session, it marks `Offline` while other outputs continue; local loss stops the session safely. |
| `runtime_capacity` | `relay_runtime.py` | `~/Library/Application Support/Macbee/relay-runtime/` exceeded 16 MiB or volume free space fell below 1 GiB (startup auto-prunes completed `relay-*` directories to 20 runs / 8 MiB, so this usually indicates < 1 GiB free disk space or oversized leftover files). Free disk space or inspect `relay-runtime/` when Macbee is idle. |

---

## Further Reference

- [README (English)](../../README.md) · [简体中文说明](../../README.zh-CN.md)
- [Architecture Overview](../../docs/ARCHITECTURE.md)
- [Offline Backend Release & Corresponding Source](../../docs/RELEASE-BACKEND.md)
- [Candidate Behavior & Known Limits](../../docs/NEXT-USER-GUIDE.md)
