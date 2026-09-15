#!/usr/bin/env bash
# Usage: ./play.sh SECONDS — play track1 for SECONDS, 1s crossfade to static (2s solo), 1s crossfade to track2.
set -euo pipefail
N=${1:?usage: ./play.sh SECONDS}
cd "$(dirname "$0")"

SOCK=/tmp/radio-mpd.sock
TRACK1="$PWD/Dea - Basma [fgVil4xoxyU].mp3"
STATIC="$PWD/Radio Static - Sound Effect [jmZIjDJeWYE].mp3"
TRACK2="$PWD/Deep Time [FL_9eOgwJE8].mp3"
FADE=1         # MPD crossfade accepts whole seconds only
STATIC_SOLO=2

mpd_cmd() {
	local out
	out=$(printf '%s\n' "$@" close | socat - "UNIX-CONNECT:$SOCK,shut-none")  # MPD drops the client on half-close
	if grep -q '^ACK' <<<"$out"; then echo "$out" >&2; exit 1; fi
	echo "$out"
}
add() { mpd_cmd "addid \"file://$1\"" | sed -n 's/^Id: //p'; }

[[ -S $SOCK ]] || mpd "$PWD/mpd.conf"

# mixrampdelay nan: MixRamp would override the plain crossfade
mpd_cmd clear "repeat 0" "random 0" "single 0" "consume 0" "crossfade $FADE" "mixrampdelay nan" >/dev/null
id1=$(add "$TRACK1"); ids=$(add "$STATIC"); id2=$(add "$TRACK2")
# ranges must be set before playback; crossfade eats the last FADE seconds of each range
mpd_cmd "rangeid $id1 0:$N" "rangeid $ids 0:$((FADE * 2 + STATIC_SOLO))" "play 0" >/dev/null
