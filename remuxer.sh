#!/usr/bin/env bash
set -euo pipefail

SRC="output"          # no trailing slash
DST="output_flac"

# Recreate the directory tree
find "$SRC" -type d -print0 | while IFS= read -r -d '' dir; do
  mkdir -p "$DST${dir#"$SRC"}"
done

# Walk files; remux mp4 -> flac, copy everything else as-is
find "$SRC" -type f -print0 | while IFS= read -r -d '' f; do
  rel="${f#"$SRC"}"
  out="$DST$rel"
  case "$f" in
    *.mp4)
      out="${out%.mp4}.flac"
      ffmpeg -nostdin -i "$f" \
        -map 0:a -map 0:v \
        -c copy -disposition:v attached_pic \
        -map_metadata 0 \
        -n "$out" \
        || echo "FAILED: $f"
      ;;
    *)
      cp -p "$f" "$out"
      ;;
  esac
done
