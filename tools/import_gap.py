#!/usr/bin/env python3
"""Which system functions a game imports that the host runtime does not provide yet.

Usage: python tools/import_gap.py path/to/aerolib.inl GAME_DIR [--exe eboot.bin] [--json out.json]

An import counts as provided when one of these holds:
  - its symbol name is in a RuntimeExport table of src/*.c ({"name",function}),
  - its NID appears as a string in src/ or gpu/ (scoped strings, shadPS4 LIB_FUNCTION),
  - it is served natively by a linked module (libc.prx, libSceFios2.prx).
The result is a static estimate: a provided function may still be a stub.
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

PORT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PORT / 'scripts'))
from link_modules import game_modules, module, module_path  # noqa: E402

NATIVE_LIBRARIES = {'libc', 'libSceLibcInternal', 'libSceFios2', 'ffmpeg'}


def provided():
    names, nids = set(), set()
    export = re.compile(r'\{\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*,')
    nid = re.compile(r'"([A-Za-z0-9+\-]{11})(?:#[^"]*)?"')
    for path in (PORT / 'src').glob('*.c'):
        text = path.read_text(encoding='utf-8', errors='replace')
        names.update(export.findall(text))
        nids.update(nid.findall(text))
    for path in (PORT / 'gpu').rglob('*.cpp'):
        if 'third_party' in path.parts:
            continue
        nids.update(nid.findall(path.read_text(encoding='utf-8', errors='replace')))
    return names, nids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('aerolib', type=Path)
    parser.add_argument('game', type=Path)
    parser.add_argument('--exe', default='eboot.bin')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    aerolib = dict(re.findall(r'STUB\("([^"]+)",\s*([^)\s]+)\)',
                              args.aerolib.read_text(encoding='utf-8', errors='replace')))
    names, nids = provided()
    imports = {}
    paths = [args.game / args.exe] + [module_path(args.game, m) for m in game_modules(args.game)]
    for path in paths:
        for s in module(path)['symbols']:
            if not s['identity'] or s['section']:
                continue
            n, (library, _), _ = s['identity']
            if library in NATIVE_LIBRARIES:
                continue
            imports[(n, library)] = aerolib.get(n, '?')
    missing = collections.defaultdict(list)
    have = 0
    for (n, library), symbol in sorted(imports.items(), key=lambda x: (x[0][1], x[1])):
        if symbol in names or n in nids:
            have += 1
        else:
            missing[library].append(f'{symbol} ({n})')
    total = len(imports)
    print(f'{total} host imports: {have} provided, {total - have} missing')
    for library, entries in sorted(missing.items(), key=lambda x: -len(x[1])):
        print(f'\n{library}: {len(entries)}')
        for e in entries:
            print(f'  {e}')
    if args.json:
        args.json.write_text(json.dumps(dict(total=total, provided=have, missing=missing), indent=2) + '\n',
                             encoding='utf-8')


if __name__ == '__main__':
    main()
