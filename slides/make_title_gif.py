"""Animated phone for the Insight title slide: the "you" dot walks the dotted route to the door
while the voice bubble says each direction, in the guide's own words (server/entrance_nav.py).

  uv run --no-project --with pillow python slides/make_title_gif.py   -> slides/insight-title.gif

Same geometry and colours as phone() in build_insight_deck.py: a 3.0 x 5.8 inch phone on the
title slide's navy, with MARGIN_IN of navy around it so the GIF can sit on the slide seamlessly.
PowerPoint plays the animation in the slide show; a PDF shows the first frame.
"""

import math
from pathlib import Path

from make_thumbnail import font  # Calibri where installed, Carlito on Linux
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / "insight-title.gif"
PHONE_W_IN, PHONE_H_IN = 3.0, 5.8
MARGIN_IN = 0.1  # navy around the phone, so the outline is never clipped
PPI, SS = 150, 2  # GIF pixels per inch; drawn at 2x and downsampled for smooth edges
FPS = 15
SPEED_IN = 1.1  # route inches per second
STEP_IN = 0.58  # one spoken "step": about 4 from the start to the first corner

INK, INK_2 = (0x0E, 0x17, 0x26), (0x1B, 0x28, 0x3D)
PAPER, ON_INK_MUTED, MUTED = (255, 255, 255), (0x9A, 0xA8, 0xBB), (0x5A, 0x67, 0x78)
AMBER, AMBER_SOFT = (0xE8, 0x96, 0x2E), (0xFB, 0xEB, 0xD5)
DIM = (0x6E, 0x55, 0x33)  # route already walked: amber faded into the phone's navy

W_IN, H_IN = PHONE_W_IN + 2 * MARGIN_IN, PHONE_H_IN + 2 * MARGIN_IN


def px(v):
    return round(v * PPI * SS)


def at(fx, fy):
    """A point given as fractions of the phone (as in phone()), in inches on the GIF."""
    return MARGIN_IN + PHONE_W_IN * fx, MARGIN_IN + PHONE_H_IN * fy


PATH = [at(0.24, 0.82), at(0.24, 0.42), at(0.76, 0.42), at(0.76, 0.22)]
LEGS = [math.dist(a, b) for a, b in zip(PATH, PATH[1:], strict=False)]
DOOR = Image.open(HERE / "icons" / "door.png").convert("RGBA")


def point_at(d):
    for (a, b), length in zip(zip(PATH, PATH[1:], strict=False), LEGS, strict=True):
        if d <= length:
            f = d / length
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        d -= length
    return PATH[-1]


def frame(walked, say, pulse, arrived):
    img = Image.new("RGB", (px(W_IN), px(H_IN)), INK)
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0 = MARGIN_IN, MARGIN_IN
    d.rounded_rectangle([px(x0), px(y0), px(x0 + PHONE_W_IN), px(y0 + PHONE_H_IN)],
                        radius=px(0.16 * min(PHONE_W_IN, PHONE_H_IN)), fill=INK_2,
                        outline=PAPER, width=px(2.5 / 72))
    sx = x0 + PHONE_W_IN / 2
    d.rounded_rectangle([px(sx - 0.22), px(y0 + 0.12), px(sx + 0.22), px(y0 + 0.18)],
                        radius=px(0.03), fill=ON_INK_MUTED)
    total, pos = sum(LEGS), 0.0
    while pos <= total + 1e-9:  # route dots every 0.17 in, dimmed once walked
        x, y = point_at(pos)
        r = 0.0375
        d.ellipse([px(x - r), px(y - r), px(x + r), px(y + r)], fill=DIM if pos < walked else AMBER)
        pos += 0.17
    gx, gy = PATH[-1]
    rd = 0.25 + (0.03 * math.sin(pulse * math.pi * 2) if arrived else 0.0)
    d.ellipse([px(gx - rd), px(gy - rd), px(gx + rd), px(gy + rd)], fill=AMBER)
    door = DOOR.resize((px(0.26), px(0.26)))
    img.paste(door, (px(gx - 0.13), px(gy - 0.13)), door)
    if not arrived:
        x, y = point_at(walked)
        for k in range(2):  # sound rings pulsing out from the dot
            ph = (pulse + k / 2) % 1.0
            r = 0.15 + 0.2 * ph
            d.ellipse([px(x - r), px(y - r), px(x + r), px(y + r)],
                      outline=(*AMBER, int(220 * (1 - ph))), width=px(1.25 / 72))
        r = 0.12
        d.ellipse([px(x - r), px(y - r), px(x + r), px(y + r)], fill=AMBER, outline=PAPER,
                  width=px(2 / 72))
    bw, bh = PHONE_W_IN * 0.62, 0.62
    bx, by = at(0.33, 0.55)
    d.rounded_rectangle([px(bx), px(by), px(bx + bw), px(by + bh)], radius=px(0.12), fill=AMBER_SOFT)
    size = round(12 / 72 * PPI)  # 12 pt, as on the slide
    d.text((px(bx + 0.14), px(by + 0.09)), say[0], font=font("bold", size), fill=INK)
    d.text((px(bx + 0.14), px(by + 0.33)), say[1], font=font("body", size), fill=MUTED)
    return img.resize((px(W_IN) // SS, px(H_IN) // SS), Image.LANCZOS)


def steps_left(dist):
    n = max(1, math.ceil(dist / STEP_IN - 0.3))
    return f"in about {n} step{'s' if n > 1 else ''}"


def timeline():
    """(walked, bubble, pulse phase, arrived) per frame: right turn, left turn, then the door."""
    out, t, walked = [], 0.0, 0.0
    dt = 1 / FPS

    def add(w, say, arrived=False, n=1):
        nonlocal t
        for _ in range(n):
            out.append((w, say, (t * 1.1) % 1.0, arrived))
            t += dt

    corner1, corner2 = LEGS[0], LEGS[0] + LEGS[1]
    add(0.0, ("Turn right", steps_left(corner1)), n=int(0.9 * FPS))
    for side, end in (("right", corner1), ("left", corner2)):
        while walked < end:
            left = end - walked
            add(walked, (f"Turn {side}", "now" if left < STEP_IN * 0.5 else steps_left(left)))
            walked = min(end, walked + SPEED_IN * dt)
        add(end, (f"Turn {side}", "now"), n=int(0.6 * FPS))
    while walked < sum(LEGS):
        add(walked, ("Go straight", "to the entrance"))
        walked = min(sum(LEGS), walked + SPEED_IN * dt)
    add(sum(LEGS), ("You're at", "the entrance"), arrived=True, n=int(1.8 * FPS))
    return out


def main(out=OUT):
    frames = [frame(*f) for f in timeline()]
    palette = frames[0].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    gif[0].save(out, save_all=True, append_images=gif[1:], duration=round(1000 / FPS), loop=0,
                optimize=True, disposal=1)
    print("wrote", out, f"({len(gif)} frames, {out.stat().st_size // 1024} KB)")
    return out


if __name__ == "__main__":
    main()
