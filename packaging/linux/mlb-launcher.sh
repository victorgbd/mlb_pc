#!/usr/bin/env sh
# Starts the MLB The Show 24 launcher (needs python3 with tkinter and Pillow).
cd -- "$(dirname -- "$0")" || exit 1
exec python3 launcher/mlb_launcher.py "$@"
