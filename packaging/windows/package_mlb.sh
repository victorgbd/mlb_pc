#!/usr/bin/env bash
# Builds dist/mlb-windows/ (and dist/mlb-windows.zip): "MLB The Show 24.exe" (the launcher, frozen with
# PyInstaller so players need no Python), "Play MLB The Show 24.exe", bb-probe.exe with the MSYS2
# CLANG64 DLLs it needs, the preparation scripts and run.py. No game files. Run from an MSYS2 CLANG64
# shell after `bash build.sh`. Freezing uses a Windows Python 3.10+ (python.org; WINPYTHON overrides)
# and a private venv in out/pyenv with PyInstaller, Pillow and tkinterdnd2.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
source ./msys2-env.sh
[[ -f out/bb-probe.exe && -f out/bb-gpu-capabilities.exe && -f out/bb-play.exe ]] || { echo 'Build first: bash build.sh' >&2; exit 1; }
# The launcher's PKG installer (Game page -> Install from PKG).
[[ -f out/ps4-pkg-tool.exe ]] || bash tools/pkg-tool/build_pkg_tool.sh

name='MLB The Show 24'
package=mlb-windows

# A Windows Python (not MSYS2's) for PyInstaller.
python=${WINPYTHON:-}
if [[ -z $python ]]; then
    for candidate in /c/Python3*/python.exe "${LOCALAPPDATA:-/c/Users/$USER/AppData/Local}"/Programs/Python/Python3*/python.exe; do
        [[ -x $candidate ]] && python=$candidate
    done
fi
[[ -n $python ]] || { echo 'Need a Windows Python 3 (python.org) or WINPYTHON=path\to\python.exe' >&2; exit 1; }
# Windows Python needs USERPROFILE (some MSYS2 shells start without it).
export USERPROFILE=${USERPROFILE:-$(cygpath -w "/c/Users/$(id -un)")}
if [[ ! -x out/pyenv/Scripts/python.exe ]]; then
    "$python" -m venv out/pyenv
fi
out/pyenv/Scripts/python.exe -m pip install -q --disable-pip-version-check pyinstaller pillow tkinterdnd2
# The scripts run inside the launcher executable (--script): the standard modules they import come along.
hidden=()
for module in argparse base64 collections hashlib json re shutil struct tempfile zlib ctypes.wintypes \
              prepare trophies mlb_art mlb_i18n i18n_base i18n_extra lang_table lang_table_more widgets \
              steam_shortcuts pkg_install PIL.ImageTk; do
    hidden+=(--hidden-import "$module")
done
out/pyenv/Scripts/python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed \
    --name "$name" --icon "$(cygpath -w "$PWD/launcher/mlb.ico")" --distpath out/pyi-dist \
    --workpath out/pyi-work --specpath out/pyi-work --paths "$(cygpath -w "$PWD/scripts")" \
    --paths "$(cygpath -w "$PWD/launcher")" \
    --add-data "$(cygpath -w "$PWD/launcher/mlb.png");." \
    "${hidden[@]}" \
    "$(cygpath -w "$PWD/launcher/mlb_launcher.py")"

# The package is assembled in a fresh staging folder and zipped from there; dist/mlb-windows (a playable
# copy that may hold saves and settings) is only refreshed afterwards.
dest=out/stage/$package
rm -rf -- out/stage
mkdir -p "$dest/bin" "$dest/launcher" "$dest/scripts"
cp -r "out/pyi-dist/$name/." "$dest/"
llvm-strip -o "$dest/Play $name.exe" out/bb-play.exe
cp launcher/mlb.ico launcher/mlb.png "$dest/launcher/"
# The executables without debug information (out/ keeps the symbols for crash reports).
for exe in bb-probe.exe bb-gpu-capabilities.exe; do
    llvm-strip --strip-debug -o "$dest/bin/$exe" "out/$exe"
done
# Every DLL the executables load from the CLANG64 tree (SDL3, FFmpeg, Vulkan loader, ...).
ldd "$dest/bin/bb-probe.exe" "$dest/bin/bb-gpu-capabilities.exe" |
    awk '/\/clang64\/bin\// {print $3}' | sort -u | while read -r dll; do
        cp -u "$dll" "$dest/bin/"
    done
# Static: needs only Windows' own DLLs.
cp out/ps4-pkg-tool.exe "$dest/bin/"
# Only what run.py runs to prepare the game.
cp scripts/prepare.py scripts/link_modules.py scripts/link_libc.py scripts/content_profile.py scripts/trophies.py "$dest/scripts/"
cp run.py LICENSE "$dest/"
cp packaging/windows/README-MLB.txt "$dest/README.txt"
mkdir -p "$dest/licenses"
cp packaging/windows/licenses/*.txt "$dest/licenses/" 2>/dev/null || true
cp tools/pkg-tool/NOTICE.txt "$dest/licenses/ps4-pkg-tool-NOTICE.txt"
for license in out/pyenv/Lib/site-packages/tkinterdnd2-*.dist-info/licenses/LICENSE; do
    [[ -f $license ]] && cp "$license" "$dest/licenses/tkinterdnd2-LICENSE.txt"
done
cp third_party/stb/LICENSE "$dest/licenses/stb-LICENSE.txt" 2>/dev/null || true
for license in gpu/third_party/wepoll/LICENSE:wepoll gpu/third_party/httplib/LICENSE:cpp-httplib; do
    if [[ -f ${license%%:*} ]]; then cp "${license%%:*}" "$dest/licenses/${license##*:}-LICENSE.txt"; fi
done
for pkg in protobuf abseil-cpp openssl miniupnpc zlib; do
    license=/clang64/share/licenses/$pkg/LICENSE
    if [[ -f $license ]]; then cp "$license" "$dest/licenses/$pkg-LICENSE.txt"; fi
done
find "$dest" -name __pycache__ -prune -exec rm -r {} +
mkdir -p dist
rm -f "dist/$package.zip"
# Not Compress-Archive, which writes backslashes that Linux tools keep in the file names.
(cd out/stage && ../pyenv/Scripts/python.exe -c "
import pathlib, zipfile
with zipfile.ZipFile('../../dist/$package.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(pathlib.Path('$package').rglob('*')): z.write(p)")

# Refresh dist/mlb-windows, keeping what players create there (saves, settings), and only while
# nothing runs from it: deleting a running launcher's files breaks it.
play=dist/$package
running=$(powershell -NoProfile -Command \
    "@(Get-Process | Where-Object { \$_.Path -like '$(cygpath -w "$PWD/$play")\\*' }).Count" | tr -d '\r')
if [[ ${running:-0} != 0 ]]; then
    echo "dist/$package is in use ($running processes): not refreshed; the zip is ready." >&2
else
    mkdir -p "$play"
    find "$play" -mindepth 1 -maxdepth 1 ! -name user ! -name out ! -name art ! -name bbport.ini \
        ! -name input.ini ! -name last_run.log -exec rm -rf {} +
    cp -r "$dest/." "$play/"
fi
du -sh "$dest" "dist/$package.zip"
