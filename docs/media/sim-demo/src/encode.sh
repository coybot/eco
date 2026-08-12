#!/bin/bash
# Encode sim-demo.mov (a real screen recording of drone/sim's Godot-rendered
# heterogeneous mission demo) into a looping GIF for the README.
#
# Notes on the settings:
#  - fps=10 / width=680: this source has real shading, motion blur, and
#    on-screen text (unlike the flat vector panels in ../../autonomy/src/),
#    so it needs more colors and dithering to stay legible - traded off
#    against file size (bayer dithering compresses much better than
#    floyd_steinberg for GIF's inter-frame LZW compression, at a small
#    quality cost that doesn't matter at this frame rate).
#  - max_colors=80, bayer_scale=4: the smallest settings that kept the
#    status-bar text and mission caption crisp in a manual check.
set -euo pipefail
cd "$(dirname "$0")"

WIDTH=680
FPS=10
COLORS=80

ffmpeg -v error -y -i sim-demo.mov \
  -vf "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,palettegen=max_colors=${COLORS}:stats_mode=full" \
  /tmp/sim-demo-palette.png

ffmpeg -v error -y -i sim-demo.mov -i /tmp/sim-demo-palette.png \
  -lavfi "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4" \
  -loop 0 ../sim-demo.gif

rm -f /tmp/sim-demo-palette.png
bytes=$(stat -f%z ../sim-demo.gif)
printf "sim-demo.gif  %sK\n" "$((bytes / 1024))"
