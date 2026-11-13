MLB The Show 24 for Windows (PC port)
=====================================

A native port: the original PS4 game code runs directly on Windows. No game files are
included. You need your own decrypted dump of MLB The Show 24 (CUSA43942): the folder with
eboot.bin, PS4\, sce_module\ and sce_sys\.

Disclaimer
- Unofficial fan project: not affiliated with, endorsed by or sponsored by Sony Interactive
  Entertainment, San Diego Studio, Major League Baseball, the MLB Players Association or their
  partners. "MLB The Show" and "MLB" are trademarks of their owners, named here only to say
  which game this port works with.
- No game files are included. Use your own legally obtained copy of the game; this package does
  not provide game files, decryption keys or tools that bypass copy protection. The PKG
  installer opens fake PKGs only (made from your own copy), never retail PKGs.
- Provided as is, without any warranty (see LICENSE).
- Proyecto no oficial, sin relacion con Sony, San Diego Studio, MLB ni la MLBPA. No incluye
  archivos del juego: necesitas tu propia copia legal.

Requirements
- Windows 10 (1903 or later) or Windows 11, 64-bit.
- A Vulkan 1.3 graphics card with a current driver (tested on a GeForce GTX 1650, 4 GB).
- About 8 GB of free memory (RAM + page file).
  Nothing else: Python and the libraries are inside this folder.

Starting
- "MLB The Show 24.exe" opens the launcher. On the Game page choose your game folder, then
  press PLAY (or double-click the cover on the Play page).
- Only have the game as a PKG file? Game page -> "Install from PKG...": choose the game PKG
  (and its update PKG, if you have one; select both), then the folder to install into. The
  launcher unpacks it into a CUSA43942 folder there and sets it as the game folder. Needs
  about as much free space as the PKG files take. An update PKG chosen alone is unpacked over
  the game folder already set. DLC PKGs are not supported.
- The first start prepares the game (a minute) and compiles shaders: the first menus and the
  first game stutter for a while. Later starts use the shader cache.
- "Play MLB The Show 24.exe" starts the game straight away with the settings saved in the
  launcher, without opening it (for shortcuts and Steam). "MLB The Show 24.exe --play" does
  the same.
- Play page -> "Add to Steam" adds the game to your Steam library, with pictures made from your
  game folder (or an image you choose). "Desktop shortcut" puts it on the desktop.
- Your own banner or cover: click the banner, or right-click the cover, on the Play page.

Options
- Display page: fullscreen, presentation (Mailbox, FIFO), 30 or 60 FPS, window size, FSR 1
  upscaling and sharpening for windows or screens larger than 1920 x 1080, FPS counter,
  speakers (stereo, 5.1, 7.1).
- Controls page: keyboard and controller work together, all remappable.
  Keyboard defaults: Space Cross, C Circle, F Square, E Triangle, Q/G L1/R1, Z/X L2/R2,
  Left Shift/V L3/R3, WASD left stick, IJKL right stick, arrows d-pad, Enter Options,
  Tab touchpad. Insert opens the in-game settings menu. Escape asks whether to quit the game
  (Quit / Cancel; Escape again cancels). Bindings are saved in input.ini.
- Trophies: the game's trophy list is read from sce_sys\trophy\trophy00.trp in the game folder.
  A trophy the game awards shows as a notice in the game and on the launcher's Trophies page.
- Advanced page -> Graphics fixes: the crowd textures and the see-through nets are fixes of this
  port; leave them on.

Data
- Saves and shader caches: user\ next to the launcher (the launcher can pick another folder).
- Launcher options: %APPDATA%\mlb-launcher. Generated files (prepared game image): out\.
- The log of the last run: user\last_run.log (the launcher's Log page shows it live).

Known limits
- Offline only: online modes (Diamond Dynasty online, Ranked, friends) need PSN servers.
- No temporal upscalers or frame generation yet (the game's renderer passes are not identified).
- 3D audio objects are not rendered; the regular audio channels play.
