MLB The Show 24 - unofficial PC port, Linux build (EXPERIMENTAL)
================================================================

This Linux build compiles and packages, but it has NOT been tested in a real game yet.
Expect problems; reports are welcome (launcher: Log page > Report a problem).

No game files are included: you need your own decrypted copy of MLB The Show 24 (CUSA43942).

Requirements
------------
- A 64-bit x86 distribution with glibc 2.43 or newer (built on Ubuntu 26.04).
- A Vulkan 1.3 GPU and driver (Mesa RADV, NVIDIA proprietary).
- Python 3 with tkinter and Pillow for the launcher, plus SDL3, FFmpeg and the
  Vulkan loader. On Ubuntu/Debian:
    sudo apt install python3-tk python3-pil python3-pil.imagetk libsdl3-0 libavcodec62 libavutil60 libvulkan1

Start
-----
    ./mlb-launcher.sh

Choose the game folder (the one with eboot.bin) on the Game page and press Play.
Without the launcher:  BB_GAME_DIR=/path/to/CUSA43942 python3 run.py

Not available on Linux yet: installing from a PKG file, the automatic updater (the
launcher opens the releases page instead), Steam shortcuts.
