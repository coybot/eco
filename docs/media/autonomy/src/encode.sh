#!/bin/bash
# Encode captured frame sequences into looping GIFs for the README.
#
# Notes on the settings:
#  - frames are captured at 20fps / deviceScaleFactor 2, then resampled with the
#    fps filter (which preserves loop duration) and downscaled with lanczos.
#  - dither=none: these scenes are flat dark panels plus a few glow halos.
#    Dithering adds per-pixel noise that defeats GIF's inter-frame compression
#    and roughly doubled the file size for no visible gain here.
#  - width 796 keeps the 10px mono HUD text legible at GitHub's content width.
set -euo pipefail
cd "$(dirname "$0")"

FPS=15
WIDTH=796
COLORS=48

mkdir -p out
total=0
for scene in scene1 scene2 scene3; do
  ffmpeg -v error -y -framerate 20 -i "frames/$scene/%04d.png" \
    -vf "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,palettegen=max_colors=${COLORS}:stats_mode=full" \
    "out/${scene}-palette.png"

  ffmpeg -v error -y -framerate 20 -i "frames/$scene/%04d.png" -i "out/${scene}-palette.png" \
    -lavfi "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle" \
    -loop 0 "out/${scene}.gif"

  n=$(ffprobe -v error -count_frames -select_streams v:0 \
        -show_entries stream=nb_read_frames -of csv=p=0 "out/${scene}.gif")
  bytes=$(stat -f%z "out/${scene}.gif")
  total=$((total + bytes))
  printf "%s  %sK  %s frames  %.1fs loop\n" \
    "$scene" "$((bytes / 1024))" "$n" "$(echo "$n/$FPS" | bc -l)"
done
rm -f out/*-palette.png
printf "total  %sK\n" "$((total / 1024))"
