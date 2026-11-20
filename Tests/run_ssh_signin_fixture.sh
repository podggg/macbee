#!/bin/sh
# Explicit local-only acceptance fixture. The caller supplies a new empty
# scratch directory; no real ssh, agent, keys, Keychain or network are used.
set -eu
test "$#" -eq 1 && test -d "$1" && test -z "$(ls -A "$1")" || { echo 'Supply an empty fixture directory' >&2; exit 2; }
OUT=$(cd "$1" && pwd -P)
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$ROOT"
mkdir -m 700 "$OUT/home"
/usr/bin/clang -std=c11 -Wall -Wextra -Werror Sources/SSHLogin/askpass.c -o "$OUT/macbee-askpass"
/usr/bin/clang -std=c11 -Wall -Wextra -Werror Tests/ssh_fake_parent.c -o "$OUT/fake-ssh"
/usr/bin/clang -fobjc-arc -Wall -Wextra -Werror -framework Cocoa Sources/SSHLogin/SSHSignIn.m Tests/ssh_askpass_fixture.m -o "$OUT/fixture"
"$OUT/fixture" "$OUT/fake-ssh" "$OUT/macbee-askpass" "$OUT/home"
