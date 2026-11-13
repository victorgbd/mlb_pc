#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launcher for the Windows port of MLB The Show 24 (Tkinter).

Chooses the game folder, the display, controls and console options, and runs run.py with them as
environment variables. Launcher options live in %APPDATA%/mlb-launcher/settings.json.

Frozen with PyInstaller the same "MLB The Show 24.exe" also runs the game without the window
(`--play`), run.py (`--run`) and the preparation scripts (`--script`), so a packaged port needs no
Python installation. Shared pieces: widgets.py (cards, toggles, buttons, sliders), i18n_base.py
and i18n_extra.py (translations of the common texts; mlb_i18n.py adds MLB's).
"""
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile

FROZEN = getattr(sys, 'frozen', False)
PORT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PORT_DIR / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import trophies  # noqa: E402  (scripts/trophies.py: the game's trophy list)
import mlb_i18n  # noqa: E402
import pkg_install  # noqa: E402

DATA_DIR = Path(os.environ.get('BB_DATA_DIR', PORT_DIR))
CONFIG_DIR = Path(os.environ.get('APPDATA', Path.home())) / 'mlb-launcher'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
NAME = 'MLB The Show 24'
EXE_NAME = 'MLB The Show 24.exe'
AUTHOR = 'victorgbd'
# This build; GitHub release tags are v<VERSION> (their digits are compared).
VERSION = '0.1.0'
# "owner/repository" on GitHub whose releases hold the packaged zip. Empty: no update check.
RELEASES_REPO = ''
RELEASES_API = f'https://api.github.com/repos/{RELEASES_REPO}/releases/latest' if RELEASES_REPO else ''
RELEASES_PAGE = f'https://github.com/{RELEASES_REPO}/releases/latest' if RELEASES_REPO else ''
UPDATE_DIR = Path(tempfile.gettempdir()) / 'mlb-update'
# Never copied over an installation by an update (the package does not hold them either).
USER_FILES = ('user', 'out', 'art', 'bbport.ini', 'input.ini', 'last_run.log')
MAX_LOG_LINES = 6000
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

GAME_KEY = 'mlb'
GAME_TITLE = 'MLB The Show 24'
GAME_ID = 'CUSA43942'
GAME_EXE = 'eboot.bin'
# Console language (system parameter 1), as the PS4 numbers them.
LANGUAGES = [('1', 'English (United States)'), ('18', 'English (United Kingdom)'), ('2', 'French'),
             ('22', 'French (Canada)'), ('4', 'German'), ('5', 'Italian'), ('3', 'Spanish'),
             ('20', 'Spanish (Latin America)'), ('7', 'Portuguese'), ('17', 'Portuguese (Brazil)'),
             ('6', 'Dutch'), ('8', 'Russian'), ('16', 'Polish'), ('19', 'Turkish'), ('21', 'Arabic'),
             ('0', 'Japanese'), ('9', 'Korean'), ('11', 'Chinese (Simplified)'), ('10', 'Chinese (Traditional)'),
             ('12', 'Finnish'), ('13', 'Swedish'), ('14', 'Danish'), ('15', 'Norwegian')]
PRESENT_MODES = [('Mailbox', 'Mailbox (low latency, no tearing)'), ('Fifo', 'FIFO (VSync)'),
                 ('FifoRelaxed', 'FIFO Relaxed'), ('Immediate', 'Immediate (tearing)')]
# The display rate the game runs at (BB_VBLANK_HZ). Higher rates are not tested with MLB yet.
FRAME_RATES = [('30', '30 FPS'), ('60', '60 FPS (as on PS4)')]
# The window (or the screen in fullscreen) shows the game's 1920x1080 picture at its size: FSR 1 (EASU)
# or bilinear when it is enlarged, then RCAS sharpening (BB_RCAS_ATTENUATION, lower is sharper).
WINDOW_SIZES = [('1280x720', '1280 × 720'), ('1600x900', '1600 × 900'),
                ('1920x1080', '1920 × 1080'), ('2560x1440', '2560 × 1440'),
                ('3840x2160', '3840 × 2160 (4K)')]
SPEAKERS = [('auto', 'Automatic (the Windows speaker setup)'), ('2', 'Stereo'), ('6', '5.1 surround'), ('8', '7.1 surround')]
SCALERS = [('fsr1', 'FSR 1 (AMD FidelityFX, sharp)'), ('bilinear', 'Smooth (bilinear)')]
SHARPENING = [('off', 'Off'), ('1000', 'Low'), ('500', 'Medium'), ('250', 'High'), ('0', 'Maximum')]
# Controls (input.ini, read by the game: src/runtime_pad.c). Actions with their PS4 names, the
# default keys ("A|B": two bindings) and the default controller buttons (SDL names).
BUTTON_ACTIONS = [
    ('cross', 'Cross ✕', 'Space', 'a'), ('circle', 'Circle ○', 'C', 'b'),
    ('square', 'Square □', 'F', 'x'), ('triangle', 'Triangle △', 'E', 'y'),
    ('l1', 'L1', 'Q', 'leftshoulder'), ('r1', 'R1', 'G', 'rightshoulder'),
    ('l2', 'L2', 'Z', 'lefttrigger'), ('r2', 'R2', 'X', 'righttrigger'),
    ('l3', 'L3', 'Left Shift', 'leftstick'), ('r3', 'R3', 'V', 'rightstick'),
    ('options', 'Options', 'Return', 'start'), ('touchpad', 'Touchpad', 'Tab', 'back'),
    ('up', 'D-pad up', 'Up', 'dpup'), ('down', 'D-pad down', 'Down', 'dpdown'),
    ('left', 'D-pad left', 'Left', 'dpleft'), ('right', 'D-pad right', 'Right', 'dpright'),
]
STICK_ACTIONS = [
    ('ls_up', 'Left stick up', 'W'), ('ls_down', 'Left stick down', 'S'),
    ('ls_left', 'Left stick left', 'A'), ('ls_right', 'Left stick right', 'D'),
    ('rs_up', 'Right stick up', 'I'), ('rs_down', 'Right stick down', 'K'),
    ('rs_left', 'Right stick left', 'J'), ('rs_right', 'Right stick right', 'L'),
]
DEFAULT_KEYS = {a: k for a, _t, k, _p in BUTTON_ACTIONS} | {a: k for a, _t, k in STICK_ACTIONS}
DEFAULT_PAD = {a: p for a, _t, _k, p in BUTTON_ACTIONS}
PAD_BUTTONS = [('a', 'A / Cross'), ('b', 'B / Circle'), ('x', 'X / Square'), ('y', 'Y / Triangle'),
               ('leftshoulder', 'LB / L1'), ('rightshoulder', 'RB / R1'),
               ('lefttrigger', 'LT / L2'), ('righttrigger', 'RT / R2'),
               ('leftstick', 'Left stick click / L3'), ('rightstick', 'Right stick click / R3'),
               ('start', 'Start / Options'), ('back', 'Back / Share'), ('touchpad', 'Touchpad click'),
               ('dpup', 'D-pad up'), ('dpdown', 'D-pad down'), ('dpleft', 'D-pad left'),
               ('dpright', 'D-pad right'), ('misc1', 'Share / Mic (misc)'), ('paddle1', 'Paddle 1'),
               ('paddle2', 'Paddle 2'), ('paddle3', 'Paddle 3'), ('paddle4', 'Paddle 4'),
               ('none', 'Not assigned')]
# Tk key names → SDL key names (letters and digits map to themselves, upper case).
TK_KEYS = {'space': 'Space', 'Return': 'Return', 'Escape': 'Escape', 'Tab': 'Tab', 'BackSpace': 'Backspace',
           'Shift_L': 'Left Shift', 'Shift_R': 'Right Shift', 'Control_L': 'Left Ctrl',
           'Control_R': 'Right Ctrl', 'Alt_R': 'Right Alt', 'Caps_Lock': 'CapsLock', 'Up': 'Up',
           'Down': 'Down', 'Left': 'Left', 'Right': 'Right', 'Delete': 'Delete', 'Home': 'Home',
           'End': 'End', 'Prior': 'PageUp', 'Next': 'PageDown', 'minus': '-', 'equal': '=',
           'bracketleft': '[', 'bracketright': ']', 'semicolon': ';', 'apostrophe': "'", 'comma': ',',
           'period': '.', 'slash': '/', 'backslash': '\\', 'grave': '`', 'KP_Enter': 'Keypad Enter',
           'KP_Add': 'Keypad +', 'KP_Subtract': 'Keypad -', 'KP_Multiply': 'Keypad *',
           'KP_Divide': 'Keypad /', 'KP_Decimal': 'Keypad .', 'KP_Delete': 'Keypad .',
           'KP_Insert': 'Keypad 0', 'KP_End': 'Keypad 1', 'KP_Down': 'Keypad 2', 'KP_Next': 'Keypad 3',
           'KP_Left': 'Keypad 4', 'KP_Begin': 'Keypad 5', 'KP_Right': 'Keypad 6', 'KP_Home': 'Keypad 7',
           'KP_Up': 'Keypad 8', 'KP_Prior': 'Keypad 9'}
# Tk on Windows names numpad keys like the main ones ("8", "plus"), only the keycode tells them apart.
WIN_NUMPAD = {**{0x60 + n: f'Keypad {n}' for n in range(10)},
              0x6A: 'Keypad *', 0x6B: 'Keypad +', 0x6D: 'Keypad -', 0x6E: 'Keypad .', 0x6F: 'Keypad /'}
# Insert opens the in-game settings menu; Escape asks whether to quit the game.
RESERVED_KEYS = {'Insert': 'Insert opens the in-game settings menu.',
                 'Escape': 'Escape asks whether to quit the game.'}
# Graphics fixes of the port that are on by default; turning one off is only for comparing.
GRAPHICS_FIXES = [
    ('fix_crowd', 'BB_IMAGE_TABLES', 'Crowd textures',
     'Each fan picks its texture from a table. Off: the crowd is black.'),
    ('fix_nets', 'BB_ALPHA_TO_MASK', 'See-through nets',
     'The protective nets let the field and the fans show through. Off: they are gray walls.'),
]

APP_DEFAULTS = {'game_dir': '', 'user_dir': '', 'language': '1', 'ui_language': '', 'player_name': '',
                'fullscreen': False, 'present_mode': 'Mailbox', 'frame_rate': '60', 'show_fps': False,
                'window_size': '1920x1080', 'speakers': 'auto', 'scaler': 'fsr1', 'sharpening': '500',
                'extra_env': '', 'close_on_play': False, 'pad_invert_y': False,
                'art_banner': '', 'art_cover': '', 'steam_art': '', 'art_crops': {},
                'shortcut_name': GAME_TITLE, 'shortcut_icon': '', 'pad_vibration': True,
                'pad_vibration_strength': '100', 'key_bindings': {}, 'pad_bindings': {}, 'check_updates': True,
                **{key: True for key, _v, _t, _h in GRAPHICS_FIXES}}


UI_LANGUAGE = 'en'


def _(text):
    """Translate launcher text, keeping English as the fallback."""
    return mlb_i18n.translate(UI_LANGUAGE, text)


def _run_problem_priority(line):
    if line.startswith(('STOP:', 'GPU crash:')):
        return 3
    if line.startswith(('ERROR', 'Fault:', 'No eboot.bin', 'Set BB_GAME_DIR', 'bb-probe.exe not found')):
        return 2
    if 'failed' in line and not line.startswith('GPU ['):
        return 1
    return 0


def select_run_problem(current, output):
    """Keep the most useful diagnostic emitted before the game exits."""
    line = output.strip()
    line_priority = _run_problem_priority(line)
    return line if line_priority and line_priority >= _run_problem_priority(current) else current


# ---------------------------------------------------------------------------------------------
# Command line roles of the frozen executable.

def attach_stdio():
    """A windowed executable starts without sys.stdout; inherited pipes or a console still exist."""
    import msvcrt
    for name, std in (('stdout', -11), ('stderr', -12)):
        if getattr(sys, name) is not None:
            continue
        stream = None
        handle = ctypes.windll.kernel32.GetStdHandle(std)
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                stream = open(msvcrt.open_osfhandle(handle, os.O_WRONLY), 'w', encoding='utf-8',
                              errors='replace', buffering=1)
            except OSError:
                stream = None
        setattr(sys, name, stream or open(os.devnull, 'w'))


def run_role(argv):
    """--run [args]: run.py; --script <file> [args]: a preparation script. Returns an exit code."""
    attach_stdio()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, errors='replace', **({} if stream.isatty() else {'encoding': 'utf-8'}))
        except (AttributeError, ValueError):
            pass
    if argv[0] == '--script':
        path, sys.argv = argv[1], argv[1:]
    else:
        path = str(PORT_DIR / 'run.py')
        sys.argv = [path, *argv[1:]]
    try:
        runpy.run_path(path, run_name='__main__')
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    return 0


def run_command():
    return [sys.executable, '--run'] if FROZEN else [sys.executable, str(PORT_DIR / 'run.py')]


# ---------------------------------------------------------------------------------------------
# Settings.

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def ini_path():
    return Path(os.environ.get('BB_CONFIG', DATA_DIR / 'bbport.ini'))


def set_ini(key, value):
    """bbport.ini (read by the game; the in-game menu edits it): one key rewritten in place."""
    path = ini_path()
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = []
    out, found = [], False
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#') and line.split('=', 1)[0].strip() == key:
            out.append(f'{key}={value}')
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f'{key}={value}')
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def game_info(folder):
    """{'title', 'id', 'version'} of a game folder with eboot.bin, or None."""
    folder = Path(folder or '.')
    if not str(folder).strip() or not (folder / GAME_EXE).is_file():
        return None
    info = {'title': GAME_TITLE, 'id': folder.name, 'version': '?'}
    try:
        from prepare import sfo
        values = sfo((folder / 'sce_sys/param.sfo').read_bytes())
        info.update(title=values.get('TITLE', info['title']).replace('™', '').replace('®', '').strip(),
                    id=values.get('TITLE_ID', info['id']), version=values.get('APP_VER', '?'))
    except (OSError, ValueError, ImportError):
        pass
    return info


def game_folder(s):
    return str(s.get('game_dir') or '').strip()


def game_available(s):
    folder = game_folder(s)
    return bool(folder) and (Path(folder) / GAME_EXE).is_file()


def vibration_strength(s):
    """Saved vibration percentage, clamped at the launcher/runtime boundary."""
    try:
        return min(100, max(0, int(float(s.get('pad_vibration_strength', 100)))))
    except (TypeError, ValueError):
        return 100


def controller_probe_path():
    """The packaged or development loader, which also provides the lightweight controller probe."""
    configured = os.environ.get('BB_PROBE')
    candidates = ([Path(configured)] if configured else []) + [PORT_DIR / 'bin' / 'bb-probe.exe',
                                                               PORT_DIR / 'out' / 'bb-probe.exe']
    return next((path for path in candidates if path.is_file()), None)


def controller_probe_environment(probe):
    """Development builds find SDL beside MSYS2; packages already keep it beside bb-probe."""
    env = dict(os.environ)
    if not (probe.parent / 'SDL3.dll').is_file():
        clang64 = Path(env.get('MSYS2_ROOT', r'C:\msys64')) / 'clang64' / 'bin'
        if clang64.is_dir():
            env['PATH'] = f'{clang64}{os.pathsep}{env.get("PATH", "")}'
    return env


def parse_controller_probe(text):
    """Parse bb-probe's deliberately small key=value controller response."""
    values = {}
    for line in text.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return {'connected': values.get('connected') == '1',
            'name': values.get('name', ''),
            'rumble': values.get('rumble') == '1',
            'tested': values.get('tested') == '1'}


def side_buttons():
    """(Mouse 4, Mouse 5) held right now: the system's XBUTTON1 / XBUTTON2 (virtual keys 5 and 6)."""
    try:
        return tuple(bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) for vk in (5, 6))
    except (AttributeError, OSError):
        return (False, False)


def display_refresh_rate():
    """Refresh rate of the main display in Hz, or None."""
    try:
        class DevMode(ctypes.Structure):
            _fields_ = [('name', ctypes.c_wchar * 32), ('spec', ctypes.c_ushort), ('driver', ctypes.c_ushort),
                        ('size', ctypes.c_ushort), ('extra', ctypes.c_ushort), ('fields', ctypes.c_ulong),
                        ('position', ctypes.c_long * 2), ('orientation', ctypes.c_ulong),
                        ('fixed', ctypes.c_ulong), ('color', ctypes.c_short * 5), ('form', ctypes.c_wchar * 32),
                        ('logpixels', ctypes.c_ushort), ('bits', ctypes.c_ulong), ('width', ctypes.c_ulong),
                        ('height', ctypes.c_ulong), ('flags', ctypes.c_ulong), ('frequency', ctypes.c_ulong),
                        ('rest', ctypes.c_ulong * 8)]
        mode = DevMode()
        mode.size = ctypes.sizeof(DevMode)
        if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(mode)) and mode.frequency > 1:
            return int(mode.frequency)
    except (AttributeError, OSError):
        pass
    return None


def input_ini_path():
    return Path(os.environ.get('BB_INPUT_CONFIG', DATA_DIR / 'input.ini'))


def write_input_ini(s):
    """input.ini: the keyboard and controller bindings the game reads at start (no mouse camera)."""
    keys = {**DEFAULT_KEYS, **(s.get('key_bindings') or {})}
    pad = {**DEFAULT_PAD, **(s.get('pad_bindings') or {})}
    lines = ['# MLB The Show 24 controls (written by the launcher, Controls page)',
             'mouse_look=0', 'mouse_sensitivity=1.00', 'mouse_invert_y=0',
             f"pad_invert_y={1 if s.get('pad_invert_y') else 0}",
             f"pad_vibration={1 if s.get('pad_vibration', True) else 0}",
             f"pad_vibration_strength={vibration_strength(s)}"]
    lines += [f'key.{action}={keys.get(action, "")}' for action in DEFAULT_KEYS]
    lines += [f'pad.{action}={pad.get(action, "none")}' for action in DEFAULT_PAD]
    path = input_ini_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def game_environment(s):
    env = dict(os.environ)
    env['BB_INPUT_CONFIG'] = str(write_input_ini(s))
    env['BB_GAME_DIR'] = game_folder(s)
    env['BB_EXE'] = GAME_KEY
    if s['user_dir']:
        env['BB_USER_DIR'] = s['user_dir']
    env['BB_LANGUAGE'] = s['language']
    if str(s['player_name']).strip():
        env['BB_USER_NAME'] = str(s['player_name']).strip()
    env['BB_FULLSCREEN'] = '1' if s['fullscreen'] else '0'
    env['BB_PRESENT_MODE'] = s['present_mode']
    env['BB_VBLANK_HZ'] = str(s.get('frame_rate') or '60')
    env['BB_WINDOW_SIZE'] = str(s.get('window_size') or '1920x1080')
    if str(s.get('speakers') or 'auto') != 'auto':
        env['BB_SPEAKERS'] = str(s['speakers'])
    env['BB_FSR1'] = '1' if s.get('scaler', 'fsr1') == 'fsr1' else '0'
    sharpening = str(s.get('sharpening') or 'off')
    env['BB_RCAS'] = '0' if sharpening == 'off' else '1'
    if sharpening != 'off':
        env['BB_RCAS_ATTENUATION'] = sharpening
    for key, variable, _title, _hint in GRAPHICS_FIXES:
        if not s.get(key, True):
            env[variable] = '0'
    for item in str(s['extra_env']).split():
        if '=' in item:
            key, value = item.split('=', 1)
            env[key] = value
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'  # open() and the pipes must not fall back to the ANSI code page
    return env


# ---------------------------------------------------------------------------------------------
# Surfaces: a night game under the lights: deep navy, white text, red accent.

BG, PANEL, SURFACE, CARD, LINE = '#060a13', '#09101d', '#0c1424', '#121c31', '#22314f'
TEXT, MUTED = '#eef2fa', '#8e9ab4'
ACCENT, ACCENT_HI, ACCENT_DIM = '#ef4550', '#ff6b74', '#9a3440'
CARD_HI = '#18243f'
COLORS = {'surface': SURFACE, 'card': CARD, 'card_hi': CARD_HI, 'line': LINE, 'text': TEXT, 'muted': MUTED,
          'gold': ACCENT, 'gold_hi': ACCENT_HI, 'track': '#26324d', 'knob': '#9aa6bf', 'knob_on': '#ffffff',
          'button': '#1a2540', 'button_hi': '#24324f'}
# Sidebar icons (Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets on Windows 10).
NAV_ICONS = {'play': '\ue768', 'game': '\ue7fc', 'display': '\ue7f4', 'controls': '\ue765',
             'trophies': '\ue734', 'advanced': '\ue713', 'log': '\ue8a5'}
# Trophy grades as the console colours them.
TROPHY_COLORS = {'P': '#9fd0f5', 'G': '#e2b04a', 'S': '#cfd3da', 'B': '#cf8548'}


class Launcher:
    def __init__(self, root, tk, ttk, filedialog, messagebox):
        global UI_LANGUAGE
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.root = root
        self.app = {**APP_DEFAULTS, **load_json(CONFIG_FILE, {})}
        UI_LANGUAGE = mlb_i18n.language_code(self.app.get('ui_language', ''))
        self.vars = {}
        self.process = self.job = None
        self.entered, self.last_problem, self.stopping = False, '', False
        self.output = queue.Queue()
        self.ui_calls = queue.Queue()
        self.banner_key = self.banner_image = None
        self.cover_key = self.cover_image = None
        self.info = None
        self.update_box = None
        self.pkg_job = None  # a PKG install in progress: {'process', 'cancelled', 'progress', 'text'}
        root.title(f'{NAME} — {_("PC port")} · {_("by {}").format(AUTHOR)}')
        root.configure(bg=BG)
        self.dpi = root.winfo_fpixels('1i') / 96.0
        # The window fits the screen's work area (the taskbar stays visible).
        width, height = self.px(1180), self.px(820)
        try:
            from ctypes import wintypes
            area = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
            work_w, work_h = area.right - area.left, area.bottom - area.top
            if work_w > 0 and work_h > 0:
                width, height = min(width, work_w - 40), min(height, work_h - 60)
        except (AttributeError, OSError):
            pass
        root.geometry(f'{width}x{height}+{max(0, (root.winfo_screenwidth() - width) // 2)}+10')
        root.minsize(min(self.px(980), width), min(self.px(600), height))
        self.set_icon()
        self.load_fonts()
        self.style()
        self.build()
        self.show('play')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.drain_output)
        root.after(150, self.detect_controller)
        if RELEASES_API and self.app.get('check_updates', True):
            threading.Thread(target=self.check_update, daemon=True).start()

    def px(self, size):
        return int(size * self.dpi)

    def set_icon(self):
        for path in (PORT_DIR / 'launcher' / 'mlb.ico', Path(__file__).resolve().parent / 'mlb.ico'):
            try:
                self.root.iconbitmap(default=str(path))
                return
            except self.tk.TclError:
                continue

    def load_fonts(self):
        """Bahnschrift (Windows' sporty DIN face) for headings; Segoe UI for body text."""
        import tkinter.font as tkfont
        families = set(tkfont.families(self.root))
        if 'Bahnschrift SemiBold' in families:
            self.brand_family, weight = 'Bahnschrift SemiBold', 'normal'
        elif 'Bahnschrift' in families:
            self.brand_family, weight = 'Bahnschrift', 'bold'
        else:
            self.brand_family, weight = 'Segoe UI Black', 'normal'
        self.font_brand_xl = (self.brand_family, 26, weight)
        self.font_brand_lg = (self.brand_family, 20, weight)
        self.font_brand_md = (self.brand_family, 13, weight)
        self.font_brand_sm = (self.brand_family, 10, weight)
        self.font_ui = ('Segoe UI', 10)
        self.font_ui_sm = ('Segoe UI', 9)
        self.font_ui_md = ('Segoe UI Semibold', 10)
        self.font_button = ('Segoe UI Semibold', 10)
        self.font_play = ('Segoe UI Semibold', 13)
        self.font_title = ('Segoe UI Semibold', 22)
        self.font_card = ('Segoe UI Semibold', 9)
        icons = next((f for f in ('Segoe Fluent Icons', 'Segoe MDL2 Assets') if f in families), None)
        self.font_icon = (icons, 13) if icons else None
        self.font_icon_sm = (icons, 11) if icons else None

    # ---- look --------------------------------------------------------------------------------
    def check_images(self):
        """16 px (DPI-scaled) check box images: the clam theme draws a cross."""
        tk = self.tk
        n = max(14, int(16 * self.dpi))
        images = []
        for checked in (False, True):
            image = tk.PhotoImage(width=n + self.px(8), height=n)
            image.put(ACCENT if checked else '#3e4c6a', to=(0, 0, n, n))
            image.put('#1a0a0c' if checked else CARD, to=(1, 1, n - 1, n - 1))
            if checked:
                x0, y0, x1, y1, x2, y2 = .22 * n, .52 * n, .42 * n, .72 * n, .78 * n, .28 * n
                width = max(1, round(n / 9))
                for t in range(40):
                    f = t / 39
                    for x, y in ((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f), (x1 + (x2 - x1) * f, y1 + (y2 - y1) * f)):
                        image.put(ACCENT, to=(int(x), int(y), int(x) + width, int(y) + width))
            images.append(image)
        self.check_off, self.check_on = images

    def style(self):
        s = self.ttk.Style(self.root)
        s.theme_use('clam')
        self.check_images()
        s.element_create('Nd.indicator', 'image', self.check_off, ('selected', self.check_on), sticky='')
        s.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            ('Nd.indicator', {'side': 'left', 'sticky': ''}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
        base = self.font_ui
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT_DIM)
        self.root.option_add('*TCombobox*Listbox.font', base)
        s.configure('.', background=SURFACE, foreground=TEXT, fieldbackground=CARD, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=CARD, focuscolor=ACCENT, font=base)
        s.configure('TFrame', background=SURFACE)
        s.configure('Shell.TFrame', background=SURFACE)
        s.configure('Card.TFrame', background=CARD)
        s.configure('TLabel', background=CARD, foreground=TEXT)
        s.configure('Shell.TLabel', background=SURFACE, foreground=TEXT)
        s.configure('Muted.TLabel', background=CARD, foreground=MUTED, font=('Segoe UI', 9))
        s.configure('ShellMuted.TLabel', background=SURFACE, foreground=MUTED, font=('Segoe UI', 9))
        s.configure('Section.TLabel', background=SURFACE, foreground=ACCENT, font=(self.brand_family, 10))
        s.configure('TCheckbutton', background=SURFACE, foreground=TEXT, padding=(0, 3))
        s.map('TCheckbutton', background=[('active', SURFACE)], foreground=[('disabled', MUTED)])
        s.configure('TCombobox', arrowcolor=ACCENT, foreground=TEXT, padding=(8, 5), bordercolor=CARD_HI,
                    lightcolor=CARD_HI, darkcolor=CARD_HI, background=CARD_HI, arrowsize=14)
        s.map('TCombobox', fieldbackground=[('readonly', CARD_HI)], foreground=[('readonly', TEXT)],
              selectbackground=[('readonly', CARD_HI)], selectforeground=[('readonly', TEXT)],
              bordercolor=[('focus', ACCENT_DIM)], lightcolor=[('focus', ACCENT_DIM)],
              background=[('active', '#22304f')])
        s.configure('TEntry', foreground=TEXT, insertcolor=TEXT, padding=(8, 5), fieldbackground=CARD_HI,
                    bordercolor=CARD_HI, lightcolor=CARD_HI, darkcolor=CARD_HI)
        s.map('TEntry', bordercolor=[('focus', ACCENT_DIM)], lightcolor=[('focus', ACCENT_DIM)])
        s.configure('Vertical.TScrollbar', background='#22304f', arrowcolor=MUTED, troughcolor=SURFACE,
                    bordercolor=SURFACE, lightcolor='#22304f', darkcolor='#22304f', gripcount=0, arrowsize=12)
        s.map('Vertical.TScrollbar', background=[('active', '#2e3f66')])
        s.configure('Accent.Horizontal.TProgressbar', background=ACCENT, troughcolor=CARD_HI, bordercolor=CARD_HI,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=self.px(8))

    # ---- widget helpers ----------------------------------------------------------------------
    def var(self, key):
        if key not in self.vars:
            value = self.app.get(key, APP_DEFAULTS[key])
            v = self.tk.BooleanVar(value=bool(value)) if isinstance(APP_DEFAULTS[key], bool) \
                else self.tk.StringVar(value=str(value))
            self.vars[key] = v
        return self.vars[key]

    def choice(self, parent, key, options, width=36, on_change=None):
        var = self.var(key)
        values = [v for v, _t in options]
        box = self.ttk.Combobox(parent, values=[_(t) for _v, t in options], state='readonly', width=width)
        if var.get() not in values:
            var.set(values[0])

        def show(*_args):
            box.current(values.index(var.get()) if var.get() in values else 0)
        show()

        def selected(_event):
            var.set(values[box.current()])
            if on_change:
                on_change()
        box.bind('<<ComboboxSelected>>', selected)
        var.trace_add('write', show)
        return box

    def next_row(self, parent):
        return parent.grid_size()[1]

    def target(self, parent):
        """Where a page's rows go: its current card (one is opened when rows come before a section)."""
        if parent in self.page_cards:
            if self.page_cards[parent] is None:
                self.new_card(parent, None)
            self.page_cards[parent].refit()
            return self.page_cards[parent].inner
        return parent

    def new_card(self, page, title):
        from widgets import Card
        card = Card(self.tk, page, COLORS, self.px, title, self.font_card)
        card.canvas.grid(row=self.next_row(page), column=0, columnspan=2, sticky='ew', pady=(0, self.px(14)))
        self.page_cards[page] = card
        return card

    def toggle(self, parent, variable, background=CARD):
        from widgets import Toggle
        return Toggle(self.tk, parent, variable, COLORS, self.px, background)

    def pill(self, parent, text, command, kind='secondary', background=CARD, padding=(16, 8), icon=None):
        from widgets import PillButton
        return PillButton(self.tk, parent, text, command, COLORS, self.px,
                          self.font_play if kind == 'primary' and icon else self.font_button, kind,
                          background, padding, icon, self.font_icon_sm)

    def row(self, parent, title, widget, hint=None):
        parent = self.target(parent)
        r = self.next_row(parent)
        self.ttk.Label(parent, text=title).grid(row=r, column=0, sticky='nw', padx=(0, 24), pady=(9, 0))
        widget.grid(row=r, column=1, sticky='w', pady=(5, 0), in_=parent)
        widget.lift()
        if hint:
            self.ttk.Label(parent, text=hint, style='Muted.TLabel', wraplength=self.px(560), justify='left').grid(
                row=r + 1, column=1, sticky='w', pady=(3, 4))
        return widget

    def check(self, parent, key, title, hint=None):
        parent = self.target(parent)
        r = self.next_row(parent)
        line = self.tk.Frame(parent, bg=CARD)
        line.grid(row=r, column=0, columnspan=2, sticky='w', pady=(7, 0))
        self.toggle(line, self.var(key)).pack(side='left')
        label = self.tk.Label(line, text=title, bg=CARD, fg=TEXT, font=self.font_ui, cursor='hand2')
        label.pack(side='left', padx=(self.px(12), 0))
        label.bind('<Button-1>', lambda _e: self.var(key).set(not self.var(key).get()))
        if hint:
            self.ttk.Label(parent, text=hint, style='Muted.TLabel', wraplength=self.px(640), justify='left').grid(
                row=r + 1, column=0, columnspan=2, sticky='w', padx=(self.px(52), 0), pady=(2, 2))

    def note(self, parent, text, top=12):
        parent = self.target(parent)
        self.ttk.Label(parent, text=text, style='Muted.TLabel', wraplength=self.px(680), justify='left').grid(
            row=self.next_row(parent), column=0, columnspan=2, sticky='w', pady=(top, 0))

    def section(self, parent, title, top=18):
        if parent in self.page_cards:
            return self.new_card(parent, title).inner
        self.ttk.Label(parent, text=title, style='Section.TLabel').grid(
            row=self.next_row(parent), column=0, columnspan=2, sticky='w', pady=(top, 2))
        return parent

    def buttons_row(self, parent):
        card = self.target(parent)
        holder = self.tk.Frame(card, bg=CARD)
        holder.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(12, 0))
        return holder

    def folder(self, parent, key, title, prompt, hint=None, on_change=None):
        var = self.var(key)
        holder = self.tk.Frame(self.target(parent), bg=CARD)
        self.ttk.Entry(holder, textvariable=var, width=54).pack(side='left')

        def browse():
            chosen = self.filedialog.askdirectory(title=prompt, initialdir=var.get() or str(PORT_DIR))
            if chosen:
                var.set(str(Path(chosen)))
        self.pill(holder, _('Browse…'), browse, padding=(14, 6)).pack(side='left', padx=(8, 0))
        self.pill(holder, _('Open'), lambda: self.open_path(var.get(), key), kind='ghost',
                  padding=(12, 6)).pack(side='left', padx=(4, 0))
        if on_change:
            var.trace_add('write', lambda *_a: on_change())
        return self.row(parent, title, holder, hint)

    def image_path(self, title, current=''):
        """Choose and validate a local image."""
        chosen = self.filedialog.askopenfilename(
            title=title, initialdir=str(Path(current).parent) if current else str(PORT_DIR),
            filetypes=[(_('Image files'), '*.png *.jpg *.jpeg *.webp *.ico'), (_('All files'), '*.*')])
        if not chosen:
            return ''
        try:
            from PIL import Image
            with Image.open(chosen) as image:
                image.verify()
        except (OSError, ValueError) as error:
            self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
            return ''
        return str(Path(chosen))

    def enable_image_drop(self, widget, callback):
        """Accept Explorer file drops when tkinterdnd2 is bundled; clicking always remains available."""
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', lambda event: callback(self.root.tk.splitlist(event.data)[0]))
        except (ImportError, self.tk.TclError, AttributeError, IndexError):
            pass

    def edit_art(self, key, title, size, crop_key, initial_path=None, initial_crop=None):
        """Preview, position and save one local image without copying it into the port folder."""
        from PIL import Image, ImageTk
        import mlb_art

        window = self.tk.Toplevel(self.root)
        window.title(title)
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)
        path = self.tk.StringVar(value=self.app.get(key, '') if initial_path is None else initial_path)
        saved = (self.app.setdefault('art_crops', {}).get(crop_key, {})
                 if initial_crop is None else initial_crop)
        result = {'value': None}
        zoom = self.tk.DoubleVar(value=float(saved.get('zoom', 1.0)))
        focus_x = self.tk.DoubleVar(value=float(saved.get('x', 0.5)))
        focus_y = self.tk.DoubleVar(value=float(saved.get('y', 0.5)))
        preview_w = self.px(560)
        preview_h = max(self.px(180), round(preview_w * size[1] / size[0]))
        if preview_h > self.px(390):
            preview_h = self.px(390)
            preview_w = round(preview_h * size[0] / size[1])
        shell = self.tk.Frame(window, bg=PANEL)
        shell.pack(padx=self.px(24), pady=self.px(22))
        self.tk.Label(shell, text=title, bg=PANEL, fg=TEXT, font=self.font_title).pack(anchor='w')
        self.tk.Label(shell, text=_('Drag an image here or click Choose image.'), bg=PANEL, fg=MUTED,
                      font=self.font_ui).pack(anchor='w', pady=(2, self.px(12)))
        canvas = self.tk.Canvas(shell, width=preview_w, height=preview_h, bg=BG, highlightthickness=2,
                                highlightbackground=ACCENT_DIM, bd=0, cursor='hand2')
        canvas.pack()
        preview = {'photo': None}

        def redraw(*_args):
            canvas.delete('all')
            try:
                with Image.open(path.get()) as source:
                    crop = {'zoom': zoom.get(), 'x': focus_x.get(), 'y': focus_y.get()}
                    image = mlb_art.crop_image(source.convert('RGB'), (preview_w, preview_h), crop)
                preview['photo'] = ImageTk.PhotoImage(image)
                canvas.create_image(0, 0, image=preview['photo'], anchor='nw')
            except (OSError, ValueError, self.tk.TclError):
                preview['photo'] = None
                canvas.create_text(preview_w // 2, preview_h // 2, text=_('Choose image'), fill=MUTED,
                                   font=self.font_ui_md)
            canvas.create_rectangle(1, 1, preview_w - 2, preview_h - 2, outline=ACCENT, width=2)

        def choose(candidate=''):
            chosen = candidate or self.image_path(title, path.get())
            if chosen:
                try:
                    with Image.open(chosen) as image:
                        image.verify()
                except (OSError, ValueError) as error:
                    self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
                    return
                path.set(str(Path(chosen)))
                redraw()

        controls = self.tk.Frame(shell, bg=PANEL)
        controls.pack(fill='x', pady=(self.px(14), 0))
        for row, (label, variable, start, end, step) in enumerate((
                (_('Zoom'), zoom, 1.0, 3.0, 0.01), (_('Horizontal position'), focus_x, 0.0, 1.0, 0.01),
                (_('Vertical position'), focus_y, 0.0, 1.0, 0.01))):
            self.tk.Label(controls, text=label, bg=PANEL, fg=TEXT, font=self.font_ui,
                          width=20, anchor='w').grid(row=row, column=0, sticky='w', pady=2)
            scale = self.tk.Scale(controls, variable=variable, from_=start, to=end, resolution=step,
                                  orient='horizontal', showvalue=False, length=self.px(380), bg=PANEL,
                                  fg=TEXT, troughcolor=CARD_HI, activebackground=ACCENT, highlightthickness=0,
                                  command=redraw)
            scale.grid(row=row, column=1, sticky='ew', pady=2)
        buttons = self.tk.Frame(shell, bg=PANEL)
        buttons.pack(fill='x', pady=(self.px(16), 0))
        self.pill(buttons, _('Choose image'), choose, background=PANEL).pack(side='left')

        def restore():
            path.set('')
            zoom.set(1.0)
            focus_x.set(0.5)
            focus_y.set(0.5)
            redraw()

        self.pill(buttons, _('Restore'), restore, kind='ghost', background=PANEL).pack(side='left', padx=(6, 0))
        self.pill(buttons, _('Cancel'), window.destroy, kind='ghost', background=PANEL).pack(side='right')

        def save():
            result['value'] = (path.get(), {'zoom': zoom.get(), 'x': focus_x.get(), 'y': focus_y.get()})
            window.destroy()

        self.pill(buttons, _('Save changes'), save, kind='primary', background=PANEL).pack(side='right', padx=(0, 6))
        canvas.bind('<Button-1>', lambda _event: choose())
        self.enable_image_drop(canvas, choose)
        for variable in (path, zoom, focus_x, focus_y):
            variable.trace_add('write', redraw)
        redraw()
        window.bind('<Escape>', lambda _event: window.destroy())
        window.wait_window()
        return result['value']

    def edit_launcher_art(self, key, title, size, crop_key, initial_path=None):
        result = self.edit_art(key, title, size, crop_key, initial_path=initial_path)
        if not result:
            return
        self.app[key], self.app.setdefault('art_crops', {})[crop_key] = result
        self.change_art()

    def quick_art(self, key, path):
        """Open a dropped image in the same editor used by click selection."""
        try:
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
        except (OSError, ValueError) as error:
            self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
            return
        if key == 'art_banner':
            self.edit_launcher_art('art_banner', _('Banner'), (1600, 450), 'banner', str(Path(path)))
        else:
            self.edit_launcher_art('art_cover', _('Cover'), (512, 512), 'cover', str(Path(path)))

    def change_art(self):
        self.collect()
        self.banner_key = self.cover_key = None
        self.refresh()

    def page(self, name, title, subtitle):
        tk = self.tk
        outer = tk.Frame(self.content, bg=SURFACE)
        shell = tk.Frame(outer, bg=SURFACE)
        shell.pack(fill='both', expand=True, padx=self.px(18), pady=(self.px(14), 0))
        head = tk.Frame(shell, bg=SURFACE)
        head.pack(fill='x', padx=self.px(26), pady=(self.px(18), self.px(14)))
        tk.Label(head, text=title, bg=SURFACE, fg=TEXT, font=self.font_title).pack(anchor='w')
        tk.Label(head, text=subtitle, bg=SURFACE, fg=MUTED, font=self.font_ui).pack(anchor='w', pady=(2, 0))
        body = tk.Frame(shell, bg=SURFACE)
        body.pack(fill='both', expand=True)
        canvas = tk.Canvas(body, bg=SURFACE, highlightthickness=0, bd=0)
        bar = self.ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)
        inner = self.ttk.Frame(canvas, style='Shell.TFrame', padding=(26, 0, 26, 12))
        inner.columnconfigure(1, weight=1)
        self.page_cards[inner] = None
        window = canvas.create_window(0, 0, window=inner, anchor='nw')

        def fit(_event=None):
            canvas.itemconfigure(window, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), inner.winfo_reqheight()))
            if inner.winfo_reqheight() > canvas.winfo_height():
                bar.pack(side='right', fill='y')
            else:
                bar.pack_forget()
                canvas.yview_moveto(0)
        inner.bind('<Configure>', fit)
        canvas.bind('<Configure>', fit)

        self.scrollers[name] = (canvas, bar)
        self.pages[name] = outer
        return inner

    def on_wheel(self, event):
        """The mouse wheel scrolls the page under the pointer; while a key is being bound it is the binding."""
        if getattr(self, 'capturing', None):
            return self.captured_wheel(event)
        canvas = self.scroller_under_pointer()
        if canvas:
            # One notch (120) moves a tenth of the page; finer mice send smaller steps.
            self.scroll_page(canvas, -event.delta / 120 * canvas.winfo_height() / 10)
        return 'break'

    def on_touchpad(self, event):
        """Tk 9 reports touchpads and smooth-scrolling mice as TouchpadScroll: dx in the high 16 bits, dy in the low."""
        low = event.delta & 0xffff
        dy = low - 0x10000 if low >= 0x8000 else low
        if getattr(self, 'capturing', None):
            if dy:
                self.set_key(*self.capturing, 'Wheel Up' if dy > 0 else 'Wheel Down')
            return 'break'
        canvas = self.scroller_under_pointer()
        if canvas and dy:
            self.scroll_page(canvas, -dy * self.dpi)
        return 'break'

    def scroller_under_pointer(self):
        """The current page's canvas when the pointer is over it and it has something to scroll."""
        scroller = self.scrollers.get(self.current_page)
        if not scroller:
            return None
        canvas, bar = scroller
        x, y = self.root.winfo_pointerxy()
        inside = (canvas.winfo_rootx() <= x < canvas.winfo_rootx() + canvas.winfo_width() and
                  canvas.winfo_rooty() <= y < canvas.winfo_rooty() + canvas.winfo_height())
        return canvas if inside and bar.winfo_ismapped() else None

    def scroll_page(self, canvas, pixels):
        """Scrolls by pixels, carrying the fractions over so slow, fine scrolling still moves."""
        self.wheel_rest = getattr(self, 'wheel_rest', 0.0) + pixels
        step = int(self.wheel_rest)
        self.wheel_rest -= step
        if step:
            try:
                canvas.yview_scroll(step, 'pixels')
            except self.tk.TclError:  # Tk 8.6 canvases only know units (a tenth of the page)
                canvas.yview_moveto(canvas.yview()[0] + step / max(1, float(canvas.cget('scrollregion').split()[3])))

    # ---- layout ------------------------------------------------------------------------------
    def build(self):
        tk = self.tk
        side_w = self.px(248)
        side = tk.Frame(self.root, bg=BG, width=side_w)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        self.emblem_image = None
        try:
            from PIL import ImageTk
            import mlb_art
            mark = mlb_art.emblem(self.px(64))
            self.emblem_image = ImageTk.PhotoImage(mark) if mark else None
        except Exception:
            self.emblem_image = None
        if self.emblem_image:
            tk.Label(side, image=self.emblem_image, bg=BG, bd=0).pack(anchor='w', padx=16, pady=(22, 0))
        tk.Label(side, text='MLB THE SHOW', bg=BG, fg=TEXT, font=self.font_brand_lg, anchor='w').pack(
            anchor='w', padx=18, pady=(10 if self.emblem_image else 28, 0))
        tk.Label(side, text=_('PC PORT'), bg=BG, fg=ACCENT, font=self.font_brand_sm, anchor='w').pack(
            anchor='w', padx=18, pady=(4, 0))
        tk.Label(side, text=_('by {}').format(AUTHOR), bg=BG, fg=TEXT, font=self.font_ui_md,
                 anchor='w').pack(anchor='w', padx=18, pady=(10, 0))
        tk.Label(side, text=_('version {}').format(VERSION), bg=BG, fg='#5d6a86', font=self.font_ui_sm,
                 anchor='w').pack(anchor='w', padx=18, pady=(1, 0))
        tk.Frame(side, bg=LINE, height=1).pack(fill='x', padx=18, pady=(16, 14))
        self.nav, self.nav_pills, self.nav_icons, self.nav_marks, self.current_page = {}, {}, {}, {}, None
        for name, title in (('play', _('Play')), ('game', _('Game')), ('display', _('Display')),
                            ('controls', _('Controls')), ('trophies', _('Trophies')), ('advanced', _('Advanced')),
                            ('log', _('Log'))):
            row = tk.Frame(side, bg=BG, cursor='hand2')
            row.pack(fill='x', padx=(0, 12), pady=2)
            mark = tk.Frame(row, bg=BG, width=self.px(3))
            mark.pack(side='left', fill='y')
            pill = tk.Frame(row, bg=BG, cursor='hand2')
            pill.pack(side='left', fill='x', expand=True, padx=(self.px(9), 0))
            widgets = [row, pill]
            if self.font_icon:
                icon = tk.Label(pill, text=NAV_ICONS[name], bg=BG, fg=MUTED, font=self.font_icon,
                                padx=self.px(12), pady=self.px(9), cursor='hand2')
                icon.pack(side='left')
                self.nav_icons[name] = icon
                widgets.append(icon)
            item = tk.Label(pill, text=title, bg=BG, fg=TEXT, anchor='w', font=self.font_ui_md,
                            padx=self.px(2 if self.font_icon else 14), pady=self.px(9), cursor='hand2')
            item.pack(side='left', fill='x', expand=True)
            widgets.append(item)
            for widget in widgets:
                widget.bind('<Button-1>', lambda _e, n=name: self.show(n))
                widget.bind('<Enter>', lambda _e, n=name: self._nav_hover(n, True))
                widget.bind('<Leave>', lambda _e, n=name: self._nav_hover(n, False))
            self.nav[name], self.nav_pills[name], self.nav_marks[name] = item, pill, mark
        tk.Frame(side, bg=BG).pack(fill='both', expand=True)
        self.side = side
        # The bottom of the sidebar (the update notice goes just above it).
        self.side_note = tk.Frame(side, bg=BG, height=1)
        self.side_note.pack(anchor='w', padx=18, pady=(0, 18))

        tk.Frame(self.root, bg=LINE, width=1).pack(side='left', fill='y')
        right = tk.Frame(self.root, bg=SURFACE)
        right.pack(side='left', fill='both', expand=True)
        bar = tk.Frame(right, bg=PANEL, height=self.px(78))
        bar.pack(fill='x', side='bottom')
        bar.pack_propagate(False)
        tk.Frame(bar, bg=LINE, height=1).pack(fill='x')
        dock = tk.Frame(bar, bg=PANEL)
        dock.pack(fill='both', expand=True)
        self.status = tk.Label(dock, text='', bg=PANEL, fg=MUTED, font=('Segoe UI', 10), anchor='w', justify='left')
        self.status.pack(side='left', padx=24, pady=14)
        self.play_button = self.pill(dock, _('Play'), self.play, kind='primary', background=PANEL,
                                     padding=(30, 9), icon='\ue768' if self.font_icon else None)
        self.play_button.pack(side='right', padx=(10, 22), pady=12)
        self.stop_button = self.pill(dock, _('Stop'), self.stop, background=PANEL, padding=(24, 9),
                                     icon='\ue71a' if self.font_icon else None)
        self.stop_button.configure(state='disabled')
        self.stop_button.pack(side='right', pady=12)
        self.content = tk.Frame(right, bg=SURFACE)
        self.content.pack(fill='both', expand=True)

        self.pages, self.scrollers, self.page_cards = {}, {}, {}
        self.root.bind_all('<MouseWheel>', self.on_wheel)
        self.root.bind_class('TCombobox', '<MouseWheel>', self.on_wheel)
        try:  # Tk 9 only
            self.root.bind_all('<TouchpadScroll>', self.on_touchpad)
            self.root.bind_class('TCombobox', '<TouchpadScroll>', self.on_touchpad)
        except self.tk.TclError:
            pass
        self.build_play()
        self.build_game()
        self.build_display()
        self.build_controls()
        self.build_trophies()
        self.build_advanced()
        self.build_log()
        self.refresh()

    def _nav_style(self, name, active=False, hover=False):
        if active:
            bg, fg, icon = '#111a2e', TEXT, ACCENT
        elif hover:
            bg, fg, icon = '#0b1222', TEXT, TEXT
        else:
            bg, fg, icon = BG, MUTED, MUTED
        self.nav_pills[name].configure(bg=bg)
        self.nav[name].configure(bg=bg, fg=fg)
        if name in self.nav_icons:
            self.nav_icons[name].configure(bg=bg, fg=icon)
        self.nav_marks[name].configure(bg=ACCENT if active else BG)

    def _nav_hover(self, name, inside):
        if name == self.current_page:
            return
        self._nav_style(name, hover=inside)

    def show(self, name):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill='both', expand=True)
        self.current_page = name
        for n in self.nav:
            self._nav_style(n, active=(n == name))
        if name == 'play':
            self.refresh()
        elif name == 'trophies':
            self.trophy_info = None  # the game folder or the language may have changed
            self.trophy_state = None
            self.render_trophies()

    # ---- play page ---------------------------------------------------------------------------
    def build_play(self):
        tk = self.tk
        page = tk.Frame(self.content, bg=SURFACE)
        self.pages['play'] = page
        self.hero = tk.Canvas(page, height=self.px(300), bg=SURFACE, highlightthickness=0, bd=0, cursor='hand2')
        self.hero.pack(fill='x')
        self.hero.bind('<Configure>', self._sync_hero_height)
        self.hero.bind('<Button-1>', lambda _e: self.edit_launcher_art('art_banner', _('Banner'), (1600, 450), 'banner'))
        self.enable_image_drop(self.hero, lambda path: self.quick_art('art_banner', path))

        shell = tk.Frame(page, bg=SURFACE)
        shell.pack(fill='both', expand=True, padx=self.px(36), pady=(self.px(4), self.px(10)))
        side = self.px(196)
        self.cover = tk.Canvas(shell, width=side + 2 * max(10, side // 14), height=side + 2 * max(10, side // 14),
                               bg=SURFACE, highlightthickness=0, bd=0, cursor='hand2')
        self.cover.pack(side='left', anchor='n', pady=(self.px(0), 0))
        self.cover.bind('<Double-Button-1>', lambda _e: self.play())
        self.cover.bind('<Button-3>', lambda _e: self.edit_launcher_art('art_cover', _('Cover'), (512, 512), 'cover'))
        self.enable_image_drop(self.cover, lambda path: self.quick_art('art_cover', path))
        self.cover_side = side

        info = tk.Frame(shell, bg=SURFACE)
        info.pack(side='left', fill='both', expand=True, padx=(self.px(22), 0), pady=(self.px(14), 0))
        self.title_label = tk.Label(info, text=GAME_TITLE.upper(), bg=SURFACE, fg=TEXT, font=self.font_brand_xl,
                                    anchor='w')
        self.title_label.pack(anchor='w')
        self.id_label = tk.Label(info, text='', bg=SURFACE, fg=ACCENT, font=self.font_brand_sm, anchor='w')
        self.id_label.pack(anchor='w', pady=(2, 0))
        self.ready = tk.Label(info, text='', bg=SURFACE, fg=MUTED, font=self.font_ui, wraplength=self.px(560),
                              justify='left', anchor='w')
        self.ready.pack(anchor='w', pady=(self.px(10), 0))
        row = tk.Frame(info, bg=SURFACE)
        row.pack(anchor='w', pady=(self.px(16), 0))
        self.toggle(row, self.var('fullscreen'), SURFACE).pack(side='left')
        fullscreen = tk.Label(row, text=_('Fullscreen'), bg=SURFACE, fg=TEXT, font=self.font_ui, cursor='hand2')
        fullscreen.pack(side='left', padx=(self.px(10), 0))
        fullscreen.bind('<Button-1>', lambda _e: self.var('fullscreen').set(not self.var('fullscreen').get()))
        actions = tk.Frame(info, bg=SURFACE)
        actions.pack(anchor='w', pady=(self.px(16), 0))
        self.pill(actions, _('Add to Steam'), self.add_to_steam, background=SURFACE, padding=(14, 6)).pack(side='left')
        self.pill(actions, _('Desktop shortcut'), self.shortcut, background=SURFACE, padding=(14, 6)).pack(
            side='left', padx=(self.px(8), 0))
        tk.Label(info, text=_('Double-click the cover to play. Click the banner, or right-click the cover, to use '
                              'your own image.'), bg=SURFACE, fg='#5d6a86', font=self.font_ui_sm,
                 anchor='w', justify='left', wraplength=self.px(560)).pack(anchor='w', pady=(self.px(14), 0))

    def _sync_hero_height(self, _event=None):
        c = self.hero
        w = max(c.winfo_width(), 400)
        h = max(self.px(170), min(self.px(300), int(w * 9 / 32)))
        if int(c['height']) != h:
            c.configure(height=h)
        self.draw_hero()

    def page_rgb(self):
        return tuple(int(SURFACE[i:i + 2], 16) for i in (1, 3, 5))

    def draw_hero(self):
        c = self.hero
        c.delete('all')
        w, h = max(c.winfo_width(), 400), int(c['height'])
        game_dir = game_folder(self.current())
        key = (w, h, game_dir, self.app.get('art_banner', ''), str(self.app.get('art_crops', {}).get('banner')))
        if self.banner_key != key:
            self.banner_key = key
            self.banner_image = None
            try:
                from PIL import ImageTk
                import mlb_art
                scene = mlb_art.banner(game_dir, w, h, self.page_rgb(), self.app.get('art_banner', ''),
                                       self.app.get('art_crops', {}).get('banner'))
                self.banner_image = ImageTk.PhotoImage(scene)
            except Exception:
                self.banner_image = None
        if self.banner_image:
            c.create_image(0, 0, image=self.banner_image, anchor='nw')
        c.create_rectangle(0, h - 2, w, h, fill=ACCENT, outline='')
        c.create_text(w - self.px(24), self.px(18), text=_('Change image'), anchor='ne', fill='#c9d2e6',
                      font=self.font_ui_sm)

    def draw_cover(self):
        c = self.cover
        game_dir = game_folder(self.current())
        key = (self.cover_side, game_dir, self.app.get('art_cover', ''), str(self.app.get('art_crops', {}).get('cover')))
        if self.cover_key != key:
            self.cover_key = key
            self.cover_image = None
            try:
                from PIL import ImageTk
                import mlb_art
                tile = mlb_art.cover_tile(game_dir, self.cover_side, self.page_rgb(), self.app.get('art_cover', ''),
                                          self.app.get('art_crops', {}).get('cover'),
                                          ring=tuple(int(ACCENT[i:i + 2], 16) for i in (1, 3, 5)))
                self.cover_image = ImageTk.PhotoImage(tile)
            except Exception:
                self.cover_image = None
        c.delete('all')
        if self.cover_image:
            c.create_image(0, 0, image=self.cover_image, anchor='nw')

    # ---- other pages -------------------------------------------------------------------------
    def build_game(self):
        f = self.page('game', _('Game'), _('Your own decrypted dump of MLB The Show 24 ({}).').format(GAME_ID))
        self.folder(f, 'game_dir', _('Game folder'), _('The folder with eboot.bin'),
                    _('The folder with eboot.bin, PS4\\ and sce_sys\\.'), on_change=self.refresh)
        self.folder(f, 'user_dir', _('Saves folder'), _('Where saves and the shader cache are kept'),
                    _('Empty: the user folder next to the launcher.'))
        card = self.section(f, _('Install from PKG'))
        self.note(f, _('Unpack the game from its PKG file (a fake PKG made from your own copy), and its update '
                       'PKG if you have one. The game folder is set when it finishes.'), top=2)
        holder = self.buttons_row(f)
        self.pkg_button = self.pill(holder, _('Install from PKG…'), self.install_pkg)
        self.pkg_button.pack(side='left')
        self.pkg_cancel_button = self.pill(holder, _('Cancel'), self.cancel_pkg, kind='ghost')
        self.pkg_cancel_button.pack(side='left', padx=(8, 0))
        self.pkg_bar = self.ttk.Progressbar(card, style='Accent.Horizontal.TProgressbar', orient='horizontal',
                                            mode='determinate', maximum=1000, length=self.px(520))
        self.pkg_bar.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(14, 0))
        self.pkg_status = self.ttk.Label(card, text='', style='Muted.TLabel', wraplength=self.px(640),
                                         justify='left')
        self.pkg_status.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.show_pkg_state()
        self.section(f, _('Console'))
        self.row(f, _('Language'), self.choice(f, 'language', LANGUAGES, 30),
                 _('The language the PS4 would be set to; the game uses it for text and commentary.'))
        name = self.ttk.Entry(f, textvariable=self.var('player_name'), width=30)
        self.row(f, _('Player name'), name, _('Shown where the game uses the PSN name.'))
        self.note(f, _('The game runs offline: online modes (Diamond Dynasty online, Ranked, '
                       'friends) need PSN servers and are not available.'))

    def build_display(self):
        f = self.page('display', _('Display'), _('How the game window is shown.'))
        self.check(f, 'fullscreen', _('Fullscreen'))
        self.row(f, _('Presentation'), self.choice(f, 'present_mode', PRESENT_MODES, 34),
                 _('Mailbox shows each frame as soon as it is ready without tearing; FIFO waits for the display.'))
        refresh = display_refresh_rate()
        hint = _('The game runs at 60 FPS on PS4. 30 FPS helps slower PCs.')
        if refresh:
            hint += ' ' + _('Your display runs at {} Hz.').format(refresh)
        self.row(f, _('Frame rate'), self.choice(f, 'frame_rate', FRAME_RATES, 34), hint)
        self.section(f, _('Picture'))
        self.row(f, _('Window size'), self.choice(f, 'window_size', WINDOW_SIZES, 34),
                 _('The game draws at 1920 × 1080; the window (or the screen in fullscreen) shows that picture '
                   'scaled to fit.'))
        self.row(f, _('Upscaling'), self.choice(f, 'scaler', SCALERS, 34),
                 _('FSR 1 keeps edges sharp when the image is enlarged; bilinear is softer.'))
        self.row(f, _('Sharpening'), self.choice(f, 'sharpening', SHARPENING, 34),
                 _('AMD RCAS after FSR 1 (only while the image is enlarged).'))
        self.check(f, 'show_fps', _('Show the FPS counter'))
        self.section(f, _('Audio'))
        self.row(f, _('Speakers'), self.choice(f, 'speakers', SPEAKERS, 34),
                 _('What the game is told the speakers are. Automatic follows the Windows speaker configuration '
                   'of the default playback device.'))

    def build_controls(self):
        f = self.page('controls', _('Controls'),
                      _('Keyboard and controller. They work together; changes apply when the game starts.'))
        self.key_buttons = {}
        self.capturing = None
        self.section(f, _('Keyboard'), top=6)
        self.note(f, _('Click a box, then press a key. ✕ removes a binding. Each PS4 button can have two keys. '
                       'Insert opens the in-game settings menu; Escape asks whether to quit the game.'), top=2)
        card = self.target(f)
        table = self.tk.Frame(card, bg=CARD)
        table.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(10, 0))
        rows = [(a, t) for a, t, _k, _p in BUTTON_ACTIONS] + [(a, t) for a, t, _k in STICK_ACTIONS]
        for r, (action, title) in enumerate(rows):
            self.ttk.Label(table, text=_(title)).grid(row=r, column=0, sticky='w', padx=(0, 24), pady=3)
            for slot in (0, 1):
                button = self.tk.Button(table, width=16, relief='flat', bg=CARD_HI, fg=TEXT,
                                        activebackground='#22304f', activeforeground=TEXT,
                                        font=self.font_ui, cursor='hand2', bd=0, padx=8, pady=4,
                                        command=lambda a=action, i=slot: self.capture_key(a, i))
                button.grid(row=r, column=1 + slot * 2, sticky='w', pady=3)
                clear = self.tk.Label(table, text='\ue711' if self.font_icon else '✕', bg=CARD, fg=MUTED,
                                      font=self.font_icon_sm or self.font_ui, cursor='hand2', padx=8)
                clear.grid(row=r, column=2 + slot * 2, sticky='w', padx=(2, 10))
                clear.bind('<Button-1>', lambda _e, a=action, i=slot: self.set_key(a, i, ''))
                clear.bind('<Enter>', lambda _e, w=clear: w.configure(fg=TEXT))
                clear.bind('<Leave>', lambda _e, w=clear: w.configure(fg=MUTED))
                self.key_buttons[(action, slot)] = button
        self.show_keys()
        self.pill(self.buttons_row(f), _('Default keys'), self.reset_keys).pack(side='left')

        self.section(f, _('Controller'))
        self.note(f, _('Which controller button gives each PS4 button (Xbox / PlayStation names).'), top=2)
        self.pad_vars = {}
        for action, title, _k, _p in BUTTON_ACTIONS:
            var = self.tk.StringVar(value=(self.app.get('pad_bindings') or {}).get(action, DEFAULT_PAD[action]))
            self.pad_vars[action] = var
            values = [v for v, _t in PAD_BUTTONS]
            box = self.ttk.Combobox(f, values=[_(t) for _v, t in PAD_BUTTONS], state='readonly', width=26)
            box.current(values.index(var.get()) if var.get() in values else values.index('none'))
            box.bind('<<ComboboxSelected>>', lambda _e, b=box, v=var: v.set(values[b.current()]))
            var.trace_add('write', lambda *_a, b=box, v=var: b.current(values.index(v.get())))
            self.row(f, _(title), box)
        self.check(f, 'pad_invert_y', _('Invert the right stick vertically'))
        self.pill(self.buttons_row(f), _('Default controller buttons'), self.reset_pad).pack(side='left')

        self.section(f, _('Vibration'))
        self.controller_name = self.ttk.Label(self.target(f), text=_('Detecting…'))
        self.row(f, _('Controller'), self.controller_name)
        self.controller_status = self.ttk.Label(self.target(f), text=_('Checking vibration support…'),
                                                style='Muted.TLabel')
        self.row(f, _('Status'), self.controller_status)
        self.check(f, 'pad_vibration', _('Controller vibration'),
                   _('The setting applies the next time the game starts.'))
        from widgets import Slider
        self.row(f, _('Vibration intensity'),
                 Slider(self.tk, self.target(f), self.var('pad_vibration_strength'), 0, 100, 5,
                        COLORS, self.px, self.font_ui_md, fmt='{:.0f}', width=300, suffix='%'))
        holder = self.buttons_row(f)
        self.controller_refresh_button = self.pill(holder, _('Detect controller'), self.detect_controller)
        self.controller_refresh_button.pack(side='left')
        self.controller_test_button = self.pill(holder, _('Test vibration'),
                                                lambda: self.detect_controller(test=True))
        self.controller_test_button.pack(side='left', padx=(8, 0))
        self.controller_test_button.configure(state='disabled')

    def key_list(self, action):
        value = (self.app.get('key_bindings') or {}).get(action, DEFAULT_KEYS[action])
        parts = (value.split('|') + ['', ''])[:2]
        return [p.strip() for p in parts]

    def show_keys(self):
        for (action, slot), button in self.key_buttons.items():
            if self.capturing == (action, slot):
                button.configure(text=_('Press a key…'), bg=ACCENT, fg='#14080a')
            else:
                name = self.key_list(action)[slot]
                button.configure(text=name or '—', bg=CARD_HI, fg=TEXT if name else MUTED)

    def set_key(self, action, slot, name):
        keys = self.key_list(action)
        keys[slot] = name
        if name:  # one binding per key: taken from the action that had it
            bindings = dict(self.app.get('key_bindings') or {})
            for other in DEFAULT_KEYS:
                if other == action:
                    continue
                others = self.key_list(other)
                if name in others:
                    others = ['' if k == name else k for k in others]
                    bindings[other] = '|'.join(others).strip('|') if any(others) else ''
            self.app['key_bindings'] = bindings
        if not keys[0] and keys[1]:
            keys = [keys[1], '']
        bindings = dict(self.app.get('key_bindings') or {})
        bindings[action] = '|'.join(k for k in keys if k)
        self.app['key_bindings'] = bindings
        self.capturing = None
        self.release_capture()
        self.show_keys()

    def capture_key(self, action, slot):
        self.release_capture()
        self.capturing = (action, slot)
        self.show_keys()
        # The click that opened the capture must not be taken as the binding.
        self.root.after(200, self.arm_capture)

    def arm_capture(self):
        if not self.capturing:
            return
        self.root.bind_all('<KeyPress>', self.captured_key)
        self.root.bind_all('<ButtonPress>', self.captured_button)
        self.poll_side_buttons(side_buttons())

    def poll_side_buttons(self, held):
        """Tk on Windows never reports the side buttons (XBUTTON1/2), so they are read from the system
        while a binding is being captured. `held`: their state a moment ago."""
        if not self.capturing:
            return
        down = side_buttons()
        if self.root.focus_displayof():
            for index, name in enumerate(('Mouse 4', 'Mouse 5')):
                if down[index] and not held[index]:
                    self.set_key(*self.capturing, name)
                    return
        self.side_poll = self.root.after(15, self.poll_side_buttons, down)

    def release_capture(self):
        for sequence in ('<KeyPress>', '<ButtonPress>'):
            self.root.unbind_all(sequence)
        if getattr(self, 'side_poll', None):
            self.root.after_cancel(self.side_poll)
            self.side_poll = None

    def captured_key(self, event):
        sym = event.keysym
        if sym in RESERVED_KEYS:
            self.status.configure(text=_(RESERVED_KEYS[sym]), fg=ACCENT)
            return 'break'
        name = WIN_NUMPAD.get(event.keycode) if sys.platform == 'win32' else None
        name = name or TK_KEYS.get(sym)
        if not name and len(sym) == 1 and sym.isalnum():
            name = sym.upper()
        if not name and sym.startswith('F') and sym[1:].isdigit():
            name = sym
        if not name and sym.startswith('KP_') and sym[3:].isdigit():
            name = f'Keypad {sym[3:]}'
        if name:
            self.set_key(*self.capturing, name)
        return 'break'

    def captured_button(self, event):
        name = {1: 'Mouse Left', 2: 'Mouse Middle', 3: 'Mouse Right', 4: 'Mouse 4', 5: 'Mouse 5',
                8: 'Mouse 4', 9: 'Mouse 5'}.get(event.num)
        if name:
            self.set_key(*self.capturing, name)
        return 'break'

    def captured_wheel(self, event):
        self.set_key(*self.capturing, 'Wheel Up' if event.delta > 0 else 'Wheel Down')
        return 'break'

    def reset_keys(self):
        self.app['key_bindings'] = {}
        self.capturing = None
        self.release_capture()
        self.show_keys()

    def reset_pad(self):
        for action, var in self.pad_vars.items():
            var.set(DEFAULT_PAD[action])

    # ---- trophies ----------------------------------------------------------------------------
    def build_trophies(self):
        tk = self.tk
        f = self.page('trophies', _('Trophies'),
                      _("The game's own trophy list. A trophy shows here, and as a notice in the game, "
                        'when you earn it.'))
        self.trophy_filter = 'all'
        self.trophy_icons, self.trophy_state, self.trophy_info = {}, None, None
        bar = tk.Frame(f, bg=SURFACE)
        bar.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(0, self.px(10)))
        self.trophy_filters = {}
        for name, title in (('locked', _('Locked')), ('earned', _('Earned')), ('all', _('All'))):
            button = self.pill(bar, title, lambda n=name: self.set_trophy_filter(n), background=SURFACE,
                               padding=(14, 7))
            button.pack(side='right', padx=(8, 0))
            self.trophy_filters[name] = button
        self.trophy_summary = tk.Label(f, text='', bg=SURFACE, fg=MUTED, font=self.font_ui, anchor='w',
                                       justify='left')
        self.trophy_summary.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(0, self.px(10)))
        self.trophy_body = tk.Frame(f, bg=SURFACE)
        self.trophy_body.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we')
        self.root.after(1500, self.poll_trophies)

    def trophy_user_dir(self):
        return Path(self.var('user_dir').get().strip() or DATA_DIR / 'user')

    def load_trophy_info(self):
        """The trophy list (texts in the console language), made from the trophy pack in the game folder
        when it is not there yet; None without a pack."""
        s = self.current()
        folder = game_folder(s)
        if not folder:
            return None
        out = DATA_DIR / 'out' / Path(GAME_EXE).stem / 'trophy'
        language = str(s.get('language') or '1')
        try:
            trophies.prepare(folder, GAME_KEY, out, int(language) if language.isdigit() else 1, False)
        except Exception:  # a broken pack must not break the launcher
            return None
        info = trophies.read_cache(out)
        if info:
            info['folder'] = out
        return info

    def trophy_icon(self, info, number, size, earned):
        name = (info['npcommid'], number, size, earned)
        if name not in self.trophy_icons:
            image = None
            try:
                from PIL import Image, ImageEnhance, ImageTk
                with Image.open(info['folder'] / f'TROP{number:03d}.PNG') as source:
                    icon = source.convert('RGBA').resize((size, size), Image.LANCZOS)
                if not earned:
                    icon = ImageEnhance.Brightness(ImageEnhance.Color(icon).enhance(0.15)).enhance(0.55)
                image = ImageTk.PhotoImage(icon)
            except (ImportError, OSError):
                pass
            self.trophy_icons[name] = image
        return self.trophy_icons[name]

    @staticmethod
    def mark_pill(button, on):
        kind = 'primary' if on else 'secondary'
        if button.kind != kind:
            button.kind = kind
            button.draw()

    def set_trophy_filter(self, name):
        self.trophy_filter, self.trophy_state = name, None
        self.render_trophies()

    def render_trophies(self, force=False):
        info = self.trophy_info
        if info is None or force:
            info = self.trophy_info = self.load_trophy_info() or False
        unlocked = trophies.read_unlocks(self.trophy_user_dir(), info['npcommid']) if info else {}
        state = (self.trophy_filter, tuple(sorted(unlocked.items())), bool(info))
        for name, button in self.trophy_filters.items():
            self.mark_pill(button, name == self.trophy_filter)
        if state == self.trophy_state:
            return
        self.trophy_state = state
        tk = self.tk
        for child in self.trophy_body.winfo_children():
            child.destroy()
        if not info:
            self.trophy_summary.configure(text='')
            self.note_in(self.trophy_body, _('No trophy list yet. Set the game folder on the Game page '
                                             '(it needs sce_sys/trophy/trophy00.trp).'))
            return
        progress = trophies.progress(info, unlocked)
        grades = '   '.join(f"{_(trophies.GRADE_NAMES[g])} {progress['earned'][g]}/{progress['total'][g]}"
                            for g in 'PGSB' if progress['total'][g])
        self.trophy_summary.configure(
            text=f"{info['title']}\n" + _('{} of {} earned').format(progress['count'], len(info['trophies']))
                 + f" · {progress['percent']}%   ·   {grades}")
        shown = 0
        size = self.px(52)
        for number, grade, hidden, name, detail in info['trophies']:
            when = unlocked.get(number)
            if (self.trophy_filter == 'earned') != (when is not None) and self.trophy_filter != 'all':
                continue
            shown += 1
            if hidden and when is None:
                name, detail = _('Hidden trophy'), _('Keep playing to find out how to earn it.')
            card = tk.Frame(self.trophy_body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
            card.pack(fill='x', pady=(0, 6))
            icon = self.trophy_icon(info, number, size, when is not None)
            if icon:
                tk.Label(card, image=icon, bg=CARD).pack(side='left', padx=(10, 12), pady=8)
            side = tk.Frame(card, bg=CARD)
            side.pack(side='right', padx=(10, 16))
            color = TROPHY_COLORS.get(grade, MUTED) if when is not None else MUTED
            tk.Label(side, text=_(trophies.GRADE_NAMES.get(grade, 'Bronze')), bg=CARD, fg=color,
                     font=('Segoe UI Semibold', 9)).pack(anchor='e')
            tk.Label(side, text=time.strftime('%d %b %Y', time.localtime(when)) if when is not None else _('Locked'),
                     bg=CARD, fg=MUTED, font=self.font_ui_sm).pack(anchor='e', pady=(2, 0))
            middle = tk.Frame(card, bg=CARD)
            middle.pack(side='left', fill='x', expand=True, pady=8)
            tk.Label(middle, text=name, bg=CARD, fg=TEXT if when is not None else MUTED, anchor='w',
                     font=self.font_ui_md).pack(anchor='w')
            tk.Label(middle, text=detail, bg=CARD, fg=MUTED, font=self.font_ui_sm, justify='left', anchor='w',
                     wraplength=self.px(520)).pack(anchor='w', pady=(1, 0))
        if not shown:
            self.note_in(self.trophy_body, _('Nothing here yet.') if self.trophy_filter == 'earned'
                         else _('Every trophy is earned.'))

    def note_in(self, parent, text):
        self.tk.Label(parent, text=text, bg=SURFACE, fg=MUTED, font=self.font_ui, anchor='w', justify='left',
                      wraplength=self.px(640)).pack(anchor='w', pady=(8, 0))

    def poll_trophies(self):
        """Every 1.5 s while the page is open: the game writes an unlock as it happens."""
        if self.current_page == 'trophies':
            try:
                self.render_trophies()
            except Exception:
                pass
        self.root.after(1500, self.poll_trophies)

    def build_advanced(self):
        f = self.page('advanced', _('Advanced'), _('Maintenance and options for testing.'))
        self.section(f, _('Launcher language'), top=2)
        self.row(f, _('Interface language'),
                 self.choice(f, 'ui_language', mlb_i18n.LANGUAGES, 24, self.change_ui_language),
                 _('This changes only the launcher. The game language is selected on the Game page.'))
        self.check(f, 'close_on_play', _('Close the launcher when the game starts'))
        extra = self.ttk.Entry(f, textvariable=self.var('extra_env'), width=60)
        self.row(f, _('Environment'), extra, _('Extra variables for the game, NAME=value separated by spaces.'))
        self.section(f, _('Graphics fixes'))
        self.note(f, _('Fixes of this port for MLB The Show. Leave them on; turn one off only to compare.'), top=2)
        for key, _variable, title, hint in GRAPHICS_FIXES:
            self.check(f, key, _(title), _(hint))
        self.section(f, _('Shader cache'))
        self.note(f, _('Compiled shaders are kept so the game does not stutter again. Clear the cache only '
                       'when the picture is broken after an update.'), top=2)
        holder = self.buttons_row(f)
        self.pill(holder, _('Clear shader cache'), self.clear_cache).pack(side='left')
        self.pill(holder, _('Open the saves folder'),
                  lambda: self.open_path(self.var('user_dir').get(), 'user_dir')).pack(side='left', padx=(8, 0))
        if RELEASES_API:
            self.section(f, _('Updates'))
            self.check(f, 'check_updates', _('Look for a new version when the launcher starts'))
            holder = self.buttons_row(f)
            self.pill(holder, _('Check for updates'),
                      lambda: threading.Thread(target=self.check_update, args=(True,), daemon=True).start()
                      ).pack(side='left')
            self.pill(holder, _('Releases page'), lambda: webbrowser.open(RELEASES_PAGE)).pack(side='left', padx=(8, 0))

    def build_log(self):
        tk = self.tk
        page = tk.Frame(self.content, bg=SURFACE)
        self.pages['log'] = page
        shell = tk.Frame(page, bg=SURFACE)
        shell.pack(fill='both', expand=True, padx=self.px(18), pady=(self.px(14), self.px(14)))
        head = tk.Frame(shell, bg=SURFACE)
        head.pack(fill='x', padx=self.px(26), pady=(self.px(18), self.px(14)))
        tk.Label(head, text=_('Log'), bg=SURFACE, fg=TEXT, font=self.font_title).pack(side='left')
        self.pill(head, _('Copy'), self.copy_log, background=SURFACE, padding=(16, 6)).pack(side='right')
        body = tk.Frame(shell, bg=SURFACE)
        body.pack(fill='both', expand=True, padx=self.px(26), pady=(0, self.px(8)))
        self.log = tk.Text(body, bg=CARD, fg='#d6deee', insertbackground=TEXT, font=('Cascadia Mono', 9), wrap='none',
                           relief='flat', state='disabled', highlightbackground=LINE, highlightthickness=1,
                           padx=self.px(12), pady=self.px(10))
        bar = self.ttk.Scrollbar(body, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)

    def current(self):
        """The settings as they stand on screen (not yet saved)."""
        return {**self.app, **{key: v.get() for key, v in self.vars.items()}}

    def refresh(self):
        """The cover, the banner, the game line and the status for the current choices."""
        s = self.current()
        self.info = info = game_info(game_folder(s))
        if info:
            self.id_label.configure(text=f"{info['id']}  ·  {_('version')} {info['version']}")
            self.ready.configure(text=_('Ready to play. The first start prepares the game and compiles shaders; '
                                        'it can stutter for a while.'))
        else:
            self.id_label.configure(text=GAME_ID)
            self.ready.configure(text=_('Choose the game folder (the one with eboot.bin) on the Game page.'))
        if not self.process:
            self.status.configure(text=_('Ready: {}').format(GAME_TITLE) if info else _('No game folder yet.'),
                                  fg=MUTED)
        self.draw_hero()
        self.draw_cover()

    # ---- actions -----------------------------------------------------------------------------
    def change_ui_language(self):
        """Save and rebuild every label as soon as the interface language changes."""
        global UI_LANGUAGE
        page = self.current_page or 'advanced'
        self.collect()
        UI_LANGUAGE = mlb_i18n.language_code(self.app['ui_language'])
        for child in self.root.winfo_children():
            child.destroy()
        self.vars = {}
        self.banner_key = self.cover_key = None
        self.build()
        self.show(page)
        self.refresh()
        if self.process:
            self.play_button.configure(state='disabled')
            self.stop_button.configure(state='normal')

    def collect(self):
        for key, v in self.vars.items():
            self.app[key] = v.get()
        if getattr(self, 'pad_vars', None):
            self.app['pad_bindings'] = {a: v.get() for a, v in self.pad_vars.items()}
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.app, indent=2) + '\n', encoding='utf-8')
        try:
            set_ini('show_fps', '1' if self.app['show_fps'] else '0')
        except OSError:
            pass

    def detect_controller(self, test=False):
        """Detect or briefly rumble the first SDL gamepad without starting the game renderer."""
        if self.process:
            self.controller_status.configure(text=_('Unavailable while the game is running.'))
            return
        probe = controller_probe_path()
        if not probe:
            self.controller_name.configure(text=_('Not available'))
            self.controller_status.configure(text=_('bb-probe.exe was not found.'))
            return
        self.controller_refresh_button.configure(state='disabled')
        self.controller_test_button.configure(state='disabled')
        self.controller_status.configure(text=_('Testing…') if test else _('Detecting…'))
        strength = vibration_strength(self.current())

        def work():
            command = [str(probe), '--controller-test', str(strength)] if test \
                else [str(probe), '--controller-info']
            try:
                result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                        errors='replace', timeout=5, env=controller_probe_environment(probe),
                                        creationflags=NO_WINDOW)
                if result.returncode:
                    raise OSError(result.stderr.strip() or _('controller probe failed'))
                info, error = parse_controller_probe(result.stdout), ''
            except (OSError, subprocess.TimeoutExpired) as failure:
                info, error = None, str(failure)
            self.ui_calls.put(lambda: self.show_controller(info, error, test))
        threading.Thread(target=work, daemon=True).start()

    def show_controller(self, info, error, test):
        if self.process:
            self.controller_refresh_button.configure(state='disabled')
            self.controller_test_button.configure(state='disabled')
            self.controller_status.configure(text=_('Unavailable while the game is running.'))
            return
        self.controller_refresh_button.configure(state='normal')
        if error:
            self.controller_name.configure(text=_('Not available'))
            self.controller_status.configure(text=_('Could not detect the controller: {}').format(error[:120]))
            return
        if not info['connected']:
            self.controller_name.configure(text=_('No controller detected'))
            self.controller_status.configure(text=_('Connect a controller, then detect again.'))
            return
        self.controller_name.configure(text=info['name'] or _('Game controller'))
        self.controller_test_button.configure(state='normal' if info['rumble'] else 'disabled')
        if not info['rumble']:
            status = _('Connected · Vibration not supported')
        elif test:
            status = _('Connected · Vibration test completed') if info['tested'] \
                else _('Connected · Vibration test failed')
        else:
            status = _('Connected · Vibration supported')
        self.controller_status.configure(text=status)

    def play(self):
        if self.process:
            return
        if self.pkg_job:
            self.messagebox.showinfo(NAME, _('Wait until the PKG install finishes.'))
            self.show('game')
            return
        self.collect()
        folder = game_folder(self.app)
        if not folder or not Path(folder).is_dir() or not game_available(self.app):
            self.messagebox.showerror(NAME, _('Choose the game folder with eboot.bin.'))
            self.show('game')
            return
        self.set_log('')
        try:
            env = game_environment(self.app)
        except OSError as error:
            self.messagebox.showerror(NAME, _('Could not save the input configuration: {}').format(error))
            return
        try:
            self.process = subprocess.Popen(run_command(), cwd=PORT_DIR, env=env,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except OSError as error:
            self.append(_('Could not start: {}').format(error) + '\n')
            self.process = None
            return
        self.job = GameJob(self.process)
        self.entered, self.last_problem, self.stopping = False, '', False
        threading.Thread(target=self.read_output, args=(self.process,), daemon=True).start()
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.controller_refresh_button.configure(state='disabled')
        self.controller_test_button.configure(state='disabled')
        self.status.configure(text=_('Preparing the game; it opens in its own window…'), fg=ACCENT)
        if self.app.get('close_on_play'):
            self.root.after(5000, self.root.destroy)

    def read_output(self, process):
        """Helper thread: the game's output to the Log page and to <saves folder>/last_run.log."""
        log = None
        try:
            log = open_run_log(Path(self.app.get('user_dir') or DATA_DIR / 'user'))
        except OSError:
            pass
        for raw in iter(process.stdout.readline, b''):
            text = raw.decode('utf-8', errors='replace')
            self.output.put(text)
            if log:
                log.write(text.replace('\r\n', '\n'))
        if log:
            log.close()
        self.output.put((process.wait(),))

    def drain_output(self):
        while not self.ui_calls.empty():
            self.ui_calls.get_nowait()()
        try:
            for _i in range(500):
                item = self.output.get_nowait()
                if isinstance(item, tuple):
                    code = item[0]
                    self.append(_('\n— the game exited (code {}) —\n').format(code))
                    self.process = None
                    if self.job:
                        self.job.close()
                    self.play_button.configure(state='normal')
                    self.stop_button.configure(state='disabled')
                    self.refresh()
                    self.detect_controller()
                    if not self.stopping and (code != 0 or not self.entered):
                        reason = self.last_problem or _('see the Log page')
                        self.status.configure(text=_('The game stopped: {}').format(reason[:110]), fg='#ff8a6a')
                        self.show('log')
                else:
                    self.last_problem = select_run_problem(self.last_problem, item.strip())
                    if 'Entering original x86-64 code' in item:
                        self.entered = True
                        self.status.configure(text=_('The game is running.'), fg=ACCENT)
                    self.append(item)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_output)

    def stop(self):
        if not self.process:
            return
        self.stop_button.configure(state='disabled')
        try:
            if not self.job or not self.job.terminate():
                terminate_process_tree(self.process)
        except (OSError, subprocess.SubprocessError) as error:
            self.stop_button.configure(state='normal')
            self.messagebox.showerror(NAME, _('Could not stop the game: {}').format(error))
            return
        self.stopping = True
        self.status.configure(text=_('Stopping the game…'), fg=ACCENT)

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > MAX_LOG_LINES:
            self.log.delete('1.0', f'{lines - MAX_LOG_LINES}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def set_log(self, text):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', text)
        self.log.configure(state='disabled')

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get('1.0', 'end'))

    def open_path(self, path, key=None):
        if key == 'user_dir' and not path:
            path = DATA_DIR / 'user'
        path = Path(path or DATA_DIR)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    # ---- updates -------------------------------------------------------------------------------
    def check_update(self, manual=False):
        """Helper thread: asks GitHub for the newest release and offers it when it is newer."""
        try:
            version, url, page = latest_release()
        except (OSError, ValueError, KeyError) as failure:
            if manual:
                self.ui_calls.put(lambda error=failure: self.messagebox.showerror(
                    NAME, _('Could not check for updates: {}').format(error)))
            return

        def show():
            if version_tuple(version) > version_tuple(VERSION):
                self.offer_update(version, url, page)
            elif manual:
                self.messagebox.showinfo(NAME, _('You have the latest version ({}).').format(VERSION))
        self.ui_calls.put(show)

    def offer_update(self, version, url, page):
        if self.update_box:
            return
        tk = self.tk
        text = _('Version {} is available.').format(version)
        box = tk.Frame(self.side, bg=CARD, highlightthickness=1, highlightbackground=ACCENT_DIM)
        self.update_label = tk.Label(box, text=text, bg=CARD, fg=ACCENT, font=self.font_ui_md,
                                     wraplength=self.px(190), justify='left')
        self.update_label.pack(anchor='w', padx=12, pady=(10, 8))
        buttons = tk.Frame(box, bg=CARD)
        buttons.pack(anchor='w', padx=12, pady=(0, 12))
        self.update_button = self.pill(buttons, _('Update'), lambda: self.install_update(version, url, page),
                                       kind='primary', padding=(14, 6))
        self.update_button.pack(side='left')
        self.pill(buttons, _("What's new"), lambda: webbrowser.open(page), padding=(12, 6)).pack(
            side='left', padx=(6, 0))
        box.pack(fill='x', padx=18, pady=(0, 14), before=self.side_note)
        self.update_box = box
        if not self.process:
            self.status.configure(text=text, fg=ACCENT)

    def install_update(self, version, url, page):
        if self.process:
            self.messagebox.showinfo(NAME, _('Close the game before updating.'))
            return
        if not FROZEN or not url:  # a source tree updates with git
            webbrowser.open(page)
            return
        if not self.messagebox.askyesno(NAME, _(
                'Install version {} now? The launcher closes, installs it and opens again. Saves and settings '
                'are kept.').format(version)):
            return
        self.update_button.configure(state='disabled')

        def progress(percent):
            self.update_label.configure(text=_('Downloading version {}… {}%').format(version, percent))

        def work():
            try:
                shutil.rmtree(UPDATE_DIR, ignore_errors=True)
                UPDATE_DIR.mkdir(parents=True, exist_ok=True)
                archive = UPDATE_DIR / 'update.zip'
                request = urllib.request.Request(url, headers={'User-Agent': 'mlb-launcher'})
                with urllib.request.urlopen(request, timeout=60) as response, open(archive, 'wb') as out:
                    total, done, shown = int(response.headers.get('Content-Length') or 0), 0, -1
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        percent = done * 100 // total if total else 0
                        if percent != shown:
                            shown = percent
                            self.ui_calls.put(lambda p=percent: progress(p))
                with zipfile.ZipFile(archive) as package:
                    package.extractall(UPDATE_DIR / 'new')
                archive.unlink()
                new = next((p.parent for p in (UPDATE_DIR / 'new').rglob(EXE_NAME)), None)
                if not new:
                    raise OSError(f'{EXE_NAME} is missing from the download')
                subprocess.Popen([str(new / EXE_NAME), '--install-update', str(PORT_DIR), str(os.getpid())],
                                 cwd=str(new), stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
                self.ui_calls.put(self.close)
            except (OSError, zipfile.BadZipFile) as failure:
                def failed(error=failure):
                    self.update_button.configure(state='normal')
                    self.update_label.configure(text=_('Version {} is available.').format(version))
                    self.messagebox.showerror(NAME, _('Update failed: {}').format(error))
                self.ui_calls.put(failed)
        threading.Thread(target=work, daemon=True).start()

    def clear_cache(self):
        if self.process:
            return
        cache = Path(self.app['user_dir'] or DATA_DIR / 'user') / 'cache'
        try:
            shutil.rmtree(cache)
        except FileNotFoundError:
            pass
        except OSError as error:
            self.messagebox.showerror(NAME, _('Could not clear the shader cache: {}').format(error))
            return
        self.messagebox.showinfo(NAME, _('Shader cache cleared. The next start stutters for a while as it is rebuilt.'))

    # ---- install from PKG ----------------------------------------------------------------------
    def show_pkg_state(self):
        """The PKG card as the install stands (also after the interface is rebuilt)."""
        job = self.pkg_job
        self.pkg_button.configure(state='disabled' if job else 'normal')
        self.pkg_cancel_button.configure(state='normal' if job and not job['cancelled'] else 'disabled')
        if job:
            self.pkg_bar.grid()
            self.pkg_bar['value'] = int(job['progress'] * 1000)
            self.pkg_status.configure(text=job['text'])
        else:
            self.pkg_bar.grid_remove()

    def pkg_progress(self, fraction, text):
        if not self.pkg_job:
            return
        self.pkg_job['progress'], self.pkg_job['text'] = fraction, text
        self.show_pkg_state()
        if not self.process:
            self.status.configure(text=text, fg=ACCENT)

    def choose_pkgs(self):
        """The chosen PKGs in install order (the game, then its updates), or None."""
        paths = self.filedialog.askopenfilenames(
            title=_('Choose the game PKG (and its update PKG)'),
            initialdir=self.app.get('pkg_dir') or str(Path.home() / 'Downloads'),
            filetypes=[(_('PS4 packages'), '*.pkg'), (_('All files'), '*.*')])
        if not paths:
            return None
        self.app['pkg_dir'] = str(Path(paths[0]).parent)
        pkgs = []
        for path in paths:
            try:
                pkgs.append(pkg_install.read_pkg(path))
            except (OSError, ValueError) as error:
                reason = _('this is not a PS4 PKG file.') if isinstance(error, ValueError) else error
                self.messagebox.showerror(NAME, f'{Path(path).name}: {reason}')
                return None
        for pkg in pkgs:
            if pkg['kind'] == 'dlc':
                self.messagebox.showerror(NAME, _('{} is downloadable content (DLC). Only the game and its updates '
                                                  'can be installed here.').format(pkg['path'].name))
                return None
            if pkg['kind'] == 'other':
                self.messagebox.showerror(NAME, _('{} is not a game or an update PKG.').format(pkg['path'].name))
                return None
            if pkg['delta']:
                self.messagebox.showerror(NAME, _('{} is a delta update (only the changed parts of the files): it '
                                                  'cannot be unpacked over the game. Use a full update PKG.').format(
                                              pkg['path'].name))
                return None
        games = [p for p in pkgs if p['kind'] == 'game']
        if len(games) > 1:
            self.messagebox.showerror(NAME, _('Choose only one game PKG (updates can go with it).'))
            return None
        if len({p['title_id'] for p in pkgs}) > 1:
            self.messagebox.showerror(NAME, _('These PKGs are for different games: {}.').format(
                ', '.join(sorted({p['title_id'] for p in pkgs}))))
            return None
        title_id = pkgs[0]['title_id']
        if title_id != GAME_ID and not self.messagebox.askyesno(NAME, _(
                'This PKG is {} ({}), not {} ({}). This port is made for {}. Install it anyway?').format(
                    pkgs[0]['title'] or _('another game'), title_id, GAME_TITLE, GAME_ID, GAME_ID)):
            return None
        return games + sorted((p for p in pkgs if p['kind'] == 'update'), key=lambda p: pkg_install.version_key(p['version']))

    def install_pkg(self):
        if self.pkg_job:
            return
        if self.process:
            self.messagebox.showinfo(NAME, _('Close the game before installing.'))
            return
        tool = pkg_install.tool_path(PORT_DIR)
        if not tool:
            self.messagebox.showerror(NAME, _('ps4-pkg-tool.exe was not found in the bin folder.'))
            return
        plan = self.choose_pkgs()
        if not plan:
            return
        title_id = plan[0]['title_id']
        if plan[0]['kind'] == 'game':
            chosen = self.filedialog.askdirectory(
                title=_('Where to install the game (a {} folder is made inside)').format(title_id),
                initialdir=self.app.get('install_dir') or str(plan[0]['path'].parent))
            if not chosen:
                return
            self.app['install_dir'] = chosen
            chosen = Path(chosen)
            target = chosen if chosen.name.upper() == title_id.upper() else chosen / title_id
            if target.is_dir() and any(target.iterdir()):
                question = _('There is already a game in {}. Replace its files?') if (target / GAME_EXE).is_file() \
                    else _('{} is not empty. Unpack the game into it anyway?')
                if not self.messagebox.askyesno(NAME, question.format(target)):
                    return
        else:
            folder = game_folder(self.current())
            if not game_available(self.current()):
                self.messagebox.showerror(NAME, _('Choose the game PKG too, or set the game folder first: the update '
                                                  'is unpacked over the game.'))
                return
            target = Path(folder)
            installed = game_info(folder)
            if installed and installed['id'] != title_id:
                self.messagebox.showerror(NAME, _('This update is for {}, but the game folder holds {}.').format(
                    title_id, installed['id']))
                return
            versions = ', '.join(p['version'] for p in plan)
            if not self.messagebox.askyesno(NAME, _('Install update {} into {}?').format(versions, target)):
                return
        need = sum(p['size'] for p in plan)
        place = next((p for p in (target, *target.parents) if p.exists()), None)
        try:
            free = shutil.disk_usage(place).free if place else need
        except OSError:
            free = need
        if free < need * 1.05 and not self.messagebox.askyesno(NAME, _(
                'The game needs about {:.1f} GB and the drive has {:.1f} GB free. Install anyway?').format(
                    need / 1e9, free / 1e9)):
            return
        self.collect()
        created = not target.exists()
        self.pkg_job = {'process': None, 'cancelled': False, 'progress': 0.0, 'text': _('Reading the PKG…'),
                        'target': target, 'created': created}
        self.set_log('')
        self.show_pkg_state()
        threading.Thread(target=self.run_pkg_install, args=(tool, plan, target, created), daemon=True).start()

    def run_pkg_install(self, tool, plan, target, created):
        """Helper thread: unpacks the PKGs one after the other into the game folder."""
        job = self.pkg_job
        total = sum(p['size'] for p in plan) or 1
        before, error = 0, ''

        def started(process):
            job['process'] = process
            if job['cancelled']:
                pkg_install_stop(process)

        for pkg in plan:
            if job['cancelled']:
                break
            what = _('the game') if pkg['kind'] == 'game' else _('update {}').format(pkg['version'])
            self.ui_calls.put(lambda w=what, b=before: self.pkg_progress(b / total, _('Unpacking {}…').format(w)))

            def progress(done, size, w=what, b=before, weight=pkg['size']):
                fraction = (b + weight * done / size) / total if size else b / total
                self.ui_calls.put(lambda: self.pkg_progress(fraction, _('Unpacking {}… {}%  ({:.1f} of {:.1f} GB)').format(
                    w, int(done * 100 / size) if size else 0, done / 1e9, size / 1e9)))

            def line(text):
                self.output.put(text + '\n')
            try:
                code, failure = pkg_install.extract(tool, pkg['path'], target, progress, line, started)
            except OSError as failure_error:
                code, failure = 1, str(failure_error)
            job['process'] = None
            if job['cancelled']:
                break
            if code:
                error = failure or _('ps4-pkg-tool exited with code {}').format(code)
                break
            before += pkg['size']
        if not error and not job['cancelled'] and plan[0]['kind'] == 'game' and not (target / GAME_EXE).is_file():
            error = _('The PKG was unpacked, but it holds no {}.').format(GAME_EXE)
        removed = False
        if (error or job['cancelled']) and created and plan[0]['kind'] == 'game':
            self.ui_calls.put(lambda: self.pkg_progress(job['progress'], _('Removing the unfinished install…')))
            shutil.rmtree(target, ignore_errors=True)
            removed = not target.exists()
        self.ui_calls.put(lambda: self.pkg_finished(plan, target, error, job['cancelled'], removed))

    def pkg_finished(self, plan, target, error, cancelled, removed):
        self.pkg_job = None
        self.show_pkg_state()
        self.pkg_status.configure(text='')
        game = plan[0]['kind'] == 'game'
        if cancelled or error:
            if removed:
                detail = _('The unfinished files were removed.')
            elif game:
                detail = _('The folder {} holds an unfinished install.').format(target)
            else:
                detail = _('The game folder may now hold a partial update: install the game PKG again if it does '
                           'not start.')
            text = _('The install was cancelled.') if cancelled else _('The install failed: {}').format(error)
            self.pkg_status.configure(text=text)
            self.refresh()
            if not cancelled:
                self.messagebox.showerror(NAME, text + '\n\n' + detail)
            elif not removed:
                self.messagebox.showinfo(NAME, text + '\n\n' + detail)
            return
        if game:
            self.var('game_dir').set(str(target))
        self.collect()
        self.refresh()
        info = game_info(str(target)) or {'title': GAME_TITLE, 'version': '?'}
        text = _('{} {} is installed in {}.').format(info['title'], info['version'], target)
        self.pkg_status.configure(text=text)
        self.messagebox.showinfo(NAME, text + ('\n\n' + _('The game folder is set: press Play.') if game else ''))

    def cancel_pkg(self):
        job = self.pkg_job
        if not job or job['cancelled']:
            return
        if not self.messagebox.askyesno(NAME, _('Stop the install?')):
            return
        job['cancelled'] = True
        if job['process']:
            pkg_install_stop(job['process'])
        self.pkg_progress(job['progress'], _('Stopping…'))

    def shortcut(self):
        """Name and icon of the desktop shortcut, then create it."""
        from PIL import Image, ImageTk

        window = self.tk.Toplevel(self.root)
        window.title(_('Desktop shortcut'))
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)
        shell = self.tk.Frame(window, bg=PANEL)
        shell.pack(padx=self.px(24), pady=self.px(22))
        self.tk.Label(shell, text=_('Desktop shortcut'), bg=PANEL, fg=TEXT,
                      font=self.font_title).grid(row=0, column=0, columnspan=2, sticky='w')
        self.tk.Label(shell, text=_('Shortcut name'), bg=PANEL, fg=TEXT,
                      font=self.font_ui).grid(row=1, column=0, sticky='w', pady=(18, 4))
        name = self.tk.StringVar(value=self.app.get('shortcut_name', APP_DEFAULTS['shortcut_name']))
        self.ttk.Entry(shell, textvariable=name, width=48).grid(row=2, column=0, columnspan=2, sticky='ew')
        self.tk.Label(shell, text=_('Shortcut icon'), bg=PANEL, fg=TEXT,
                      font=self.font_ui).grid(row=3, column=0, sticky='w', pady=(16, 4))
        icon = self.tk.StringVar(value=self.app.get('shortcut_icon', ''))
        preview = self.tk.Canvas(shell, width=self.px(112), height=self.px(112), bg=BG,
                                 highlightthickness=2, highlightbackground=ACCENT_DIM, cursor='hand2')
        preview.grid(row=4, column=0, sticky='w')
        photo = {'image': None}

        def draw_icon(*_args):
            preview.delete('all')
            try:
                with Image.open(icon.get()) as source:
                    image = source.convert('RGBA')
                    image.thumbnail((self.px(96), self.px(96)), Image.LANCZOS)
                photo['image'] = ImageTk.PhotoImage(image)
                preview.create_image(self.px(56), self.px(56), image=photo['image'])
            except (OSError, ValueError, self.tk.TclError):
                photo['image'] = None
                preview.create_text(self.px(56), self.px(56), text=_('Original icon'), fill=MUTED,
                                    font=self.font_ui_sm, width=self.px(86))

        def choose_icon(candidate=''):
            chosen = candidate or self.image_path(_('Shortcut icon'), icon.get())
            if chosen:
                try:
                    with Image.open(chosen) as image:
                        image.verify()
                except (OSError, ValueError) as error:
                    self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
                    return
                icon.set(chosen)

        actions = self.tk.Frame(shell, bg=PANEL)
        actions.grid(row=4, column=1, sticky='w', padx=(16, 0))
        self.pill(actions, _('Choose image'), choose_icon, background=PANEL).pack(anchor='w')
        self.pill(actions, _('Restore'), lambda: icon.set(''), kind='ghost', background=PANEL).pack(
            anchor='w', pady=(6, 0))
        buttons = self.tk.Frame(shell, bg=PANEL)
        buttons.grid(row=5, column=0, columnspan=2, sticky='ew', pady=(18, 0))
        self.pill(buttons, _('Cancel'), window.destroy, kind='ghost', background=PANEL).pack(side='right')

        def create():
            self.app['shortcut_name'] = name.get().strip() or APP_DEFAULTS['shortcut_name']
            self.app['shortcut_icon'] = icon.get()
            self.collect()
            window.destroy()
            self.create_shortcut()

        self.pill(buttons, _('Create shortcut'), create, kind='primary', background=PANEL).pack(
            side='right', padx=(0, 6))
        preview.bind('<Button-1>', lambda _event: choose_icon())
        self.enable_image_drop(preview, choose_icon)
        icon.trace_add('write', draw_icon)
        draw_icon()
        window.bind('<Escape>', lambda _event: window.destroy())

    def create_shortcut(self):
        """Create the configured .lnk on the desktop."""
        if FROZEN:
            target, arguments, icon = sys.executable, '', f'{sys.executable},0'
        else:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            target = str(pythonw if pythonw.exists() else sys.executable)
            arguments, icon = Path(__file__).resolve(), Path(__file__).resolve().parent / 'mlb.ico'
        custom_icon = self.app.get('shortcut_icon', '')
        if custom_icon:
            try:
                from PIL import Image
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                icon = CONFIG_DIR / 'custom-shortcut.ico'
                with Image.open(custom_icon) as image:
                    image.convert('RGBA').save(icon, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
            except OSError as error:
                self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
                return
        name = re.sub(r'[<>:"/\\|?*]', '', self.app.get('shortcut_name', '')).strip() or GAME_TITLE
        ps = lambda value: "'" + str(value).replace("'", "''") + "'"
        script = ('$desktop=[Environment]::GetFolderPath("Desktop");'
                  f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut($desktop+{ps(chr(92) + name + ".lnk")});'
                  f'$s.TargetPath={ps(target)};$s.WorkingDirectory={ps(PORT_DIR)};$s.IconLocation={ps(icon)};'
                  + (f'$s.Arguments={ps(chr(34) + str(arguments) + chr(34))};' if arguments else '') + '$s.Save()')
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True,
                                creationflags=NO_WINDOW)
        if result.returncode == 0:
            self.messagebox.showinfo(NAME, _('Shortcut created on the desktop.'))
        else:
            self.messagebox.showerror(NAME, result.stderr.decode(errors='replace')[:400])

    def add_to_steam(self):
        """Preview the library pictures before writing the non-Steam shortcut."""
        from PIL import Image, ImageTk
        import mlb_art

        window = self.tk.Toplevel(self.root)
        window.title(_('Add to Steam'))
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)
        shell = self.tk.Frame(window, bg=PANEL)
        shell.pack(padx=self.px(24), pady=self.px(22))
        self.tk.Label(shell, text=_('Add to Steam'), bg=PANEL, fg=TEXT,
                      font=self.font_title).grid(row=0, column=0, columnspan=3, sticky='w')
        self.tk.Label(shell, text=_('The pictures come from your game folder. Choose an image to use your own.'),
                      bg=PANEL, fg=MUTED, font=self.font_ui).grid(row=1, column=0, columnspan=3, sticky='w',
                                                                  pady=(2, 14))
        formats = [('portrait', _('Portrait'), (600, 900), (150, 225)),
                   ('header', _('Header'), (920, 430), (230, 108)),
                   ('hero', _('Hero'), (1920, 620), (300, 97))]
        previews, photos = {}, {}
        selected = {'path': self.app.get('steam_art', ''),
                    'crops': {name: dict(self.app.get('art_crops', {}).get(f'steam_{name}', {}))
                              for name, _label, _size, _preview in formats}}

        def default_picture(name, size):
            folder = game_folder(self.current())
            box, background = mlb_art.cover(folder), mlb_art.wide(folder)
            if name == 'portrait' and box is not None:
                return mlb_art._framed(box, size)
            if background is not None:
                return mlb_art._fill(background, size)
            return mlb_art._framed(box, size) if box is not None else None

        def redraw():
            for name, _label, _size, preview_size in formats:
                canvas = previews[name]
                canvas.delete('all')
                image = None
                try:
                    if selected['path']:
                        with Image.open(selected['path']) as source:
                            image = mlb_art.crop_image(source.convert('RGB'), preview_size, selected['crops'][name])
                    else:
                        image = default_picture(name, preview_size)
                except (OSError, ValueError):
                    image = None
                if image is not None:
                    photos[name] = ImageTk.PhotoImage(image)
                    canvas.create_image(0, 0, image=photos[name], anchor='nw')
                else:
                    canvas.create_text(preview_size[0] // 2, preview_size[1] // 2,
                                       text=_('Choose image'), fill=MUTED, font=self.font_ui_sm)
                canvas.create_rectangle(1, 1, preview_size[0] - 2, preview_size[1] - 2, outline=ACCENT_DIM, width=2)

        def choose(candidate=''):
            chosen = candidate or self.image_path(_('Choose image'), selected['path'])
            if chosen:
                selected['path'] = chosen
                redraw()

        def adjust(name, label, size):
            if not selected['path']:
                choose()
                if not selected['path']:
                    return
            result = self.edit_art('steam_art', label, size, f'steam_{name}',
                                   initial_path=selected['path'], initial_crop=selected['crops'][name])
            if result:
                selected['path'], selected['crops'][name] = result
                redraw()

        for column, (name, label, size, preview_size) in enumerate(formats):
            cell = self.tk.Frame(shell, bg=PANEL)
            cell.grid(row=2, column=column, sticky='n', padx=(0 if column == 0 else 16, 0))
            self.tk.Label(cell, text=label, bg=PANEL, fg=TEXT, font=self.font_ui_md).pack(anchor='w')
            canvas = self.tk.Canvas(cell, width=preview_size[0], height=preview_size[1], bg=BG,
                                    highlightthickness=0, cursor='hand2')
            canvas.pack(pady=(6, 8))
            previews[name] = canvas
            canvas.bind('<Button-1>', lambda _e, n=name, l=label, s=size: adjust(n, l, s))
            self.enable_image_drop(canvas, choose)
            self.pill(cell, _('Adjust crop'), lambda n=name, l=label, s=size: adjust(n, l, s),
                      background=PANEL, padding=(12, 6)).pack(anchor='w')
        footer = self.tk.Frame(shell, bg=PANEL)
        footer.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(18, 0))
        self.pill(footer, _('Choose image'), choose, background=PANEL).pack(side='left')

        def original():
            selected['path'] = ''
            redraw()
        self.pill(footer, _('Restore'), original, kind='ghost', background=PANEL).pack(side='left', padx=(6, 0))
        self.pill(footer, _('Cancel'), window.destroy, kind='ghost', background=PANEL).pack(side='right')

        def add():
            self.app['steam_art'] = selected['path']
            self.app.setdefault('art_crops', {}).update(
                {f'steam_{name}': crop for name, crop in selected['crops'].items()})
            self.collect()
            window.destroy()
            self.perform_add_to_steam()

        self.pill(footer, _('Add to Steam'), add, kind='primary', background=PANEL).pack(side='right', padx=(0, 6))
        redraw()
        window.bind('<Escape>', lambda _event: window.destroy())

    def perform_add_to_steam(self):
        """Write the shortcut and its pictures to Steam."""
        import steam_shortcuts
        steam = steam_shortcuts.steam_folder()
        configs = steam_shortcuts.user_configs(steam) if steam else []
        if not configs:
            self.messagebox.showerror(NAME, _('Steam was not found on this PC (or nobody has signed in to it yet).'))
            return
        if FROZEN:
            exe, options = Path(sys.executable), '--play'
        else:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            exe, options = (pythonw if pythonw.exists() else Path(sys.executable)), f'"{Path(__file__).resolve()}" --play'
        self.collect()
        restart = False
        if steam_shortcuts.steam_running():
            if not self.messagebox.askyesno(NAME, _('Steam has to close while the game is added; it starts '
                                                    'again afterwards. Close Steam now?')):
                return
            subprocess.Popen([str(steam / 'steam.exe'), '-shutdown'], creationflags=NO_WINDOW)
            for _i in range(60):
                time.sleep(0.5)
                if not steam_shortcuts.steam_running():
                    break
            else:
                self.messagebox.showerror(NAME, _('Steam did not close. Close it yourself and try again.'))
                return
            time.sleep(1)
            restart = True
        title = GAME_TITLE
        try:
            for config in configs:
                steam_shortcuts.add_shortcut(config / 'shortcuts.vdf', title, str(exe), str(PORT_DIR), str(exe), options)
                try:
                    import mlb_art
                    mlb_art.steam_artwork(
                        game_folder(self.app), config / 'grid', steam_shortcuts.shortcut_id(f'"{exe}"', title),
                        self.app.get('steam_art', ''),
                        {name: self.app.get('art_crops', {}).get(f'steam_{name}', {})
                         for name in ('portrait', 'header', 'hero')})
                except Exception:  # pictures are a nicety: the shortcut works without them
                    pass
        except (OSError, ValueError) as error:
            self.messagebox.showerror(NAME, _('Could not add the game to Steam: {}').format(error))
            return
        if restart:
            os.startfile('steam://open/games')
        self.messagebox.showinfo(NAME, _('The game is in your Steam library. It starts with the settings saved in '
                                         'this launcher.'))

    def close(self):
        job = self.pkg_job
        if job:
            if not self.messagebox.askyesno(NAME, _('A PKG install is running. Stop it and close?')):
                return
            job['cancelled'] = True
            if job['process']:
                pkg_install_stop(job['process'])
                try:
                    job['process'].wait(10)
                except (OSError, subprocess.SubprocessError):
                    pass
            if job['created']:
                shutil.rmtree(job['target'], ignore_errors=True)
        try:
            self.collect()
        except Exception:  # never keep the window open over a settings problem
            pass
        self.root.destroy()


class GameJob:
    """A Windows job holding run.py and everything it starts (the game, an in-game restart), so Stop
    ends all of them."""

    def __init__(self, process):
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if self.handle:
            assigned = self.kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(self.handle), ctypes.c_void_p(int(process._handle)))
            if not assigned:
                self.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
                self.handle = None

    def terminate(self):
        return bool(self.handle and self.kernel32.TerminateJobObject(ctypes.c_void_p(self.handle), 1))

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None


def terminate_process_tree(process):
    """Stop a launcher process and every child when its Windows job is unavailable."""
    if process.poll() is not None:
        return
    taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe'
    result = subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW, timeout=10)
    if result.returncode and process.poll() is None:
        detail = result.stdout.decode(errors='replace').strip()
        raise OSError(detail or _('taskkill exited with code {}').format(result.returncode))


def pkg_install_stop(process):
    """Ends a running ps4-pkg-tool (it starts nothing of its own)."""
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass


def version_tuple(text):
    return tuple(int(number) for number in re.findall(r'\d+', text or ''))


def latest_release():
    """(version, zip URL, page URL) of the newest GitHub release."""
    request = urllib.request.Request(RELEASES_API, headers={'Accept': 'application/vnd.github+json',
                                                            'User-Agent': 'mlb-launcher'})
    with urllib.request.urlopen(request, timeout=15) as response:
        release = json.load(response)
    version = '.'.join(re.findall(r'\d+', release['tag_name']))
    url = next((asset['browser_download_url'] for asset in release.get('assets', [])
                if asset.get('name', '').lower().endswith('.zip')), None)
    return version, url, release.get('html_url') or RELEASES_PAGE


def install_update(target, wait_pid):
    """--install-update TARGET PID, run by the downloaded version from its temporary folder: waits for
    the old launcher to close, copies this version over TARGET (never the saves, settings or art) and
    starts it."""
    target = Path(target)
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000, False, int(wait_pid))  # SYNCHRONIZE
    if handle:
        kernel.WaitForSingleObject(handle, 60000)
        kernel.CloseHandle(handle)
    ignore = shutil.ignore_patterns(*USER_FILES)
    for _attempt in range(30):
        try:
            shutil.copytree(PORT_DIR, target, dirs_exist_ok=True, ignore=ignore)
            break
        except OSError:  # a file still in use: the old launcher is closing
            time.sleep(1)
    else:
        ctypes.windll.user32.MessageBoxW(None, _('Could not install the update. Download it from the '
                                                 'releases page.'), NAME, 0x10)
        if RELEASES_PAGE:
            webbrowser.open(RELEASES_PAGE)
        return 1
    subprocess.Popen([str(target / EXE_NAME)], cwd=str(target))
    return 0


def open_run_log(log_dir, keep=5):
    """last_run.log for a new run; the previous ones move to last_run.1.log ... last_run.<keep-1>.log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    for n in range(keep - 1, 0, -1):
        older = log_dir / (f'last_run.{n - 1}.log' if n > 1 else 'last_run.log')
        try:
            if older.exists():
                older.replace(log_dir / f'last_run.{n}.log')
        except OSError:
            pass
    return open(log_dir / 'last_run.log', 'w', encoding='utf-8', buffering=1)


def play_without_window(settings):
    """--play: the game with the saved settings (shortcuts, Steam); output to the console when there is
    one and to <saves folder>/last_run.log."""
    attach_stdio()
    log_dir = Path(settings.get('user_dir') or DATA_DIR / 'user')
    with open_run_log(log_dir) as log:
        try:
            env = game_environment(settings)
        except OSError as error:
            text = _('Could not save the input configuration: {}').format(error) + '\n'
            log.write(text)
            try:
                sys.stderr.write(text)
            except (OSError, ValueError):
                pass
            return 1
        process = subprocess.Popen(run_command(), cwd=PORT_DIR, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        for raw in iter(process.stdout.readline, b''):
            text = raw.decode('utf-8', errors='replace')
            log.write(text)
            try:
                sys.stdout.write(text)
            except (OSError, ValueError):
                pass
        return process.wait()


def main():
    args = sys.argv[1:]
    if args and args[0] in ('--run', '--script'):
        sys.exit(run_role(args))
    if args[:1] == ['--install-update'] and len(args) == 3:
        sys.exit(install_update(args[1], args[2]))
    if FROZEN and UPDATE_DIR not in PORT_DIR.parents:
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)  # what a finished update left behind
    settings = {**APP_DEFAULTS, **load_json(CONFIG_FILE, {})}
    if '--play' in args and game_available(settings):
        sys.exit(play_without_window(settings))
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except (ImportError, tk.TclError):
        root = tk.Tk()
    Launcher(root, tk, ttk, filedialog, messagebox)
    root.mainloop()


if __name__ == '__main__':
    main()
