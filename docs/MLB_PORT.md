# MLB The Show 24 on the Uncharted port

Branch `mlb24` of this repository runs MLB The Show 24 (CUSA43942) on the same loader, HLE
runtime and shadPS4 renderer as the Uncharted collection. You need your own dump: the folder
with `eboot.bin`, `PS4/`, `sce_module/` and `sce_sys/`. No game files are in this repository.

## Running

```bash
bash build.sh                                   # MSYS2 CLANG64, see packaging/windows/README.md
BB_GAME_DIR="C:/path/to/CUSA43942" python run.py
```

`run.py` prepares `out/eboot/boot-linked.bin` the first time (and again when the dump or the
scripts change), then starts `out/bb-probe.exe`. The log is printed to the console.

## What was changed for MLB

- **Executable:** the dump's executables are fake-signed SELFs (no encrypted segments), which
  `scripts/prepare.py` already reads.
- **Linked modules:** besides `libc.prx` and `libSceFios2.prx`, the game ships `PS4/ffmpeg.prx`
  and needs it at start (DT_NEEDED). `scripts/link_modules.py` links game modules outside
  `sce_module/` (`GAME_MODULES`) and gives no TLS module ID to an empty PT_TLS.
- **Imports:** the eboot and its three modules import 634 functions from the system. 465 were
  already in the Uncharted runtime; the other 169 are new:
  - `src/runtime_mlb.c`: libSceJson2 (a real parser behind the SDK's `sce::Json` objects),
    Audio3d (ports run, AudioOut ports play, 3D objects are not rendered yet), USB storage,
    browser/sign-in/USB dialogs (refused), touch gestures (no events), core dumps, PS VR,
    game intents, live streaming, share play, random numbers, temporary data, RFC 3339 dates,
    and kernel functions libc and the game call at start;
  - `src/runtime_imagedec.c`: libScePngDec and libSceJpegDec on stb_image (`third_party/stb`);
  - POSIX rwlocks (`runtime_rwlock.c`), thread names/cancel/sched params (`runtime_thread.c`),
    `scePthreadCondSignalto` (`runtime_mutex.c`), fcntl/pwritev/getdents/chmod/Lwfs
    (`runtime_file.c`), `sceKernelMmap` and `sceKernelMemoryPoolExpand` (`runtime_memory.c`),
    POSIX `inet_pton`/`inet_ntop` (`runtime_services.c`).
- `src/import_names.inc`: names for MLB's imports (from shadPS4's aerolib, via
  `tools/import_names.py`).
- `run.py`: one executable; the Uncharted shader hashes for the upscaler and the per-game GPU
  readback defaults are no longer set. `scripts/trophies.py`: the MLB trophy set (NPWR38674_00).

## Fixes found while running the game

- **libkernel / libScePosix:** MLB's libc imports POSIX functions from libkernel that the runtime
  had under libScePosix; `runtime_resolve` tries the other spelling (same NID, same contract).
- **SSE4a (EXTRQ, INSERTQ):** AMD only; the game has 98 of them. `cpu_patch.c` rewrites them
  (tests/test_sse4a.c checks every form against the AMD definition). The thunks take memory from
  the red zone's early reservation (`cpu_patch_set_hooks`): after the GPU starts, nothing was left
  within rel32 reach of MLB's larger image.
- **ATRAC9 configuration:** `sceAjmDecAt9ParseConfigData` must fill frame samples (+0xC) and
  frame bytes (+0x10); with 0 the audio streaming thread looped forever on the lineup screen.
- **Mapping addresses:** mappings without an address start at `BB_MAP_BASE` (0x8000000000).
  First-fit from 0x1000000000 had put the first direct mappings where the game later maps its
  pools at fixed addresses, which replaced its shaders ("Shader binary info not found").
- `sceKernelLoadStartModule` of an already linked module (PS4/ffmpeg.prx) succeeds; `/download1`
  is mounted; `sceSystemServiceGetPlatformPrivacySetting` takes one pointer.

Status: title screen, menus, Exhibition, team and stadium select and a full game run
(Diamondbacks at Rangers, Globe Life Field).

## Table images (the crowd)

The crowd's pixel shaders pick each instance's texture from a bindless table of T#s (about 950
valid entries; indices such as 4, 5, 254, 826, 915 come from a flat vertex attribute) inside
waterfall loops: `s_buffer_load_dwordx8` of `table[readlane(index) * 8]`. A bind-time
descriptor cannot follow that, so these textures were null and the crowd rendered black.

- `resource_patching_pass.cpp` (`FindTableSharp`): a T# whose eight dwords are unflattened
  `ReadConstBuffer`s of one buffer at `index * stride + base + i` becomes a table image
  (`ImageResource::is_table`: the buffer's V#, base, stride, 1024 entries). A new IR instruction,
  `SetImageTableIndex(binding, index)`, goes before each access.
- SPIR-V: the binding is an array of 1024 images; `SetImageTableIndex` stores the index in a
  Private variable that the image loads use (clamped).
- `Rasterizer::BindTextures`: reads the table's entries from guest memory and binds each valid
  one (fully mapped, plausible size, 2D view); the rest bind null. Fast paths that assume one
  image per binding (helper thread, memoized sets) are skipped for these pipelines.
- `AddBindings` counts array elements; the sampled image pool holds 65536 descriptors; the
  shader cache version is 13.
- `BB_IMAGE_TABLES=0` turns it off. Frame rate is the same with and without it (GTX 1650: 45-55).

## Two reads of one T# (cube map probes)

Pixel shader b9c1fcef samples the same cube map T#s twice, each time through its own copy of
the V#. Value numbering kept the two reads apart, and the flatten pass's list of reads per
pointer and offset kept only the last one: the first read got no slot in the flattened buffer
("Sharp source was not flatenned", 16 times) and its texture bound null.
`FlattenExtendedUserdataPass` now gives such reads the slot of the first one (shader cache
version 14).

## Alpha to coverage (the nets)

The protective nets (behind home plate, along the lines) were opaque gray walls that hid the
crowd and, from the stands camera, the whole field. They are drawn in a depth pass with no color
target whose pixel shader only exports MRT0's alpha (the net texture's): `DB_ALPHA_TO_MASK` turns
that alpha into coverage, so the holes write no depth and the color pass (depth Equal) skips
them. shadPS4 had no alpha to coverage, and with no color target the export was dropped: the
shader was empty and the whole quad wrote depth.

- `regs.h`: `DB_ALPHA_TO_MASK` (0xA2DC: enable, four 2-bit offsets, round).
- `HwFragmentRuntimeInfo`: `alpha_to_mask`, the offsets and MRT0's export format (taken from
  `SPI_SHADER_COL_FORMAT` even when MRT0 has no target).
- `Translator::AlphaToMask`: on MRT0's export, discard when alpha is below
  `1 - (offset + round / 2) / 4`, the offset picked by the pixel's position in its 2x2 quad.
- `BB_ALPHA_TO_MASK=0` turns it off. Shader cache version 15.

## Garbage T#s (device lost in a game)

One run lost the device a few pitches into a game: the GPU wrote just past a 1.5 GiB staging
buffer, right after the page tracker warned about a 1.5 GiB region at 0x3e6147a000 that is not
GPU mapped. A texture binding had a T# whose start address passed the only check (the address
fits in 40 bits: `IsValidGpuMapping` checks nothing else in this port) but that described a
1.5 GiB texture over unmapped memory, and the texture cache tried to upload it. The table image
path already refused such entries; plain bindings did not.

- `ImageDescCacheEntry::implausible` (built once per T#): empty, wider or taller than 16384, or
  larger than 256 MiB.
- Where the image is looked up, `Rasterizer::IsMapped` (the GPU-mapped ranges) must cover the
  whole texture.
- Rejected bindings stay empty (null descriptor) and print `Texture rejected (...)`, the first 50.
  A normal game prints none.
- Images also reach the texture cache through other paths (render targets, copies) with garbage
  addresses: 0, 0x3f80000000 and 0x1802720000 in one long game (151 pitches, replays). Their
  upload read nothing (`CopySparseMemory` skips holes), but `ReplaceButtonPrompts` (an Uncharted
  feature) hashed the guest bytes directly and faulted on the CPU. `RefreshImage` now uploads
  nothing for an image whose memory is not all mapped (`Rasterizer::IsMapped`) and prints
  `Texture cache: not uploading ...` (the first 20).

## Stale GPU counters (device lost while batting)

Batting lost the device after about six minutes, every time: the GPU hung (instruction pointers,
no fault address) in compute shader a0eec41b, the players' skinning, whose morph loop count
comes from a small constant buffer. Skipping that shader (`BB_SKIP_SHADERS`) stopped the losses.

The constants sit in a ring the game also hands to two compute shaders: 5b1421f7 clears 20-byte
records and 39035e05 adds to them atomically (found with `BB_WATCH_GPU_WRITE` and
`BB_WATCH_GPU_BYTES`, which now print every GPU-written range over a window). The ring is reused,
so a later frame puts skinning constants where a counter was. When the guest writes a page with
GPU data, a page whose GPU work is done is read back on the side queue; otherwise the write is
merged (`ShadowGpuData`): GPU bytes stay wherever the guest's new bytes equal the old ones. That
is right for data the GPU is still writing, and wrong for a counter left by long-finished work:
a constant rewritten with its previous value kept the counter's value, and the skinning looped
on it for seconds.

- `BufferCache::gpu_write_ticks`: the scheduler tick of the latest write of every GPU-written
  range.
- `DownloadCompletedRanges`: before a merge, the page's ranges whose work has completed are read
  back on the side queue (they are final, as the console's memory would hold them); only ranges
  still being written are merged.
- Batting 20 minutes (78 pitches) with the fix: no loss. Without it: lost at 6 minutes in 3 of 3
  runs. `BB_GPU_MERGE=0` (read back instead of merging) did not help: it lost the device in a
  crowd draw after 3 minutes.
- That fix was not enough: one run still lost the device at six minutes. The game reuses the
  ring faster than our queue retires the work (its fence has passed, our Vulkan work is still
  queued), so the counters were still "pending" when the guest wrote over them. run.py now
  turns on `BB_READBACK_SMALL_BUFFERS=1`, `BB_READBACK_SMALL_GATE=1` and
  `BB_READBACK_SMALL_MAX=4096` (new: the cap was 16 ranges per end-of-pipe event): written
  buffers of 64 bytes or less land back in guest memory when their work is done, and the
  end-of-pipe label waits for them, so the guest's copy holds the counters and a constant
  written over one is seen as a change. No loss in four runs with it (one of 70 pitches).
- `IT_SET_PREDICATION` (occlusion predication) is logged once: some scenes send it for every
  draw (100 000+ lines). Draws run unconditionally.

## Release package and launcher

`bash packaging/windows/package_mlb.sh` (MSYS2 CLANG64, after `bash build.sh`) builds
`dist/mlb-windows.zip`: "MLB The Show 24.exe" (`launcher/mlb_launcher.py`, frozen with
PyInstaller), "Play MLB The Show 24.exe" (`packaging/windows/play.c`: starts the game with the saved
settings), `bin/` (bb-probe and its DLLs), `scripts/`, `run.py`, `README.txt`
(`packaging/windows/README-MLB.txt`). No game files: the launcher's banner, cover and Steam pictures
come from the player's `sce_sys/` at run time.

- The launcher is the Uncharted one (`uncharted_launcher.py`) cut to MLB: one game, the Game, Display,
  Controls, Trophies, Advanced and Log pages, Steam and desktop shortcuts, `--play`. Anti-aliasing,
  frame generation, render resolution and cheats are left out until they work with MLB.
- Advanced -> Graphics fixes turns the crowd tables (`BB_IMAGE_TABLES`) and alpha to coverage
  (`BB_ALPHA_TO_MASK`) off, for comparing.
- `launcher/mlb_i18n.py`: Spanish texts on top of the shared tables; the interface follows the
  Windows display language.
- The icon (`launcher/mlb.ico`, `mlb.png`) is original art from `packaging/windows/make_mlb_icon.py`.
- Updates: set `RELEASES_REPO` ("owner/repo") in `mlb_launcher.py` to have the launcher check that
  repository's GitHub releases; empty, the update check is hidden.

## Tools

- `tools/thread_sample.c`: where each thread of a running game is (hangs, busy waits).

- `python tools/import_gap.py aerolib.inl GAME_DIR` lists the game's system imports that the
  runtime does not provide (a static check: a provided function may still be a simple stub).
  `aerolib.inl` is `src/core/aerolib/aerolib.inl` from shadPS4.

## Player faces (black or dirt)

Some players (Evan Carter every time) showed a black face, or the infield dirt where the skin
should be. The game builds part of the player face textures on the GPU (BC1 1024x512 near
0x806xxxxxxx; their guest memory stays zero) and writes them through storage buffers that
start before the texture or cover only part of it. A storage-buffer write invalidated only the
images starting at the buffer's address, so those faces kept what the cache had: zeros (black)
or the texture there before (dirt). Found with `BB_TEXTURE_DUMP=1` and F10: of the
GPU-built faces, 2 of 4 were black; after the change, none of 6.

- `TextureCache::InvalidateMemoryFromGPU`: every image the written range overlaps is marked
  GPU-dirty (re-read from the buffer cache), with `Texture cache: GPU buffer write ...` for the
  first 30 that the old rule left alone.
- `ExpandImage` (an uncompressed image re-made as a compressed one, e.g. R32G32 to BC1) now takes
  the guest data hashes of a GPU-modified image, which keep it from being re-uploaded from guest
  memory at the next CPU write to its pages; `Texture cache: GPU-written ... re-uploaded` reports
  such re-uploads (the first 30).
- Alpha to coverage honours `DB_SHADER_CONTROL.ALPHA_TO_MASK_DISABLE` (a pixel shader opting out
  while the register stays enabled). Shader cache version 16.

## Known gaps

- `sceKernelGetModuleInfoForUnwind`: module tables are not given to the guest, so a C++
  exception thrown by the game cannot be unwound.
- `sceKernelLoadStartModule` refuses modules loaded after start (logged with the path).
- Online features: the console is offline (no PSN, no servers).
- `g_guest_frames` (the guest frame counter used by the buffer cache's reports) stays 0: its
  hooks are Uncharted's.
- The upscaler, frame generation and resolution options need MLB's renderer passes identified
  (docs/upscaler.md); until then they stay off.
