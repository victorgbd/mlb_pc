#!/usr/bin/env bash
# Linux package (experimental): dist/mlb-linux.tar.gz from a Linux build (bash build.sh).
# The launcher runs from source with the system Python 3 (tkinter, Pillow); libbbgpu.so and the
# libraries distributions may not ship (protobuf) go to bin/gpu, which run.py puts on LD_LIBRARY_PATH.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
package=mlb-linux
for file in out/bb-probe out/bb-gpu-capabilities out/gpu/libbbgpu.so; do
    [[ -f $file ]] || { echo "Missing $file: run bash build.sh first." >&2; exit 1; }
done
dest=out/stage-linux/$package
rm -rf -- out/stage-linux
mkdir -p "$dest/bin/gpu" "$dest/launcher" "$dest/scripts" "$dest/licenses"
cp out/bb-probe out/bb-gpu-capabilities "$dest/bin/"
cp out/gpu/libbbgpu.so "$dest/bin/gpu/"
strip --strip-debug "$dest/bin/bb-probe" "$dest/bin/gpu/libbbgpu.so"
# Shared libraries outside the usual runtime set: protobuf (net code) and miniupnpc when linked
# dynamically, wherever the build found them.
for lib in $(ldd out/gpu/libbbgpu.so out/bb-probe | awk '/libprotobuf|libminiupnpc|libZydis/ {print $3}' | sort -u); do
    cp -L "$lib" "$dest/bin/gpu/"
done
cp launcher/*.py launcher/mlb.png launcher/mlb.ico "$dest/launcher/"
cp scripts/prepare.py scripts/link_modules.py scripts/link_libc.py scripts/content_profile.py scripts/trophies.py "$dest/scripts/"
cp run.py LICENSE VERSION "$dest/"
cp packaging/linux/README-MLB-linux.txt "$dest/README.txt"
install -m 755 packaging/linux/mlb-launcher.sh "$dest/mlb-launcher.sh"
cp third_party/stb/LICENSE "$dest/licenses/stb-LICENSE.txt" 2>/dev/null || true
for license in gpu/third_party/httplib/LICENSE:cpp-httplib; do
    if [[ -f ${license%%:*} ]]; then cp "${license%%:*}" "$dest/licenses/${license##*:}-LICENSE.txt"; fi
done
mkdir -p dist
tar -C out/stage-linux -czf "dist/$package.tar.gz" "$package"
du -sh "$dest" "dist/$package.tar.gz"
