"""Draws the MLB port's launcher icon (a baseball on a night-game navy tile) as .ico and .png.
Original artwork, no league or game logos. Needs Pillow.
usage: make_mlb_icon.py launcher/mlb.ico launcher/mlb.png"""
import math
import sys
from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024  # drawn large, scaled down for every icon size
C = S / 2


def tile():
    """Rounded navy square, lighter at the top like stadium lights over the field."""
    image = Image.new('RGBA', (S, S))
    draw = ImageDraw.Draw(image)
    for y in range(S):
        t = y / (S - 1)
        draw.line((0, y, S, y), fill=(int(24 + 18 * (1 - t)), int(44 + 26 * (1 - t)), int(92 + 40 * (1 - t)), 255))
    glow = Image.new('L', (S, S), 0)
    ImageDraw.Draw(glow).ellipse((C - 520, -420, C + 520, 420), fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    image = Image.composite(Image.new('RGBA', (S, S), (120, 160, 230, 255)), image, glow)
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((24, 24, S - 24, S - 24), radius=200, fill=255)
    image.putalpha(mask)
    return image


def ball(radius):
    """A white ball with soft shading and two red seams."""
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    shade = Image.new('L', (S, S), 0)
    sd = ImageDraw.Draw(shade)
    for r in range(radius, 0, -3):  # light from the upper left
        t = r / radius
        sd.ellipse((C - 70 * (1 - t) - r, C - 80 * (1 - t) - r, C - 70 * (1 - t) + r, C - 80 * (1 - t) + r),
                   fill=int(150 + 105 * (1 - t ** 2.2)))
    disc = Image.new('L', (S, S), 0)
    ImageDraw.Draw(disc).ellipse((C - radius, C - radius, C + radius, C + radius), fill=255)
    white = Image.merge('RGBA', (shade.point(lambda v: min(255, v + 12)), shade.point(lambda v: min(255, v + 8)),
                                 shade, disc))
    layer.alpha_composite(white)

    seams = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(seams)
    red = (206, 32, 44, 255)
    for side in (-1, 1):
        # Each seam is an arc of a circle centred outside the ball.
        cx = C + side * radius * 1.32
        rr = radius * 0.86
        points = []
        for i in range(61):
            a = math.radians(180 + (-48 + 96 * i / 60)) if side > 0 else math.radians(-48 + 96 * i / 60)
            points.append((cx + rr * math.cos(a), C + rr * math.sin(a)))
        d.line(points, fill=red, width=14, joint='curve')
        # Stitches: short strokes across the seam.
        for i in range(3, 58, 5):
            x, y = points[i]
            nx, ny = points[i + 1]
            dx, dy = nx - x, ny - y
            length = math.hypot(dx, dy) or 1
            px, py = -dy / length, dx / length
            for k in (-1, 1):
                ox, oy = x + px * 26 * k, y + py * 26 * k
                d.line((x + px * 6 * k, y + py * 6 * k, ox + dx * 0.6, oy + dy * 0.6), fill=red, width=11)
    seams.putalpha(ImageChops.multiply(seams.getchannel('A'), disc))
    layer.alpha_composite(seams)

    shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((C - radius + 24, C - radius + 40, C + radius + 24, C + radius + 40),
                                   fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    shadow.alpha_composite(layer)
    return shadow


def draw():
    image = tile()
    art = ball(330)
    art.putalpha(ImageChops.multiply(art.getchannel('A'), image.getchannel('A')))
    image.alpha_composite(art)
    return image


if __name__ == '__main__':
    icon = draw()
    icon.save(sys.argv[1], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if len(sys.argv) > 2:
        icon.resize((256, 256), Image.LANCZOS).save(sys.argv[2])
