"""Devpost thumbnail (3:2) in the deck's style.

  uv run --no-project --with pillow python slides/make_thumbnail.py   # slides/devpost-thumbnail.png
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
W, H, SS = 1500, 1000, 2  # drawn at 2x, then downsampled for smooth edges
INK, INK_2 = (14, 23, 38), (27, 40, 61)
AMBER, PAPER, ON_INK, MUTED = (232, 150, 46), (255, 255, 255), (230, 235, 242), (154, 168, 187)

# Cambria and Calibri where installed; their metric twins (Caladea, Carlito) on Linux.
FONTS = {
    "head": ["Cambria Bold.ttf", "cambriab.ttf", "Caladea-Bold.ttf"],
    "body": ["Calibri.ttf", "calibri.ttf", "Carlito-Regular.ttf"],
    "bold": ["Calibri Bold.ttf", "calibrib.ttf", "Carlito-Bold.ttf"],
    "italic": ["Calibri Italic.ttf", "calibrii.ttf", "Carlito-Italic.ttf"],
}
FONT_DIRS = [Path("/usr/share/fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts",
             Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts")]


def font(kind, size):
    for name in FONTS[kind]:
        for root in FONT_DIRS:
            hits = list(root.rglob(name)) if root.exists() else []
            if hits:
                return ImageFont.truetype(str(hits[0]), size * SS)
    return ImageFont.load_default(size * SS)


def main(out=HERE / "devpost-thumbnail.png"):
    img = Image.new("RGB", (W * SS, H * SS), INK)
    cx, cy = 1150 * SS, 480 * SS
    glow = Image.new("L", img.size, 0)  # soft light behind the rings
    ImageDraw.Draw(glow).ellipse([cx - 300 * SS, cy - 300 * SS, cx + 300 * SS, cy + 300 * SS],
                                 fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(110 * SS))
    img = Image.composite(Image.new("RGB", img.size, INK_2), img, glow)
    d = ImageDraw.Draw(img, "RGBA")
    for k, r in enumerate((92, 145, 205, 265, 320)):  # sound leaving a speaker
        alpha = int(255 * max(0.25, 1 - 0.2 * k))
        d.ellipse([cx - r * SS, cy - r * SS, cx + r * SS, cy + r * SS],
                  outline=(*AMBER, alpha), width=4 * SS)
    d.ellipse([cx - 78 * SS, cy - 78 * SS, cx + 78 * SS, cy + 78 * SS], fill=AMBER)
    speaker = Image.open(HERE / "icons" / "volume.png").convert("RGBA").resize((92 * SS, 92 * SS))
    img.paste(speaker, (cx - 46 * SS, cy - 46 * SS), speaker)

    x = 90 * SS
    d.text((x, 285 * SS), "Blindsight", font=font("head", 132), fill=PAPER)
    d.text((x, 470 * SS), "Voice-guided building tours", font=font("body", 46), fill=ON_INK)
    d.text((x, 530 * SS), "for blind and low-vision people", font=font("body", 46), fill=ON_INK)
    d.text((x, 630 * SS), "Built on CoStar's 3D scans", font=font("bold", 38), fill=AMBER)
    d.text((x, 860 * SS), "This is just the first step to a more visible future.",
           font=font("italic", 34), fill=MUTED)
    img.resize((W, H), Image.LANCZOS).save(out, optimize=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
