# SPDX-License-Identifier: GPL-2.0-or-later
"""Pictures for the MLB The Show launcher (Pillow).

No game images ship with the port: the banner, the cover and the Steam library pictures are made
at run time from the player's own game folder (sce_sys/pic1.png or pic0.png, sce_sys/icon0.png),
or from an image the player picks in the launcher. The emblem is the port's own icon (mlb.png,
drawn by packaging/windows/make_mlb_icon.py).
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def resource_dirs():
    here = Path(__file__).resolve().parent
    bundle = Path(getattr(sys, '_MEIPASS', here))
    return [bundle, bundle / 'launcher', here]


def _open(path):
    try:
        with Image.open(path) as image:
            return image.convert('RGB')
    except (OSError, ValueError):
        return None


def cover(game_dir):
    """The game's box art (sce_sys/icon0.png), or None."""
    return _open(Path(game_dir or '.') / 'sce_sys' / 'icon0.png') if game_dir else None


def wide(game_dir):
    """The game's wide background (sce_sys/pic1.png, else pic0.png), or None."""
    if not game_dir:
        return None
    for name in ('pic1.png', 'pic0.png'):
        image = _open(Path(game_dir) / 'sce_sys' / name)
        if image is not None:
            return image
    return None


def _fill(image, size, focus=(0.5, 0.5)):
    """The image scaled to cover size, keeping `focus` in frame."""
    w, h = size
    scale = max(w / image.width, h / image.height)
    scaled = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.LANCZOS)
    left = max(0, min(scaled.width - w, round(focus[0] * scaled.width - w / 2)))
    top = max(0, min(scaled.height - h, round(focus[1] * scaled.height - h / 2)))
    return scaled.crop((left, top, left + w, top + h))


def crop_image(image, size, crop=None):
    """Fill ``size`` using a saved zoom and focal point from the image editor."""
    crop = crop or {}
    zoom = max(1.0, min(3.0, float(crop.get('zoom', 1.0))))
    focus = (max(0.0, min(1.0, float(crop.get('x', 0.5)))), max(0.0, min(1.0, float(crop.get('y', 0.5)))))
    w, h = size
    scale = max(w / image.width, h / image.height) * zoom
    scaled = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.LANCZOS)
    left = max(0, min(scaled.width - w, round(focus[0] * scaled.width - w / 2)))
    top = max(0, min(scaled.height - h, round(focus[1] * scaled.height - h / 2)))
    return scaled.crop((left, top, left + w, top + h))


def _night_field(size):
    """Fallback banner with no game folder yet: stadium lights over a dark field (original)."""
    w, h = size
    image = Image.new('RGB', size, (10, 18, 36))
    draw = ImageDraw.Draw(image)
    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line((0, y, w, y), fill=(int(12 + 20 * (1 - t)), int(22 + 30 * (1 - t)), int(44 + 50 * (1 - t))))
    field = Image.new('L', size, 0)
    ImageDraw.Draw(field).ellipse((-w * 0.3, h * 0.62, w * 1.3, h * 1.9), fill=255)
    image = Image.composite(Image.new('RGB', size, (22, 74, 46)), image, field.filter(ImageFilter.GaussianBlur(6)))
    lights = Image.new('L', size, 0)
    ld = ImageDraw.Draw(lights)
    for x in (0.12, 0.88):
        cx, cy = w * x, h * 0.18
        ld.ellipse((cx - w * 0.16, cy - h * 0.5, cx + w * 0.16, cy + h * 0.5), fill=70)
    image = Image.composite(Image.new('RGB', size, (200, 220, 255)), image,
                            lights.filter(ImageFilter.GaussianBlur(max(10, w // 25))))
    return image


def banner(game_dir, width, height, page, custom='', crop=None):
    """The Play page header: the player's image, else the game's wide art, else a painted field;
    darkened behind the text and faded into the page at the bottom."""
    size = (max(width, 8), max(height, 8))
    image = _open(custom) if custom else None
    if image is not None:
        image = crop_image(image, size, crop)
    else:
        art = wide(game_dir)
        image = _fill(art, size, (0.5, 0.35)) if art is not None else _night_field(size)
    image = Image.blend(image, Image.new('RGB', size, (4, 8, 16)), 0.25)
    w, h = size
    shade = Image.new('L', (1, h))
    pixels = shade.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        pixels[0, y] = 0 if t < 0.5 else int(min(1.0, (t - 0.5) / 0.5) ** 1.1 * 225)
    return Image.composite(Image.new('RGB', size, page), image, shade.resize(size))


def _rounded_mask(size, radius):
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def cover_tile(game_dir, side, page, custom='', crop=None, ring=(214, 46, 58)):
    """The square cover with rounded corners, a soft shadow and a thin accent ring."""
    side = max(side, 32)
    art = _open(custom) if custom else None
    art = crop_image(art, (side, side), crop) if art is not None else None
    if art is None:
        box = cover(game_dir)
        art = box.resize((side, side), Image.LANCZOS) if box is not None else None
    if art is None:
        art = _night_field((side, side))
        icon = emblem(side // 2)
        if icon is not None:
            art.paste(icon, (side // 4, side // 4), icon)
    pad = max(10, side // 14)
    radius = max(10, side // 12)
    canvas = Image.new('RGBA', (side + pad * 2, side + pad * 2), (0, 0, 0, 0))
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad + 4, pad + 8, pad + side + 4, pad + side + 8), radius=radius,
                                             fill=(0, 0, 0, 130))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(6, side // 30))))
    framed = art.convert('RGBA')
    framed.putalpha(_rounded_mask((side, side), radius))
    canvas.alpha_composite(framed, (pad, pad))
    ImageDraw.Draw(canvas).rounded_rectangle((pad - 2, pad - 2, pad + side + 1, pad + side + 1), radius=radius + 1,
                                             outline=ring + (230,), width=2)
    flat = Image.new('RGB', canvas.size, page)
    flat.paste(canvas, mask=canvas.split()[3])
    return flat


def _framed(art, size):
    """The square box art centred on a blurred, darkened copy of itself."""
    background = _fill(art, size).filter(ImageFilter.GaussianBlur(max(size) / 30))
    background = Image.blend(background, Image.new('RGB', size, (6, 10, 20)), 0.35)
    side = min(size)
    background.paste(art.resize((side, side), Image.LANCZOS), ((size[0] - side) // 2, (size[1] - side) // 2))
    return background


def steam_artwork(game_dir, grid_dir, appid, custom_art='', crops=None):
    """Steam library pictures for the shortcut with this id: cover (p), header, hero.
    Returns the files written; nothing when there is no art."""
    art = _open(custom_art) if custom_art else None
    custom = art is not None
    art = art or cover(game_dir)
    if art is None:
        return []
    grid_dir = Path(grid_dir)
    grid_dir.mkdir(parents=True, exist_ok=True)
    if custom:
        crops = crops or {}
        pictures = {f'{appid}p.png': crop_image(art, (600, 900), crops.get('portrait')),
                    f'{appid}.png': crop_image(art, (920, 430), crops.get('header')),
                    f'{appid}_hero.png': crop_image(art, (1920, 620), crops.get('hero'))}
    else:
        background = wide(game_dir)
        pictures = {f'{appid}p.png': _framed(art, (600, 900)),
                    f'{appid}.png': _fill(background, (920, 430)) if background else _framed(art, (920, 430)),
                    f'{appid}_hero.png': _fill(background, (1920, 620)) if background else _framed(art, (1920, 620))}
    written = []
    for name, picture in pictures.items():
        picture.save(grid_dir / name)
        written.append(grid_dir / name)
    return written


def emblem(side):
    """The port's icon (mlb.png) at side×side with its transparency, or None."""
    for directory in resource_dirs():
        path = directory / 'mlb.png'
        if path.is_file():
            try:
                with Image.open(path) as image:
                    return image.convert('RGBA').resize((side, side), Image.LANCZOS)
            except OSError:
                return None
    return None
