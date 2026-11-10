#!/usr/bin/env bash
# Builds out/ps4-pkg-tool.exe: the program the launcher runs to install the game from its PKG files
# (Game page -> "Install from PKG"). Run from MSYS2 CLANG64 (or Git Bash with MSYS2 in C:\msys64).
#
# ps4-pkg-tool comes from github.com/xXJSONDeruloXx/ps4-pkg-tools (shadPS4's PKG/PFS code, GPL-2.0-or-later),
# pinned at commit 45baeda. It opens fake PKGs only (the public homebrew keys, as shadPS4 does), not retail PKGs.
# Our patches (tools/pkg-tool/patches): byte progress ("Progress-bytes: <done> <total>"), 64-bit sizes and
# offsets (multi-GB PKGs), refusal of file names that would leave the output folder, and --exact (extract into
# the given folder itself, not <folder>/<title id>: an update goes into a game folder of any name). Built static against
# MSYS2's zlib and the vendored Crypto++, so the .exe needs only Windows' own DLLs.
#
# Source: $BB_PKG_TOOL_SRC, else out/pkg-tool/src (fetched with git at the pinned commit when missing).
# The source is never changed: the patches go to a copy in out/pkg-tool/patched.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
source ./msys2-env.sh
root=$PWD
commit=45baedaa29b8da42b3c6fe912800d2fa4119834b
repo=https://github.com/xXJSONDeruloXx/ps4-pkg-tools
src=${BB_PKG_TOOL_SRC:-$root/out/pkg-tool/src}

fetch() {   # the pinned commit plus the two submodules the tool builds with (Crypto++ and its CMake files)
    rm -rf "$src"; mkdir -p "$src"
    git -C "$src" init -q
    git -C "$src" fetch -q --depth 1 "$repo" "$commit"
    git -C "$src" checkout -q FETCH_HEAD
    for p in externals/cryptopp externals/cryptopp-cmake; do
        local sha url
        sha=$(git -C "$src" ls-tree HEAD "$p" | awk '{print $3}')
        url=$(git config -f "$src/.gitmodules" --get "submodule.$p.url")
        rm -rf "${src:?}/$p"; mkdir -p "$src/$p"
        git -C "$src/$p" init -q
        git -C "$src/$p" fetch -q --depth 1 "$url" "$sha"
        git -C "$src/$p" checkout -q FETCH_HEAD
        rm -rf "$src/$p/.git"
    done
}
[[ -f $src/CMakeLists.txt && -f $src/externals/cryptopp/cryptlib.h ]] || fetch

copy=$root/out/pkg-tool/patched
build=$root/out/pkg-tool/build
mkdir -p "$copy" "$build"
find "$copy" -mindepth 1 -delete   # emptied, not removed: an editor watching the folder keeps it busy on Windows
cp -r "$src/src" "$src/CMakeLists.txt" "$copy/"
mkdir -p "$copy/externals"
cp -r "$src/externals/cryptopp" "$src/externals/cryptopp-cmake" "$copy/externals/"

# zlib: MSYS2's (find_package), not the submodule. The include line is made the one the patches expect.
sed -i 's|^add_subdirectory(${CMAKE_SOURCE_DIR}/externals/zlib EXCLUDE_FROM_ALL)$|find_package(ZLIB REQUIRED)\nadd_library(zlibstatic ALIAS ZLIB::ZLIB)|' "$copy/CMakeLists.txt"
grep -q 'find_package(ZLIB REQUIRED)' "$copy/CMakeLists.txt" || { echo 'CMakeLists.txt: zlib line not found' >&2; exit 1; }
sed -i 's|^#include "zlib/zlib.h"$|#include </usr/include/zlib.h>|' "$copy/src/core/file_format/pkg.cpp"
for patch in "$root"/tools/pkg-tool/patches/*.patch; do
    patch -d "$copy" -p1 -s --binary < "$patch" 2>/dev/null || patch -d "$copy" -p1 -s < "$patch" \
        || { echo "Patch failed: $patch" >&2; exit 1; }
done
# By its full path: Crypto++ (on the include path) has a zlib.h of its own.
zlib=${MSYSTEM_PREFIX:-/clang64}/lib/libz.a
[[ -f $zlib ]] || zlib=/c/msys64/clang64/lib/libz.a
zheader=$(cygpath -m "$(dirname "$zlib")/../include/zlib.h")
sed -i "s|^#include </usr/include/zlib.h>\$|#include \"$zheader\"|" "$copy/src/core/file_format/pkg.cpp"
grep -q "$zheader" "$copy/src/core/file_format/pkg.cpp" || { echo 'pkg.cpp: zlib include not found' >&2; exit 1; }
# 64-bit seeks: io_file.cpp maps fseeko to _fseeki64 only for MSVC; MinGW's fseeko takes a 32-bit offset, so
# every file stored more than 2 GiB into the PKG was read from the wrong place (garbage, e.g. PS4/ffmpeg.prx).
sed -i 's|^#ifdef _MSC_VER$|#if defined(_MSC_VER) \|\| defined(__MINGW32__)|' "$copy/src/common/io_file.cpp"
grep -q '^#define fseeko _fseeki64$' "$copy/src/common/io_file.cpp" && grep -q '__MINGW32__' "$copy/src/common/io_file.cpp" \
    || { echo 'io_file.cpp: fseeko mapping not found' >&2; exit 1; }
# Current MinGW headers declare QUEUE_USER_APC_FLAGS themselves; ntapi.h's own copy (unused) is dropped.
sed -i '/^typedef enum _QUEUE_USER_APC_FLAGS {$/,/^} QUEUE_USER_APC_FLAGS;$/d' "$copy/src/common/ntapi.h"

# The tool prints its progress with plain "\n": into the launcher's pipe stdout would be block-buffered and
# the launcher would see nothing until the end. bb_linebuf.cpp (run before main) makes stdout line-buffered.
# bb_utf8.o: a manifest that makes UTF-8 the program's code page (PKG paths with accents, a Windows user name)
# and allows long paths. Both go into the executable as sources (the build path has spaces: no linker flags).
cat > "$copy/src/cli/bb_linebuf.cpp" <<'C'
#include <cstdio>
__attribute__((constructor)) static void bb_line_buffered_stdout() { std::setvbuf(stdout, nullptr, _IOLBF, 0); }
C
cat > "$copy/src/cli/utf8.manifest" <<'XML'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">UTF-8</activeCodePage>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
</assembly>
XML
printf '1 24 "utf8.manifest"\n' > "$copy/src/cli/utf8.rc"
(cd "$copy/src/cli" && windres utf8.rc -O coff -o bb_utf8.o)
sed -i 's|^add_executable(ps4-pkg-tool src/cli/main.cpp)$|add_executable(ps4-pkg-tool src/cli/main.cpp src/cli/bb_linebuf.cpp src/cli/bb_utf8.o)|' "$copy/CMakeLists.txt"
grep -q 'bb_utf8.o' "$copy/CMakeLists.txt" || { echo 'CMakeLists.txt: add_executable line not found' >&2; exit 1; }

cmake -S "$copy" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_PKG_QT_GUI=OFF \
    -DCRYPTOPP_BUILD_TESTING=OFF -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DZLIB_LIBRARY="$zlib" -DZLIB_INCLUDE_DIR="$(dirname "$zlib")/../include" \
    -DCMAKE_EXE_LINKER_FLAGS=-static \
    "-DCMAKE_CXX_STANDARD_LIBRARIES=-lntdll -lmincore -lbcrypt -lws2_32 -ladvapi32 -lshell32 -luser32 -lkernel32" \
    > "$build/cmake.log" 2>&1 || { tail -30 "$build/cmake.log" >&2; exit 1; }
ninja -C "$build" -j "${BB_JOBS:-6}" ps4-pkg-tool > "$build/build.log" 2>&1 || { grep -E 'error' "$build/build.log" | head -20 >&2; tail -20 "$build/build.log" >&2; exit 1; }
llvm-strip --strip-all -o out/ps4-pkg-tool.exe "$build/ps4-pkg-tool.exe"
ls -la out/ps4-pkg-tool.exe
echo "Built out/ps4-pkg-tool.exe"
