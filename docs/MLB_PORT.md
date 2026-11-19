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
    Audio3d (ports run, AudioOut ports play, objects mixed to stereo: see "Songs" below), USB storage,
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
- **Address range top:** the runtime reserves the PS4 range 0x1000000000-0xfc00000000 at start.
  Hosts with a 39-bit address space (Android kernels, under Winlator/Box64) cannot hold it:
  `BB_USER_MAX` (hex, at least 0x2000000000) lowers the top, for example `7000000000`, and
  `BB_MAP_BASE` then defaults to halfway up the range (0x4000000000), above the fixed pools.
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

- `BB_WATCH_WRITE=address` (hex, 4-byte aligned; `BB_WATCH_WRITE_LEN=8` for eight bytes): a
  hardware watchpoint on guest memory. Each write prints `Watch: write N by guest+0x...` with the
  code offset after the writing instruction, the 16 bytes there as floats and the code addresses
  on the stack (the first 60 writes). `BB_CAPTURE_FLIPS=N` makes an F10 capture
  (`BB_TEXTURE_DUMP=1`) span N flips instead of 4, to record something that happens once.

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

### Noise in the faces (coloured beards, see-through faces)

The game compresses each face on the GPU by writing an uncompressed image with one texel per
block (R32G32 over BC1, R32G32B32A32 over BC7) at addresses it reuses. When the compressed
texture of an earlier face was still cached there, the write went through an uncompressed view
of the compressed image, which is not a valid view, and the face came out as noise, on different
players in each session.

- `TextureCache::ResolveOverlap`: an uncompressed image requested where a compressed one of the
  same block size is cached is re-made (`ExpandImage`), the reverse of the existing case;
  `Texture cache: ... re-made as ...` reports the first 30.

### Black skin of players without a face scan (the skin tone palette)

Evan Carter and Josh H. Smith (generic heads: no photo on their cards either) kept a black
face with correct eyebrows, beard and relief, in games and on player cards. Their skin is the
base head texture times `tone / average` in pixel shader c65e272e, and the tone reached the
shader as (0, 0, 0, 0).

- The tone comes from `SkinTonePalette2016`, an RGBA8 256x512 texture (1D thin tiled, at
  0x8051ff1800) that a compute shader fills once at start (32x64 groups). The game then copies
  the texture's memory into a table (guest function 0x185fd90) and looks the colour up on the
  CPU (0x1860270). GPU-written images stay on the GPU here: the table was 524 288 zero bytes
  while the GPU image held the right gradient (texture dump).
- `BB_READBACK_TARGETS=WxH[,WxH...]` (run.py sets `256x512`): images of these sizes that a
  shader writes (storage images, render targets) are copied back to guest memory at the next
  end-of-pipe event or submission (`TextureCache::ReadbackTarget`), 1D thin tiled ones re-tiled
  on the CPU (`RetileMicro`: the guest detiles what it reads). The log shows
  `Texture cache: GPU-written 256x512 ... is read back to guest memory` once at start.
- Found with `BB_WATCH_WRITE` (below): the constants' second half was written as zeros by guest
  code at +0x1870f84, from the palette lookup.

## Songs (Audio3d objects)

The licensed songs were silent while everything else played. The game decodes them like its
other streams (ATRAC9 XVAGs packed in `PS4/AUDIO/MUSIC1.SKX`), but plays them as four Audio3d
objects (mono float PCM, 256 samples per granule, a gain and passthrough left/right; two carry
sound while a song plays), not through its AudioOut mix. `sceAudio3dObjectSetAttributes` dropped
them.

- `runtime_mlb.c`: each Audio3d port mixes its objects' buffers into a stereo granule
  (passthrough left/right to that side, others to both at -3 dB, gain applied; positions are not
  rendered), `sceAudio3dPortAdvance` closes it and `sceAudio3dPortPush` plays it through an
  AudioOut port of its own (2 ch float; a blocking push is paced by it). The log shows
  `Audio3d port 0 plays its objects (stereo)`.
- `BB_AUDIO3D_OBJECTS=0`: objects dropped as before. `BB_AUDIO_TRACE=1` also sums what the objects
  carry every 5 seconds.

## Known gaps

- `sceKernelGetModuleInfoForUnwind`: module tables are not given to the guest, so a C++
  exception thrown by the game cannot be unwound.
- `sceKernelLoadStartModule` refuses modules loaded after start (logged with the path).
- Online features: the console is offline (no PSN, no servers).
- `g_guest_frames` (the guest frame counter used by the buffer cache's reports) stays 0: its
  hooks are Uncharted's.
- Render resolution (2026-11-14): `BB_RENDER_SCALE=WxH` (or a percentage; launcher: Display >
  Render resolution) draws the scene smaller and FSR 1 rebuilds it. The scene starts at the first
  pass with the 1920x1080 depth; passes with that depth draw into reduced `SceneTargets` proxies
  (UI and post passes without it stay native). At the final composite (ps c684cde1, the pass
  writing the display buffer), `SceneTargets::UpscaleColors` runs EASU (+ RCAS,
  `BB_RENDER_SHARPNESS`, attenuation; negative = off) from each written color proxy into its
  native image. fsr.comp now keeps the input alpha. In a match on the GTX 1650: 37 FPS native,
  38 at 1280x720, 41 at 960x540 (the game is CPU-bound there; slower GPUs gain more).
- Aspect ratio (2026-11-14): `BB_MLB_RES=WxH` (launcher: Display > Aspect ratio: 16:10 1728x1080,
  21:9 2176x928, 32:9 2688x760) gives the game another size and shape (`src/runtime_display.c`).
  MLB's video out setup copies its size from two rodata tables ({1920x1080, 2560x1440, 3200x1800,
  3840x2160} for PS4 Pro, {1280x720, 1920x1080} for base) into the renderer's size globals; the
  renderer, the projection (the view widens, nothing stretches) and the UI layout follow them.
  The 1920x1080 table entries and the display frame buffer allocation (sized for a fixed 1920x1080:
  `mov edx,0x780; mov ecx,0x438`) are patched. The presenter enlarges the picture to the window.
  `SceneResolution::Native()` makes the render resolution scale the game's size (the launcher
  passes a percentage then; scaled sizes are multiples of 8).
  Fixed 2026-11-16: the main camera's aspect (camera +0x318) is set from a constant table
  {16/9, 4/3} (code at 0x81f5, table at 0x207383c), not from the size. The projection follows the
  size globals, but culling and the SSAO's view reconstruction (camera +0x4f8, tan of the half
  angles) used 16:9: at 21:9/32:9 the ground pieces at the sides were culled (black bands on the
  grass next to the dirt and the foul lines) and the 3D view was stretched sideways (players twice
  as wide at 32:9). The table entry is now w/h. The SSAO chain (Linear Depth Buffer,
  SSAOOffscreen, AO Buffer 2) is created at a fixed 960x544 and its texel size stored as a constant
  ({1/960, 1/544}, 0x134993f); both now follow half the size, like ExposureWeightingRT and
  DownsampledFB (fixed 240x136, now 1/8). The full size fixed targets (ScratchSurface0-2,
  ZBuffer1920, SubsurfaceRT/Final, spfx, depth, snapshot: `mov esi,0x780; mov edx,0x438` before
  the create call) stay 1920x1080: resized, the skin turned black.
  Fixed 2026-11-16: no UI at all in a match (HUD, scoreboard, presentation banners; the frame has
  no UI passes) when the height is not a multiple of 8: 2688x756 and 2432x810 had none,
  2688x760, 2560x720, 2176x928 draw it. The 32:9 preset is now 2688x760 and BB_MLB_RES is rounded
  to multiples of 8 (runtime_display.c and SceneResolution::Native()). Where the game checks it is
  not known.
  Limit: above a 1920x1088 area of 128x64-aligned pixels (1920x1200, 2240x960, 2208x928 fail;
  1920x1088, 2176x928, 2688x760, 1728x1080 work) the title screen sends a depth target with
  garbage registers (`z_format=2`, pitch 9064) and the GPU stops. Traced so far: the game's
  `setDepthRenderTarget` (0x1cc9d70) copies a valid descriptor from its context (dumped:
  z_info a010200b, size 1920x1200), yet the packet the GPU reads holds other values, and a
  watchpoint on the packet sees no later CPU write. Not the render pool (growing it, and the
  budget, through the base/Pro constants of the memory setup changed nothing), not
  sceVideoOutGetResolutionStatus, not the Bink frame buffers. The runtime refuses larger sizes.
  `BB_WATCH_WRITE=+offset` now watches an image-relative address (the image base moves).
- The upscaler, frame generation and resolution options need MLB's renderer passes identified
  (docs/upscaler.md); until then they stay off. What is known (2026-11-11):
  - MLB renders forward: scene color RGBA16F 1920x1080 with depth D32S8 1920x1080; no G-buffer,
    no velocity buffer, no temporal AA. One final pass (ps c684cde1) composes the scene, the
    bloom (512x512) and the UI (its own RGBA8 target) into the display buffer. The temporal
    upscalers' trigger (compute 9a9cf8a9, Bloodborne's) never runs in MLB: the "FSR 3.1" shown
    by the FPS counter does nothing.
  - Camera (found 2026-11-18 in frame captures, `BB_CAPTURE_TRIGGER`): the scene's vertex shaders
    (4f5afe70, 9ef15ce2, 8b9c253b, ... slot 11 or 12) bind a 128-byte constant buffer =
    view-projection (16 floats, column-major) + world matrix (16 floats). The VP is the same for
    every scene draw of a frame; test: |row x| / |row y| = 1080/1920 and |row w xyz| = 1. Depth
    is not reversed (D3D 0..1): z row = 1.01005 w row - 100.5, so near about 100, far about
    10000 game units. The pitch camera is a telephoto (P11 = 27.2, vertical FOV about 4 degrees)
    that stays still for long stretches; the intro uses P11 = 1.77. Pass #72 (2104x1184 target)
    has the same camera at 1920/2104 of the scale. The world matrix of that block is the identity
    for the stadium. The UI uses its own pixel-space matrix (P11 = -1.7778, -960/960).
    Search scripts: vpfind.py / vpstats.py (row-norm test over the capture's buffers).
  - Characters (confirmed 2026-11-18 with `BB_CAPTURE_VSBUF=1`, which notes every draw's vertex
    shader buffers in a capture): skinned in the vertex shader. Each draw gets its part of the
    bone palette in a constant buffer of 4x4 world-space bone matrices (rotation + translation,
    row 3 = position): 192 bytes = 3 bones, 1728 bytes = 27 bones (shaders a01311cb, 306b55fd,
    281decad, 7b17c70e, 44dcb85c, ...). Compute 3722f2a7 only rotates a packed vector by the
    blended bones (no translation): normals/tangents, not positions. Object motion vectors
    therefore need the previous frame's bone palette of the same draw and a vertex shader that
    transforms the vertex with both. The crowd (5703a1b5, 01da6d6b, 769bf6cf) decodes
    bit-packed vertices from a 5 MB buffer.
  - Temporal upscaler, camera motion only (2026-11-18, opt-in `BB_MLB_TEMPORAL=1`; the menu's
    anti-aliasing choice picks FSR 3/4, DLSS or XeSS). The launcher's Display page sets it up
    with **Temporal anti-aliasing** (`temporal_aa`: off/fsr3/dlss/xess, DLSS and XeSS listed only
    when their DLLs are beside the loader): `BB_MLB_TEMPORAL=1`, `BB_UPSCALER`,
    `BB_UPSCALE_PRESET=0`, and the render resolution is ignored while it is on. The scene is drawn with **4x MSAA** (color
    0x80e5dc0000 and depth D32S8, 4 samples) and a CB resolve copies the color into a one-sample
    image (0x80e4dd0000) that the bloom and the final pass read. `Rasterizer::Resolve` calls
    `TemporalUpscaler::OnResolve`, which upscales that resolved image in place;
    depth_resolve.comp copies sample 0 of the MSAA depth into an R32F image for the motion pass
    and the upscalers. Camera: `CameraMotion::OnViewProjection` takes the first 128-byte
    vertex shader constant buffer of a frame's scene draws that passes the perspective test and
    builds previous-clip-from-current-clip for camera_motion.comp's mode 2 (math checked against a
    synthetic camera: exact). Scene draws get the jitter through the viewport. Seen on a GTX 1650:
    correct and sharp with the pitch camera; ghosting on moving players in close-ups (fixed by the object
    motion vectors below); FSR 3 at 1920x1080 costs about 3-4 FPS. The scene stays at the
    native size (MSAA passes are not drawn into reduced proxies), so it is anti-aliasing only for
    now. The port's own TAA still uses Bloodborne's camera for its depth test (not adapted).
  - Object motion vectors (2026-11-18, with `BB_MLB_TEMPORAL=1` and the menu's character
    motion vectors on): the port's ObjectMotion (vertex shaders store each vertex's clip position
    and read last frame's; the fragment shader writes the difference) now runs on MLB:
    - Selection: every pipeline drawing with the scene depth (address published by the
      rasterizer in `MotionVectors::scene_depth_address`) whose targets have the depth's pitch
      (heights are padded differently: 1920x1088 color, 1920x1152 depth). Pipelines that write
      no depth get a zero write mask on the motion attachment (a full-screen pass erased a
      quarter of the vectors).
    - MSAA: the attachment takes the depth's 4 samples; `ObjectMotion::PrepareRead` copies
      sample 0 into a one-sample image (motion_resolve, depth_resolve.comp with MOTION), and
      camera_motion.comp's mode 2 takes the object vector where it is valid and its depth
      matches (bit 16 of mode).
    - Matching across frames: MLB rewrites the normals/tangents of skinned meshes into a new place
      every frame, so the vertex-stream hash never matched. `Draw::stable` hashes only the streams
      the GPU did not write; a draw with only GPU-written streams (simulated cloth) is matched
      by their layout when it was alone with it last frame. Non-indexed quads with CPU-written
      vertices and meshes whose index lists change per draw (5fb82e05) stay unmatched (camera).
    - Gate: draws are instrumented only while their bone palette changes, or always when they read
      GPU-written vertices (cloth moves with an unchanged palette). Static stadium pieces keep
      the camera's vectors.
    - Result on the GTX 1650: players, cloth and the mound get their own vectors (debug view),
      no ghosting seen in a FANCAM pan; about 3 FPS more than camera-only. The crowd (packed
      vertices) keeps the camera's vectors. `BB_MOTION_UNMATCHED_LOG=1` lists the draws with
      and without history in two frames every 1200.
  - Fixed 2026-11-18: `TextureCache::TouchImage` on an unregistered image (a 512x512 RGBA16F bloom
    target evicted under memory pressure) relinked its free LRU entry; once reused, the list closed
    on itself and `GarbageCollectImages` spun forever (the GPU thread stopped, "Kick failed to
    complete"). Seen on the 4 GB GTX 1650 in a match when the upscaler's ~100 MB pushed the texture
    cache over its budget; any memory pressure can do it. Touches of unregistered images are now
    ignored (logged).
  - The resolution patch (`BB_GAME_RES`) finds no display configuration in MLB
    ("0 stores changed").
  - Frame generation without a velocity pass: `BB_FRAME_GEN=fsr3 BB_FRAME_GEN_PS=c684cde1` takes
    the scene depth and zero motion vectors before the final pass (FSR 3.1 interpolates with its
    optical flow, UI included). Menus: 60 game FPS shown as 120 with no dropped frames. In a game
    on a GTX 1650 (4 GB) it runs out of video memory: the game fell to 5 FPS, then
    `AllocateArenaMemory: ErrorOutOfDeviceMemory`. It needs the game's video memory use lowered
    first (the texture cache reports about 4.4 GiB in use in a game on that card).
  - Frame captures now include indirect draws and dispatches; `BB_FPS_LOG=1` reports game and
    generated frames separately.
- Game speed on a 4-core laptop (i5-10300H): about 30 FPS in a game, limited by the GPU command
  processor thread (87% of a core) while the GPU is half idle.
- Video memory (fixed 2026-11-17): MLB declares 8192 layers (the T# depth field at its maximum)
  on many 2D array textures whose views reach one slice, and on a 1024x512 BC1 array whose views
  reach 134. Created as declared, that one image took 2.7 GiB (its 32-bit guest size wrapped), so
  in some match presentations (PresFanCam) the process went to 6.5-8.7 GB and a 4 GB card failed
  an allocation (`Failed allocating image with error ErrorOutOfDeviceMemory`, the game stops).
  `ImageInfo` (T#) now keeps the slices the view reaches when that is far fewer than declared,
  and beyond those at most 512 MiB of layers (`BB_ARRAY_CLAMP=0`: off; the clamped images are
  logged). Peak in a match on the GTX 1650: ~3.5 GB. `BB_VRAM_LOG=1` now also logs every image
  of 64 MiB or more.
  Fixed 2026-11-18 (0.4.3): some array T#s are incoherent (base_array 4096, last_array 42 on a
  128x128 array declaring 7761 layers): the image got 43 layers and its view was clamped
  (FindView), but the barrier for the binding still used layer 4096 and indexed past the
  subresource state table (`image.cpp GetBarriers: Assertion Failed`, the game stopped while a
  stadium loaded; in 0.4.1 and 0.4.2). Image::GetBarriers clamps the range to the image now.
- Frame rate investigation (2026-11-17, i5-10300H + GTX 1650, 16:9, pitching view, ~37-41 FPS,
  frame pacing median 33 ms at 60 Hz):
  - The game's main thread waits ~15 ms per frame on a condition variable (BB_WAIT_STATS: "main
    cond timedwait signaled", once per frame) that the kickCompletion thread broadcasts when its
    event queue delivers the GPU end-of-pipe interrupt. Main thread work ~10 ms + this wait ->
    ~25 ms frames.
  - The EOP does not wait for the GPU (half idle): it is raised once the Vulkan recording thread
    has recorded up to it. BB_EOP_STATS: submit -> GPU command thread decodes it ~4.1 ms, -> the
    draw recording thread (stage B) runs it ~5.1 ms, -> the recorder signals it ~3.3 ms (~12.5 ms
    in all, ~9000 EOPs/s). Stage B spends ~20 us per draw (~600 draws/frame), spread over
    BindTextures (~12%), GetBarriers, GetSampler, FindImage, BindBuffers, FindView (4-6% each).
  - Ruled out: guest mutexes (BB_MUTEX_STATS: ~1500 contended/s, ~3.6 ms/s in all), longer spins
    before stage B / the recorder sleep (BB_PIPE_SPIN_US, BB_RECORDER_SPIN_US), BB_ASYNC_FENCES=1
    (30 FPS: stage B then waits ~13 ms), disabling any existing pipeline optimization (all slower).
  - Guest write faults on pages holding GPU data (~250/s) are resolved on the GPU command thread
    (SendFaultResolve). They waited ~2.3 ms each (the resolve itself ~65 us) because that thread
    only took commands between packets, not in its wait loops (draw pipe drains, host copy and
    deferred fence waits: ~55% of its time). Its wait loops now serve them (vk_fault_service.h):
    in a drain they go to stage B between two draws (DrawPipe::PostPriority), in the other waits
    the GPU thread runs them itself. ~165 us each now, writers blocked 4% of the time instead of
    66%; frame rate unchanged (the frame waits for the EOP chain above). BB_FAULTS_ON_STAGE_B=0
    keeps them off stage B.
  - Diagnostics: BB_WAIT_STATS=1 (time blocked per guest thread and wait kind, who signals the
    main thread's condition), BB_EOP_STATS=1, BB_MUTEX_STATS=1, BB_FRAME_STATS (fault resolve
    wait and run time).
  - Stage B per second in a match (BB_DRAW_PHASES=1, 2026-11-18): ~23k direct draws at ~8 us
    (~180 ms), ~30k compute dispatches at ~4.5 us (~105-150 ms), ~11k in-order tasks (fences,
    writes) at ~4-7 us (~50-75 ms), ~150 indirect draws at ~220-575 us (~40-85 ms, almost all in
    BindTextures). BindBuffers ~115-128 ms (draws and dispatches), BindTextures ~60-75 ms for
    direct draws. The crowd's texture tables (1024 T#s each) are not the indirect draws' cost
    (the same with BB_IMAGE_TABLES=0); a memo of the table entries measured no gain in an
    in-match A/B and was dropped. Run-to-run and in-run variance (EOP 13-27 ms as the match
    moves on) needs in-run A/B toggles to judge changes.
- Profiling on Windows: guest threads now carry their names as thread descriptions
  (SetThreadDescription), so profilers and debuggers show MainMultiTask, KineCore_MT, ...
  `BB_FRAME_STATS=1` adds "host copy waits" by caller (WaitHostCopies/WaitDeferredSignals;
  +100000 on the line: on the draw recording thread).
