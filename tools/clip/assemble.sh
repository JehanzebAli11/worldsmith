#!/bin/bash
# Assemble the episode-1 clip (1080x1350, 4:5, H.264) from the two Studio recordings and the cards.
# usage: assemble.sh REC_BEFORE REC_AFTER OUT.mp4 [REC_ORBIT]
# Footage is never sped up, cut inside or reversed: each recording is trimmed only at its ends
# (set TRIM_*_START / TRIM_*_END in seconds) and shown at real speed with the overlay on top.
set -euo pipefail
cd "$(dirname "$0")"
B="$1"; A="$2"; OUT="$3"; ORBIT="${4:-}"
PAPER="0xF6F0E6"   # the site's --color-paper, approximated as sRGB for ffmpeg's pad colour
TB0="${TRIM_BEFORE_START:-0}"; TB1="${TRIM_BEFORE_END:-}"
TA0="${TRIM_AFTER_START:-0}"; TA1="${TRIM_AFTER_END:-}"
T=$(mktemp -d)
card() { ffmpeg -loglevel error -y -loop 1 -t "$2" -i "cards/$1.png" -f lavfi -t "$2" -i anullsrc=r=48000:cl=stereo \
  -vf "scale=1080:1350,format=yuv420p,fade=t=in:st=0:d=0.25,fade=t=out:st=$(echo "$2-0.25" | bc):d=0.25" -r 30 \
  -c:v libx264 -preset veryfast -crf 18 -c:a aac -shortest "$T/$1.mp4"; echo "$T/$1.mp4"; }
foot() { # $1 input, $2 overlay png, $3 start, $4 end (may be empty), $5 name
  local dur=(); [ -n "$4" ] && dur=(-to "$4")
  ffmpeg -loglevel error -y -ss "$3" "${dur[@]}" -i "$1" -i "cards/$2.png" -f lavfi -i anullsrc=r=48000:cl=stereo \
    -filter_complex "[0:v]${CROP:+crop=$CROP,}scale=1080:-2:force_original_aspect_ratio=decrease,pad=1080:1350:(ow-iw)/2:(oh-ih)/2:color=$PAPER,setsar=1[v];[v][1:v]overlay=0:0,format=yuv420p[o]" \
    -map "[o]" -map 2:a -r 30 -c:v libx264 -preset veryfast -crf 18 -c:a aac -shortest "$T/$5.mp4"; echo "$T/$5.mp4"; }
list="$T/list.txt"; : > "$list"
if [ -n "$ORBIT" ]; then echo "file '$(foot "$ORBIT" o1_orbit 0 4 orbit)'" >> "$list"; else echo "file '$(card c1_title 4)'" >> "$list"; fi
echo "file '$(card c2_scene 5)'" >> "$list"
echo "file '$(foot "$B" o3_before "$TB0" "$TB1" before)'" >> "$list"
echo "file '$(card c4_before 2.5)'" >> "$list"
echo "file '$(card c5_diff 5)'" >> "$list"
echo "file '$(foot "$A" o6_after "$TA0" "$TA1" after)'" >> "$list"
echo "file '$(card c7_after 4)'" >> "$list"
echo "file '$(card c7b_architect 6)'" >> "$list"
echo "file '$(card c8_end 5)'" >> "$list"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$list" -c copy -movflags +faststart "$OUT"
ffprobe -v error -show_entries format=duration:stream=width,height -of default=nw=1 "$OUT"
rm -rf "$T"
