#!/usr/bin/env bash
# Project the captured interface without redrawing any UI elements.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_image="$repo_root/docs/screenshots/mqtt-overview.jpg"
output_image="$repo_root/docs/screenshots/mqtt-hero.png"
if ! command -v magick >/dev/null 2>&1; then
  printf '%s\n' 'ImageMagick 7 is required (the magick command).' >&2
  exit 1
fi
if [[ "$(magick identify -format '%wx%h' "$source_image")" != '1600x1000' ]]; then
  printf '%s\n' 'Screenshot dimensions changed; review the perspective first.' >&2
  exit 1
fi
render_dir="$(mktemp -d "${TMPDIR:-/tmp}/mqtt-hero.XXXXXX")"
trap 'rm -rf -- "$render_dir"' EXIT
magick "$source_image" -alpha on \
  \( -size 1600x1000 xc:none -fill white \
     -draw 'roundrectangle 0,0 1599,999 20,20' \) \
  -compose DstIn -composite -compose Over \
  -virtual-pixel transparent -filter Robidoux \
  -define distort:viewport=1780x1240+0+0 \
  -distort Perspective \
  '0,0 110,200 1599,0 1590,70 1599,999 1660,1050 0,999 160,1170' \
  -strip "$render_dir/panel.png"
magick "$render_dir/panel.png" -fill '#152033' -colorize 100 \
  -channel A -evaluate multiply 0.22 +channel -blur 0x18 \
  -strip "$render_dir/shadow.png"
magick -size 1780x1240 xc:'#f0f1f3' \
  "$render_dir/shadow.png" -geometry +0+18 -compose Over -composite \
  "$render_dir/panel.png" -geometry +0+0 -compose Over -composite \
  -strip -define png:exclude-chunk=date,time "PNG24:$render_dir/hero.png"
mv -- "$render_dir/hero.png" "$output_image"
printf 'Rendered %s\n' "$output_image"
