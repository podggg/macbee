# Macbee 1.2.6-dev candidate

Apple Silicon, macOS 14.2 or later. Uses the default 48 kHz stereo output on each
selected Mac. The sender needs `/usr/bin/python3` 3.9 or later; receivers need
Python 3.9 or later and a logged-in, usable audio session. This is an ad-hoc
signed development build, not Developer ID signed or notarized. A security
warning is not a reason to disable macOS protection; no bypass is included.

## Sharing

1. Choose the intended outputs. Macbee shares **Entire Mac** audio.
2. Click **Start**, even before playing anything. Connections are verified first.
   While the source is silent, the window says **Waiting for audio**.
3. Play audio in ordinary apps or switch apps without starting again.
4. Click **Stop** to end sharing. Closing or quitting also stops our renderers
   before releasing the tap. Nothing automatically starts on launch.

Entire Mac includes notifications and calls, and does not need playback first.
There is no microphone capture or recording-file feature. Use this deliberately
with the receivers you select. Macbee excludes its exact local renderer from the
tap to prevent feedback. Changing that renderer's identity stops sharing rather
than guessing a new one. User-controlled volume and mute do not act as the share
Stop control.

## Output list and SSH setup

The existing configured pair is preserved and migrated without inventing devices.
Selections are remembered on explicit Start. A new receiver is **not selected**
automatically. Available reflects a successful recent check; Start verifies again.
Configured means saved, not proven online. Offline is not the same as unpaired.

**Add Mac…** uses an existing owner-authenticated, host-verified SSH connection.
The explicit **Set Up & Add** button stages only bundled Macbee receiver components
in a versioned private Application Support directory on that receiver, verifies
hashes and read-only output metadata, then saves the device locally. No audio
starts. It does not enable Remote Login, install a background service, accept an
unknown/changed host key, request passwords or save credentials. A Mac with no
working trusted SSH setup needs that prerequisite arranged separately; this is
not a zero-setup consumer onboarding flow.

Remote Mac identity is verified and stored as a one-way identity digest in private
configuration. Start rejects duplicate receiver identities, including an SSH
alias that points back at the sender, before starting renderers. No machine
addresses, configuration or identity digests are embedded in the released source
or application. Hardware output selection is still **Default output**, not an
arbitrary speaker/device picker. This first implementation bounds the list at
16 total outputs; four receiver instances are covered synthetically, not sixteen
physical-Mac performance qualification.

An individual remote receiver may become Offline while the remaining selected
outputs continue. It is not automatically reconnected or reauthorized. Failure
of the source, shared server, last output or local renderer identity stops the
session. A lost receiver is not falsely reported as confirmed clean shutdown.
Independent short renewable leases remain active if the sender exits or the
SSH path fails. They are not an elapsed 30-minute playback limit.

## Diagnostics and remaining limits

For Entire Mac with configured outputs, a late server read no longer moves the
whole playback timeline forward. The server may catch up on already queued
20 ms chunks, in order, yielding between reads. Short delays need not stop
sharing. A 250 ms stale-media or progress guard still stops genuine stalls.
Macbee now checks how much the local server actually consumed rather than
assuming a successful pipe write means it played. This is not a receiver
playback measurement. No audio, keys or screenshots are recorded. The change
has isolated server coverage, not yet paired-Mac listening acceptance.

The previous build could conflate STOP, control EOF and a renderer's spontaneous
exit, and lose the first native PCM-pipe error. This candidate distinguishes
those events and keeps bounded, local, content-free first-stop evidence. It
records fixed reason codes and timing/queue counters, not audio, media titles,
application names, PIDs, addresses or credentials. Nothing uploads automatically.
See SESSION-STOP-DIAGNOSTICS.md in the source for the exact bounds and tests.

The original intermittent short dropouts and spontaneous upstream stops have
not been assigned a proven initiating cause. Deliberately injected pacing,
health, pipe and peer failures are reproduced offline; that does not prove which
one caused a user's earlier session. These safeguards have not been disabled.
Physical multi-Mac sync, long sessions, video lip-sync, new-receiver onboarding,
and actual OS mute/exclusion behavior need controlled real qualification; offline
passing tests do not establish those results. No iOS receiver is qualified here.

Check for Updates opens the Macbee GitHub Releases page. It does not
store GitHub credentials, download silently or replace a running application.
Macbee is licensed under GPL-3.0-or-later; bundled Snapcast and other dependency
notices remain in Resources/Licenses, and matching corresponding source is
available alongside each release's binary assets.


The 1.2.3 development candidate can keep an existing session connected
across a short input gap. You may hear a brief silence while both outputs
resume on the same playback timeline. If the sender no longer receives data,
or a safe playback deadline cannot be met, sharing still stops rather than
playing old sound. This is not a guarantee of uninterrupted playback.
