"""Devpost thumbnail (3:2): Insight, indoor navigation on your phone.

  uv run --no-project --with pillow python slides/make_thumbnail.py
    -> slides/devpost-thumbnail.png (still) and slides/devpost-thumbnail.gif (animated)

The animation walks the "you" dot along the route to the door while the voice bubble says
what Insight would: the steps to the turn, "Turn right now", "Go straight", arrival.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
W, H, SS = 1500, 1000, 2  # drawn at 2x, then downsampled for smooth edges
GIF_SIZE = (900, 600)  # 3:2, small enough to stay well under Devpost's 5 MB
FPS = 20
SPEED = 170  # route pixels per second
STEP_PX = 73  # one spoken "step" along the route: 4 steps from the start to the corner
INK, INK_2 = (14, 23, 38), (27, 40, 61)
AMBER, AMBER_SOFT = (232, 150, 46), (251, 235, 213)
PAPER, ON_INK, GREY = (255, 255, 255), (230, 235, 242), (90, 103, 120)
DIM = (120, 92, 52)  # route already walked

START, CORNER, TURN_END, GOAL = (990, 740), (990, 450), (1200, 450), (1200, 300)
PATH = [START, CORNER, TURN_END, GOAL]
BUBBLE = (1050, 640, 1272, 720)

# Cambria and Calibri where installed; their metric twins (Caladea, Carlito) on Linux.
FONTS = {
    "head": ["Cambria Bold.ttf", "cambriab.ttf", "Caladea-Bold.ttf"],
    "body": ["Calibri.ttf", "calibri.ttf", "Carlito-Regular.ttf"],
    "bold": ["Calibri Bold.ttf", "calibrib.ttf", "Carlito-Bold.ttf"],
}
FONT_DIRS = [Path("/usr/share/fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts",
             Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts")]
_font_cache = {}


def font(kind, size):
    if (kind, size) in _font_cache:
        return _font_cache[kind, size]
    found = ImageFont.load_default(size * SS)
    for name in FONTS[kind]:
        hits = [h for root in FONT_DIRS if root.exists() for h in root.rglob(name)]
        if hits:
            found = ImageFont.truetype(str(hits[0]), size * SS)
            break
    _font_cache[kind, size] = found
    return found


def s(*v):
    return [round(x * SS) for x in v]


def seg_lengths():
    return [math.dist(a, b) for a, b in zip(PATH, PATH[1:], strict=False)]


def point_at(dist):
    """Point on the route `dist` pixels from the start."""
    for (a, b), length in zip(zip(PATH, PATH[1:], strict=False), seg_lengths(), strict=True):
        if dist <= length:
            f = dist / length
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        dist -= length
    return GOAL


def base(glow=True):
    """Everything that does not move. The GIF skips the soft glow: 256 colours band it."""
    img = Image.new("RGB", (W * SS, H * SS), INK)
    if glow:
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse(s(780, 170, 1400, 830), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(120 * SS))
        img = Image.composite(Image.new("RGB", img.size, INK_2), img, mask)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle(s(905, 150, 1295, 860), radius=62 * SS, fill=(19, 31, 50),
                        outline=(*PAPER, 215), width=6 * SS)
    d.rounded_rectangle(s(1055, 172, 1145, 186), radius=7 * SS, fill=(*PAPER, 90))
    d.text(s(90, 300), "Insight", font=font("head", 150), fill=PAPER)
    d.text(s(92, 500), "Indoor navigation", font=font("body", 54), fill=ON_INK)
    d.text(s(92, 566), "on your phone", font=font("body", 54), fill=ON_INK)
    return img


def frame(bg, walked, say, pulse=0.0, arrived=False):
    """One frame: the route (walked part dimmed), the moving dot, the door, the bubble."""
    img = bg.copy()
    d = ImageDraw.Draw(img, "RGBA")
    total, pos = sum(seg_lengths()), 0.0
    while pos <= total:  # route dots, every 26 px
        x, y = point_at(pos)
        d.ellipse(s(x - 6, y - 6, x + 6, y + 6), fill=DIM if pos < walked else AMBER)
        pos += 26
    gx, gy = GOAL
    door_r = 46 + (6 * math.sin(pulse * math.pi) if arrived else 0)
    d.ellipse(s(gx - door_r, gy - door_r, gx + door_r, gy + door_r), fill=AMBER)
    door = Image.open(HERE / "icons" / "door.png").convert("RGBA").resize(s(54, 54))
    img.paste(door, s(gx - 27, gy - 27), door)
    if not arrived:
        x, y = point_at(walked)
        for k in range(2):  # rings pulse outward from the dot
            phase = (pulse + k / 2) % 1.0
            r = 24 + 30 * phase
            d.ellipse(s(x - r, y - r, x + r, y + r), outline=(*AMBER, int(200 * (1 - phase))),
                      width=3 * SS)
        d.ellipse(s(x - 20, y - 20, x + 20, y + 20), fill=AMBER, outline=PAPER, width=5 * SS)
    x0, y0, x1, y1 = BUBBLE
    d.rounded_rectangle(s(x0, y0, x1, y1), radius=18 * SS, fill=AMBER_SOFT)
    d.text(s(x0 + 22, y0 + 14), say[0], font=font("bold", 26), fill=INK)
    d.text(s(x0 + 22, y0 + 44), say[1], font=font("body", 24), fill=GREY)
    return img


def timeline():
    """(walked px, what it says, pulse phase, arrived) for every animation frame."""
    lengths = seg_lengths()
    corner = lengths[0]
    total = sum(lengths)
    out, t, walked = [], 0.0, 0.0
    dt = 1 / FPS

    def add(w, say, arrived=False):
        nonlocal t
        out.append((w, say, (t * 1.2) % 1.0, arrived))
        t += dt

    for _ in range(int(0.6 * FPS)):  # a moment at the start
        add(0.0, ("Turn right", f"in {math.ceil(corner / STEP_PX)} steps"))
    while walked < corner:
        left = corner - walked
        say = ("Turn right", "now") if left < STEP_PX * 0.6 else (
            "Turn right", f"in {math.ceil(left / STEP_PX)} step{'s' if math.ceil(left / STEP_PX) > 1 else ''}")
        add(walked, say)
        walked = min(corner, walked + SPEED * dt)
    for _ in range(int(0.5 * FPS)):  # turning at the corner
        add(corner, ("Turn right", "now"))
    while walked < total:
        add(walked, ("Go straight", "to the door"))
        walked = min(total, walked + SPEED * dt)
    for _ in range(int(1.6 * FPS)):
        add(total, ("You're at", "the door"), arrived=True)
    return out


def main(out_dir=HERE):
    still = frame(base(), 0.0, ("Turn right", "in 4 steps"), pulse=0.35)
    still.resize((W, H), Image.LANCZOS).save(out_dir / "devpost-thumbnail.png", optimize=True)
    bg = base(glow=False)
    frames = [frame(bg, w, say, p, a).resize(GIF_SIZE, Image.LANCZOS)
              for w, say, p, a in timeline()]
    palette = frames[0].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    gif[0].save(out_dir / "devpost-thumbnail.gif", save_all=True, append_images=gif[1:],
                duration=round(1000 / FPS), loop=0, optimize=True, disposal=1)
    print("wrote", out_dir / "devpost-thumbnail.png", "and", out_dir / "devpost-thumbnail.gif",
          f"({len(gif)} frames)")


if __name__ == "__main__":
    main()
