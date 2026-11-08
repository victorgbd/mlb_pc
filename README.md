# MLB The Show 24 for Windows (PC port)

A native port of MLB The Show 24 (PS4, CUSA43942): the original game code runs directly on
Windows 10 (1903+) and 11, 64-bit, with a Vulkan renderer for the PS4 GPU. No game files are
included: players need their own decrypted dump (the folder with `eboot.bin`, `PS4\`,
`sce_module\` and `sce_sys\`). The launcher's art comes from that folder at run time.

Players: see [packaging/windows/README-MLB.txt](packaging/windows/README-MLB.txt) (it ships as
`README.txt` in the package). Port notes, fixes and known gaps: [docs/MLB_PORT.md](docs/MLB_PORT.md).

## Disclaimer

This is an unofficial fan project. It is not affiliated with, endorsed by or sponsored by Sony
Interactive Entertainment, San Diego Studio, Major League Baseball, the MLB Players Association
or any of their partners. "MLB The Show", "MLB" and the related names and logos are trademarks
of their owners; they appear here only to say which game this port works with.

- No game files are included in this repository or in its packages, and none will be.
- You need your own legally obtained copy of the game. This project does not provide, link to
  or explain how to obtain game files, decryption keys or tools that bypass copy protection;
  requests for them, and links to them, will be removed.
- Online features are not supported and the port does not connect to Sony's or MLB's servers.
- The software is provided as is, without any warranty (see LICENSE).

*Proyecto no oficial, sin relación con Sony Interactive Entertainment, San Diego Studio, MLB ni
la MLBPA. No incluye archivos del juego: necesitas tu propia copia legal. No se facilitan
archivos del juego, claves ni herramientas para saltarse la protección anticopia.*

## Build

Install [MSYS2](https://www.msys2.org) (`winget install MSYS2.MSYS2`), open the
**MSYS2 CLANG64** shell and install the toolchain and libraries:

```bash
pacman -S --needed git patch \
  mingw-w64-clang-x86_64-{toolchain,cmake,ninja,pkgconf,python} \
  mingw-w64-clang-x86_64-{vulkan-headers,vulkan-loader,vulkan-memory-allocator,glslang} \
  mingw-w64-clang-x86_64-{sdl3,fmt,boost,robin-map,xxhash,ffmpeg,zydis} \
  mingw-w64-clang-x86_64-{spirv-tools,spirv-headers,spirv-cross} \
  mingw-w64-clang-x86_64-{protobuf,openssl,zlib,miniupnpc,nlohmann-json}
```

magic_enum, miniz, xbyak and the other libraries MSYS2 does not package are submodules. Then,
in the repository:

```bash
git submodule update --init --recursive
bash build.sh                           # out/bb-probe.exe, out/bb-gpu-capabilities.exe, out/bb-play.exe
BB_GAME_DIR=C:/path/to/CUSA43942 python run.py
python launcher/mlb_launcher.py         # the launcher, from a python.org Python with Pillow
bash packaging/windows/package_mlb.sh   # dist/mlb-windows/ and dist/mlb-windows.zip
```

`run.py` prepares the game (linked image, content profile, trophies) and starts `bb-probe.exe`;
`BB_*` variables tune it (docs/MLB_PORT.md). The package freezes the launcher with PyInstaller
(a private venv in `out/pyenv`) into `MLB The Show 24.exe`, which also runs `run.py` (`--run`),
the preparation scripts (`--script`) and the game without the window (`--play`): players need
no Python. `Play MLB The Show 24.exe` (`packaging/windows/play.c`) runs
`MLB The Show 24.exe --play` from its folder and waits for it (shortcuts, Steam).

## Licence

GPL-2.0-or-later (LICENSE). The renderer is built on shadPS4 (GPL-2.0-or-later); the licences
of the bundled libraries are in `packaging/windows/licenses/` and ship in the package.
