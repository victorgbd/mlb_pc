# SPDX-License-Identifier: GPL-2.0-or-later
"""Installing the game from its PKG files (Game page -> Install from PKG): what a PKG holds (its header
and param.sfo, read here) and unpacking it with ps4-pkg-tool (bin/ps4-pkg-tool.exe, built by
tools/pkg-tool/build_pkg_tool.sh). The tool opens fake PKGs only, as shadPS4 does."""
import os
from pathlib import Path
import re
import struct
import subprocess

PKG_MAGIC = 0x7F434E54          # "\x7FCNT"
SFO_ENTRY_ID = 0x1000           # param.sfo in the PKG's entry table (stored unencrypted)
DELTA_PATCH = 0x41000000        # content flags of a delta patch (only the changed bytes, not whole files)
# param.sfo CATEGORY -> what the PKG is.
KINDS = {'gd': 'game', 'gp': 'update', 'ac': 'dlc'}
PROGRESS = re.compile(r'^Progress-bytes: (\d+) (\d+)')
FAILURE = ('Failed to open PKG:', 'Extraction failed:', 'Error:')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def tool_path(port_dir):
    """ps4-pkg-tool.exe of the package (bin/) or of a source tree (out/), or None."""
    configured = os.environ.get('BB_PKG_TOOL')
    candidates = ([Path(configured)] if configured else []) + [Path(port_dir) / 'bin' / 'ps4-pkg-tool.exe',
                                                               Path(port_dir) / 'out' / 'ps4-pkg-tool.exe']
    return next((path for path in candidates if path.is_file()), None)


def read_pkg(path):
    """{'path', 'title_id', 'content_id', 'kind', 'version', 'title', 'size', 'delta'} of a PS4 PKG.
    Raises ValueError when the file is not one."""
    path = Path(path)
    with open(path, 'rb') as f:
        head = f.read(0x80)
        if len(head) < 0x80 or struct.unpack_from('>I', head, 0)[0] != PKG_MAGIC:
            raise ValueError('not a PS4 PKG file')
        count, = struct.unpack_from('>I', head, 0x10)
        table, = struct.unpack_from('>I', head, 0x18)
        content_id = head[0x40:0x64].split(b'\0')[0].decode('ascii', 'replace')
        flags, = struct.unpack_from('>I', head, 0x78)
        values = {}
        if count <= 0x10000:
            f.seek(table)
            entries = f.read(count * 32)
            for n in range(len(entries) // 32):
                entry_id, _name, _f1, _f2, offset, size = struct.unpack_from('>6I', entries, n * 32)
                if entry_id == SFO_ENTRY_ID and 0 < size <= 1 << 20:
                    f.seek(offset)
                    try:
                        from prepare import sfo
                        values = sfo(f.read(size))
                    except (ImportError, ValueError, struct.error, UnicodeDecodeError):
                        values = {}
                    break
    title_id = str(values.get('TITLE_ID') or content_id[7:16])
    category = str(values.get('CATEGORY') or '')
    kind = KINDS.get(category, 'other' if category else 'game')
    title = str(values.get('TITLE') or '').replace('™', '').replace('®', '').strip()
    return {'path': path, 'title_id': title_id, 'content_id': content_id, 'kind': kind,
            'version': str(values.get('APP_VER') or values.get('VERSION') or '?'), 'title': title,
            'size': path.stat().st_size, 'delta': kind == 'update' and flags & DELTA_PATCH == DELTA_PATCH}


def version_key(text):
    return tuple(int(n) for n in re.findall(r'\d+', text or ''))


def extract(tool, pkg, target, on_progress, on_line, started=None):
    """Unpacks one PKG into `target` itself (--exact). on_progress(done, total) with file bytes,
    on_line(text) with the tool's other lines; started(process) right after it starts, so the caller
    can stop it. Returns (exit code, the last failure line or '')."""
    process = subprocess.Popen([str(tool), '--exact', str(pkg), str(target)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    if started:
        started(process)
    failure = ''
    for raw in iter(process.stdout.readline, b''):
        line = raw.decode('utf-8', errors='replace').rstrip()
        match = PROGRESS.match(line)
        if match:
            on_progress(int(match.group(1)), int(match.group(2)))
            continue
        if line.startswith(FAILURE):
            failure = line
        if line:
            on_line(line)
    return process.wait(), failure
