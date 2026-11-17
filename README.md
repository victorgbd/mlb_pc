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

### Versions and updates

`VERSION` holds the release number (`MAJOR.MINOR.PATCH`). `build.sh` writes it into the native
executables and `package_mlb.sh` into the launcher (Properties > Details); the packaging stops when
they disagree. On start the launcher asks GitHub for the latest release of `victorgbd/mlb_pc` and,
when its `v<VERSION>` tag is newer, shows an Update button. Updating downloads the release's `.zip`,
closes the launcher, copies the new files over the installation (never `user/`, `art/`,
`bbport.ini` or `input.ini`) and opens it again. Settings > Updates turns the check off.

To release: change `VERSION`, run `bash build.sh` and `package_mlb.sh`, and publish a GitHub
release tagged `v<VERSION>` with `dist/mlb-windows.zip` attached.

## Tested on

A gaming laptop, with game version 1.21 and the launcher's default settings (60 FPS display rate,
Mailbox, 1920x1080 unless noted):

| Part | |
|---|---|
| CPU | Intel Core i5-10300H (4 cores, 8 threads, 2.5 GHz) |
| GPU | NVIDIA GeForce GTX 1650 4 GB (driver 32.0.16.2114) |
| RAM | 16 GB DDR4-2933 (2 × 8 GB) |
| OS | Windows 11 Home (build 26200) |

| Where | FPS |
|---|---|
| Menus | 60 |
| Match, 1920x1080 | 30-37 |
| Match, render resolution 1280x720 (FSR 1) | about 38 |
| Match, render resolution 960x540 (FSR 1) | about 41 |
| Match, 21:9 (2176x928) | 37-41 |
| Match, 32:9 (2688x760) | 35-40; drops to 10-25 while the stadium loads (4 GB of video memory is tight) |

The game is limited by the CPU on this laptop (the thread that processes the GPU command lists
uses most of a core while the GPU is half idle), so a lower render resolution gains little; a
weaker graphics card gains more. The 4 GB card runs at the edge of its video memory in a match.

## Credits

This port stands on the work of these projects:

- [bloodborne_pc](https://github.com/deadinside28/bloodborne_pc) (bbport) by deadinside28: the
  loader, the HLE runtime and the approach of running the PS4 executable directly; its Windows
  port and launcher are by [Supermedo](https://github.com/Supermedo). MLB came to it through the
  Uncharted collection port built on the same base.
- [shadPS4](https://github.com/shadps4-emu/shadPS4): the Vulkan renderer for the PS4 GPU (video
  core, shader recompiler, Gnm/VideoOut drivers) and the PKG/PFS code.
- [ps4-pkg-tools](https://github.com/xXJSONDeruloXx/ps4-pkg-tools): installing the game from its
  PKG files.
- [FSR-Vulkan](https://github.com/FireBurn/FSR-Vulkan) (AMD FidelityFX): FSR 1 upscaling and
  sharpening.
- [LibAtrac9](https://github.com/Thealexbarney/LibAtrac9): the game's audio.
- [SDL](https://github.com/libsdl-org/SDL): window, input and audio output.
- [Dear ImGui](https://github.com/ocornut/imgui), [sirit](https://github.com/shadps4-emu/sirit),
  [xbyak](https://github.com/herumi/xbyak), [Zydis](https://github.com/zyantific/zydis),
  [magic_enum](https://github.com/Neargye/magic_enum), [miniz](https://github.com/richgel999/miniz),
  [Vulkan Memory Allocator](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator),
  [Vulkan-Headers](https://github.com/KhronosGroup/Vulkan-Headers),
  [SPIRV-Tools](https://github.com/KhronosGroup/SPIRV-Tools), [glslang](https://github.com/KhronosGroup/glslang),
  [fmt](https://github.com/fmtlib/fmt), [Boost](https://www.boost.org),
  [robin-map](https://github.com/Tessil/robin-map), [xxHash](https://github.com/Cyan4973/xxHash),
  [FFmpeg](https://ffmpeg.org), [protobuf](https://github.com/protocolbuffers/protobuf),
  [OpenSSL](https://www.openssl.org), [zlib](https://zlib.net), [miniupnpc](https://github.com/miniupnp/miniupnp),
  [cpp-httplib](https://github.com/yhirose/cpp-httplib), [wepoll](https://github.com/piscisaureus/wepoll),
  [half](https://half.sourceforge.net), [nlohmann/json](https://github.com/nlohmann/json),
  [stb](https://github.com/nothings/stb), [DejaVu fonts](https://dejavu-fonts.github.io) and
  [Kenney input prompts](https://kenney.nl/assets/input-prompts).
- Launcher: Python, [Pillow](https://python-pillow.org), [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2)
  and [PyInstaller](https://pyinstaller.org).

## Licence

GPL-2.0-or-later (LICENSE). The renderer is built on shadPS4 (GPL-2.0-or-later); the licences
of the bundled libraries are in `packaging/windows/licenses/` and ship in the package.
