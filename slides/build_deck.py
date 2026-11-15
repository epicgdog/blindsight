"""Build the Blindsight pitch deck with python-pptx.

  uv run python3 slides/build_deck.py                 # slides/blindsight.pptx (in git, no imagery)
  uv run python3 slides/build_deck.py --presenting    # slides/blindsight-presenting.pptx (git-ignored)

The committed deck carries no Matterport or Vexcel imagery. --presenting adds whichever
of these local, git-ignored images exist (a slide keeps its drawn diagram otherwise):

  data/rabinowitz_combined.png      3D model + 360 photo of the demo building
  data/matterport/topdown.png       scan points from above
  data/vexcel/vexcel_rabinowitz.jpg aerial image for the street-to-door roadmap
  data/reports/viewer.png           a screenshot of the live viewer

Speaker notes follow slides/pitch_script.md. Numbers on the slides are counts taken from
the scan itself; the pitch quotes no accuracy or latency (see NOTES-part2.md).
"""

import argparse
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ICONS = HERE / "icons"
ONE = (
    "Blindsight lets blind and low-vision people tour a property independently, "
    "guided by voice, using CoStar 3D data."
)

# Night navy dominates (title, demo, close); white content slides; amber is the voice.
INK = RGBColor(0x0E, 0x17, 0x26)
INK_2 = RGBColor(0x1B, 0x28, 0x3D)
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
MIST = RGBColor(0xF1, 0xF4, 0xF8)
LINE = RGBColor(0xD5, 0xDC, 0xE5)
TEXT = RGBColor(0x14, 0x1E, 0x2B)
MUTED = RGBColor(0x5A, 0x67, 0x78)
ON_INK = RGBColor(0xE6, 0xEB, 0xF2)
ON_INK_MUTED = RGBColor(0x9A, 0xA8, 0xBB)
AMBER = RGBColor(0xE8, 0x96, 0x2E)
AMBER_SOFT = RGBColor(0xFB, 0xEB, 0xD5)
GREEN = RGBColor(0x2E, 0x8B, 0x57)
GREEN_SOFT = RGBColor(0xDF, 0xF1, 0xE6)
SLATE_SOFT = RGBColor(0xE4, 0xE8, 0xEE)
HEAD = "Cambria"
BODY = "Calibri"

W, H = Inches(13.333), Inches(7.5)
MARGIN = 0.6  # inches


# ---------------------------------------------------------------- primitives
def _rgb(shape_fill, color):
    shape_fill.solid()
    shape_fill.fore_color.rgb = color


def new_slide(prs, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _rgb(s.background.fill, INK if dark else PAPER)
    return s


def text(s, x, y, w, h, paras, *, size=16, color=TEXT, font=BODY, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, space_after=6, line=1.1, name=None):
    """paras: a string, or a list of paragraphs; a paragraph is a string or a list of
    (text, {size, color, bold, italic, font}) runs."""
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        box.name = name
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, para in enumerate([paras] if isinstance(paras, str) else paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        p.line_spacing = line
        for chunk, opts in [(para, {})] if isinstance(para, str) else para:
            r = p.add_run()
            r.text = chunk
            f = r.font
            f.name = opts.get("font", font)
            f.size = Pt(opts.get("size", size))
            f.bold = opts.get("bold", bold)
            f.italic = opts.get("italic", False)
            f.color.rgb = opts.get("color", color)
    return box


def title(s, words, dark=False, sub=None):
    """Same place, font and size on every content slide."""
    text(s, MARGIN, 0.5, 12.1, 0.8, words, size=36, bold=True, font=HEAD,
         color=PAPER if dark else TEXT, name="Title")
    if sub:
        text(s, MARGIN, 1.32, 12.1, 0.5, sub, size=18, color=ON_INK_MUTED if dark else MUTED,
             name="Subtitle")


def _plain(shape):
    """Drop the default theme style (it adds a shadow in some renderers)."""
    shape.shadow.inherit = False
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def chip_width(label):
    return 0.34 + 0.092 * len(label)


def rect(s, x, y, w, h, fill, radius=0.08, line=None, name=None):
    shape = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
                               Inches(x), Inches(y), Inches(w), Inches(h))
    if radius:
        shape.adjustments[0] = radius
    _rgb(shape.fill, fill)
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    _plain(shape)
    if name:
        shape.name = name
    return shape


def oval(s, cx, cy, d, fill=None, outline=None, width=1.5, name=None):
    shape = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(cx - d / 2), Inches(cy - d / 2),
                               Inches(d), Inches(d))
    if fill is None:
        shape.fill.background()
    else:
        _rgb(shape.fill, fill)
    if outline is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = outline
        shape.line.width = Pt(width)
    _plain(shape)
    if name:
        shape.name = name
    return shape


def icon(s, name, cx, cy, d, circle=AMBER, variant=""):
    """Line icon centred in a filled circle: the deck's one repeated motif."""
    if circle is not None:
        oval(s, cx, cy, d, fill=circle, name=f"{name} badge")
    size = d * (0.52 if circle is not None else 1.0)
    pic = s.shapes.add_picture(str(ICONS / f"{name}{variant}.png"), Inches(cx - size / 2),
                               Inches(cy - size / 2), Inches(size), Inches(size))
    pic.name = f"{name} icon"
    pic._element.nvPicPr.cNvPr.set("descr", "")  # decorative
    return pic


def chip(s, x, y, label, fill, color, w=None):
    w = w or chip_width(label)
    shape = rect(s, x, y, w, 0.34, fill, radius=0.5, name=f"{label} chip")
    tf = shape.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = Inches(0.06)
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.name, r.font.size, r.font.bold, r.font.color.rgb = BODY, Pt(11), True, color
    return shape


def arrow(s, x1, y1, x2, y2, color=MUTED, width=2.0):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")
    tail.set("w", "med")
    tail.set("len", "med")
    return c


def rings(s, cx, cy, sizes, color=AMBER, width=1.5):
    """Concentric rings fading outward, like sound leaving a speaker."""
    for k, d in enumerate(sizes):
        ring = oval(s, cx, cy, d, outline=color, width=width, name="sound ring")
        clr = ring.line._get_or_add_ln().find(qn("a:solidFill")).find(qn("a:srgbClr"))
        alpha = etree.SubElement(clr, qn("a:alpha"))
        alpha.set("val", str(int(100000 * max(0.25, 1 - 0.22 * k))))


def notes(s, words):
    s.notes_slide.notes_text_frame.text = words


def picture(s, path, x, y, w=None, h=None, alt=""):
    pic = s.shapes.add_picture(str(path), Inches(x), Inches(y),
                               Inches(w) if w else None, Inches(h) if h else None)
    pic._element.nvPicPr.cNvPr.set("descr", alt)
    return pic


def fit_picture(s, path, x, y, w, h, alt):
    """Largest picture of the image's aspect ratio inside the box, centred."""
    from PIL import Image

    iw, ih = Image.open(path).size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    return picture(s, path, x + (w - pw) / 2, y + (h - ph) / 2, pw, ph, alt)


def footer(s, label, dark=False):
    text(s, MARGIN, 7.0, 8, 0.3, label, size=10, color=ON_INK_MUTED if dark else MUTED,
         name="Footer")



# ---------------------------------------------------------------- slides
def s_title(prs, assets):
    s = new_slide(prs, dark=True)
    rings(s, 10.35, 3.75, (1.5, 2.6, 3.7, 4.8), width=1.75)
    icon(s, "volume", 10.35, 3.75, 1.1)
    text(s, MARGIN, 2.0, 7.6, 1.3, "Blindsight", size=72, bold=True, font=HEAD, color=PAPER,
         name="Title")
    text(s, MARGIN, 3.45, 7.0, 1.4, ONE, size=24, color=ON_INK, line=1.15, name="One sentence")
    text(s, MARGIN, 6.35, 8.5, 0.4,
         [[("HackPrinceton 2026", {"bold": True, "color": AMBER}),
           ("   CoStar track   ·   Demo building: Rabinowitz Hall, Princeton", {})]],
         size=14, color=ON_INK_MUTED, name="Event")
    notes(s, "Open with the problem, never with team names. Say the one sentence first.\n\n" + ONE)
    return s


def s_problem(prs, assets):
    s = new_slide(prs)
    title(s, "Touring a building assumes you can see it")
    cards = [
        ("image", "Listings are visual",
         "Photos, floor plans and 3D tours all have to be looked at."),
        ("compass", "New places are unknown",
         "Where is the door? What is in the way? Which room is this?"),
        ("users", "You need a guide",
         "Touring alone, on your own schedule, is not really an option."),
    ]
    w, gap, top = 3.85, 0.33, 2.0
    for k, (ico, head, body) in enumerate(cards):
        x = MARGIN + k * (w + gap)
        rect(s, x, top, w, 3.3, MIST, name=f"{head} card")
        icon(s, ico, x + 0.85, top + 0.85, 0.95)
        text(s, x + 0.4, top + 1.6, w - 0.8, 0.5, head, size=20, bold=True, name=f"{head} head")
        text(s, x + 0.4, top + 2.15, w - 0.8, 1.0, body, size=16, color=MUTED, name=f"{head} body")
    text(s, MARGIN, 5.85, 12.1, 0.6,
         [[("What if the 3D tour could talk you through the building?",
            {"italic": True, "color": AMBER, "bold": True})]], size=22, name="Hook")
    notes(s, "0:00 to 0:25, the problem.\n\n" + ONE + "\n\nIf you cannot see, you cannot browse "
          "listings: photos, floor plans and 3D tours all have to be looked at. Walking through "
          "an unfamiliar building alone is hard, so someone has to go with you.")
    return s


def s_insight(prs, assets):
    s = new_slide(prs)
    title(s, "CoStar has already mapped the inside",
          sub="Matterport 3D scans: every scan point is a 360° photo taken at a measured position.")
    stats = [("66", "scan points in the demo building"), ("2", "floors, one staircase"),
             ("1,584", "views the phone is matched against")]
    for k, (big, small) in enumerate(stats):
        y = 2.25 + k * 1.45
        text(s, MARGIN, y, 2.3, 0.9, big, size=54, bold=True, font=HEAD, color=AMBER,
             anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT, name=f"{small} number")
        text(s, MARGIN + 2.6, y + 0.1, 3.2, 0.7, small, size=17, color=TEXT,
             anchor=MSO_ANCHOR.MIDDLE, name=f"{small} label")
    text(s, MARGIN, 6.45, 6.0, 0.45, "Built for sighted buyers. Nobody had turned it into guidance.",
         size=16, bold=True, color=MUTED, name="Takeaway")
    box = (6.75, 1.95, 6.0, 4.95)
    shot = assets.get("model") or assets.get("topdown")
    if shot:
        rect(s, *box, MIST, name="Picture frame")
        fit_picture(s, shot, box[0] + 0.15, box[1] + 0.15, box[2] - 0.3, box[3] - 0.55,
                    "Rabinowitz Hall in Matterport: 3D model and scan points")
        text(s, box[0] + 0.15, box[1] + box[3] - 0.36, box[2] - 0.3, 0.3,
             "Rabinowitz Hall, Princeton. Imagery © Matterport", size=10, color=MUTED,
             name="Picture credit")
    else:
        _scan_map(s, *box)
    notes(s, "0:25 to 0:50, the insight.\n\nCoStar already holds a detailed 3D record of a huge "
          "number of spaces. Our demo building has 66 scan points on two floors; each is a 360 "
          "degree photo at a known position. It was built for sighted buyers. Nobody has turned "
          "it into guidance for people who cannot see.")
    return s


def _scan_map(s, x, y, w, h):
    """Drawn stand-in for the scan: scan points along corridors on two floors, joined by
    the stairs. An illustration, not the real layout."""
    rect(s, x, y, w, h, MIST, name="Scan illustration")
    floors = {
        "Floor 2": ([(0.08, 0.3), (0.24, 0.3), (0.4, 0.3), (0.56, 0.3), (0.72, 0.3), (0.88, 0.3),
                     (0.88, 0.72), (0.72, 0.72), (0.4, 0.72), (0.24, 0.72)],
                    [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (2, 8), (8, 9)]),
        "Floor 1": ([(0.08, 0.7), (0.22, 0.7), (0.36, 0.7), (0.5, 0.7), (0.64, 0.7), (0.64, 0.3),
                     (0.8, 0.3), (0.92, 0.3), (0.36, 0.3), (0.92, 0.7)],
                    [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (2, 8), (4, 9)]),
    }
    fw, fh = w - 0.6, 1.6
    centres = {}
    for k, (label, (pts, edges)) in enumerate(floors.items()):
        fy = y + 0.55 + k * 2.15
        text(s, x + 0.3, fy - 0.33, 2, 0.3, label, size=11, bold=True, color=MUTED,
             name=f"{label} label")
        rect(s, x + 0.3, fy, fw, fh, PAPER, radius=0.05, line=LINE, name=f"{label} outline")
        xy = [(x + 0.3 + px * fw, fy + py * fh) for px, py in pts]
        centres[label] = xy
        for a, b in edges:
            c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, *(Inches(v) for v in (*xy[a], *xy[b])))
            c.line.color.rgb, c.line.width = LINE, Pt(2)
        for cx, cy in xy:
            oval(s, cx, cy, 0.2, fill=AMBER, name="scan point")
    (ax, ay), (bx, by) = centres["Floor 2"][9], centres["Floor 1"][8]
    stairs = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(ax), Inches(ay + 0.1),
                                    Inches(bx), Inches(by - 0.1))
    stairs.line.color.rgb, stairs.line.width = MUTED, Pt(1.5)
    stairs.line.dash_style = MSO_LINE.DASH
    text(s, ax - 0.85, (ay + by) / 2 - 0.15, 0.7, 0.3, "stairs", size=10, color=MUTED,
         align=PP_ALIGN.RIGHT, name="Stairs label")
    text(s, x + 0.3, y + h - 0.38, w - 0.6, 0.3, "Illustration, not the real layout", size=10,
         color=MUTED, name="Illustration note")

def s_demo(prs, assets):
    s = new_slide(prs, dark=True)
    title(s, "Live demo: one phone, eyes closed", dark=True,
          sub="A blindfolded volunteer in Rabinowitz Hall. The room hears the phone; "
              "the laptop shows why it said it.")
    beats = [
        ("pin", "Where am I?", "“View matches the front door.”"),
        ("target", "What is around me?", "“2 people at 1 o'clock and a bench at 11 o'clock.”"),
        ("activity", "Heads up", "“Person directly ahead, getting closer.”"),
        ("mic", "Ask it", "Hold to talk: “How do I get to the entrance?”"),
    ]
    w, gap, top = 2.85, 0.233, 2.3
    for k, (ico, head, quote) in enumerate(beats):
        x = MARGIN + k * (w + gap)
        rect(s, x, top, w, 3.2, INK_2, name=f"{head} card")
        icon(s, ico, x + 0.62, top + 0.65, 0.8)
        text(s, x + 1.2, top + 0.42, w - 1.35, 0.5, f"0{k + 1}", size=14, bold=True,
             color=ON_INK_MUTED, anchor=MSO_ANCHOR.MIDDLE, name=f"{head} step")
        text(s, x + 0.3, top + 1.3, w - 0.6, 0.5, head, size=19, bold=True, color=PAPER,
             name=f"{head} head")
        text(s, x + 0.3, top + 1.85, w - 0.6, 1.25, [[(quote, {"italic": True})]], size=16,
             color=AMBER, name=f"{head} quote")
    shot = assets.get("viewer")
    caption = ("On the laptop: the live camera with detections, which scan matched and how sure, "
               "position on the floor plan, and every cue that was spoken.")
    text(s, MARGIN, 5.85, 12.1, 0.7, caption, size=16, color=ON_INK, name="Viewer caption")
    notes_extra = "\n\nThe next slide is a viewer screenshot, for when the demo fails." if shot else ""
    text(s, MARGIN, 6.55, 12.1, 0.35, "Cue wording is what the server says today; place names "
         "come from the photos we taught it.", size=11, color=ON_INK_MUTED, name="Wording note")
    notes(s, "0:50 to 2:00, live demo. One person talks, one drives the laptop, one guides the "
          "volunteer.\n\n[Volunteer is blindfolded at the first taught place, phone upright in "
          "portrait.] Our volunteer cannot see. The phone streams its camera to our laptop. "
          "Listen.\n[Location cue.] The laptop compared the live view with every scan of the "
          "building and confirmed the match with geometry.\n[Turn toward people or a bench: "
          "clock-direction cue.] That is object detection, spoken as clock directions.\n"
          "[Someone walks toward the volunteer: 'getting closer'.]\n[Optional, if the voice "
          "agent is running: hold to talk, 'How do I get to the entrance?']\nA person who cannot "
          "see just learned where they are and what is around them, in real time.\n\nIf the live "
          "demo fails: play slides/backup.mp4 (labelled SIMULATED) without apologizing."
          + notes_extra)
    if shot:
        s2 = new_slide(prs, dark=True)
        title(s2, "The judges' view", dark=True)
        fit_picture(s2, shot, MARGIN, 1.45, 12.1, 5.45, "Blindsight viewer during a walk")
        notes(s2, "Backup: the live viewer. Only in the presenting copy.")
    return s


def s_how(prs, assets):
    s = new_slide(prs)
    title(s, "How it works", sub="Phone in, voice out. The building's own scans are the map.")
    top = 2.15
    # phone
    rect(s, MARGIN, top + 0.55, 2.3, 2.5, MIST, name="Phone box")
    icon(s, "phone", MARGIN + 1.15, top + 1.2, 0.85)
    text(s, MARGIN + 0.2, top + 1.85, 1.9, 1.1, [[("iPhone", {"bold": True, "size": 18})],
         "Camera, tilt and compass"], size=14, color=MUTED, align=PP_ALIGN.CENTER, name="Phone text")
    arrow(s, MARGIN + 2.4, top + 1.8, 3.55, top + 1.8)
    # server
    sx, sw = 3.65, 6.05
    rect(s, sx, top, sw, 3.6, PAPER, line=LINE, name="Server box")
    text(s, sx + 0.3, top + 0.15, sw - 0.6, 0.35, "LAPTOP SERVER", size=11, bold=True, color=MUTED,
         name="Server label")
    stages = [
        ("target", "See", "YOLO11 finds people and obstacles in each frame"),
        ("pin", "Locate", "Shortlist likely scans, then confirm with matched points and geometry"),
        ("volume", "Speak", "Short cues with clock directions, paced so none are lost"),
    ]
    for k, (ico, head, body) in enumerate(stages):
        y = top + 0.65 + k * 0.98
        icon(s, ico, sx + 0.65, y + 0.38, 0.62)
        text(s, sx + 1.15, y + 0.05, sw - 1.4, 0.8,
             [[(head + "  ", {"bold": True, "size": 18, "color": TEXT}), (body, {})]],
             size=15, color=MUTED, anchor=MSO_ANCHOR.MIDDLE, name=f"{head} stage")
    arrow(s, sx + sw + 0.1, top + 1.8, 10.3, top + 1.8)
    # voice
    rect(s, 10.4, top + 0.55, 2.33, 2.5, AMBER_SOFT, name="Voice box")
    icon(s, "volume", 11.565, top + 1.2, 0.85)
    text(s, 10.55, top + 1.85, 2.03, 1.1, [[("Voice", {"bold": True, "size": 18})],
         "Spoken cues and answers"], size=14, color=MUTED, align=PP_ALIGN.CENTER,
         name="Voice text")
    # map source
    my = top + 3.95
    rect(s, sx, my, sw, 0.95, MIST, name="Map box")
    icon(s, "layers", sx + 0.65, my + 0.475, 0.62, circle=INK)
    text(s, sx + 1.15, my + 0.1, sw - 1.4, 0.75,
         [[("The map: ", {"bold": True, "color": TEXT}),
           ("CoStar / Matterport scans, cut into 1,584 reference views", {})]],
         size=15, color=MUTED, anchor=MSO_ANCHOR.MIDDLE, name="Map text")
    arrow(s, sx + 0.65, my - 0.02, sx + 0.65, top + 3.62, color=MUTED, width=1.5)
    rect(s, 10.4, my, 2.33, 0.95, MIST, name="Agent box")
    icon(s, "mic", 10.85, my + 0.475, 0.55)
    text(s, 11.25, my + 0.08, 1.4, 0.8, [[("Ask it", {"bold": True, "color": TEXT})],
         "Gemini voice agent"], size=13, color=MUTED, anchor=MSO_ANCHOR.MIDDLE, space_after=0,
         name="Agent text")
    notes(s, "2:00 to 2:35, how it works (one diagram only).\n\nPhone video goes to the laptop. "
          "We detect objects, match the frame against the building's scans with geometric "
          "verification, and speak short cues back. Hold to talk and a Gemini voice agent "
          "answers from what the system knows.")
    return s


def s_built(prs, assets):
    s = new_slide(prs)
    title(s, "What works today",
          sub="Built this weekend on Rabinowitz Hall. Status is what we have actually run.")
    tiles = [
        ("phone", "Phone app", "Streams the camera, speaks cues, and works through the silent switch.",
         "Working"),
        ("target", "Object cues", "People and furniture with counts, clock directions and "
         "“getting closer”.", "Working"),
        ("pin", "Which scan?", "Matches the view against all 66 scans and gives the "
         "heading. Accuracy so far measured on simulated views.", "Working"),
        ("mic", "Ask it", "Hold to talk: where am I, what is around, guide me to the entrance. "
         "Gemini agent.", "Demo"),
        ("map", "Floor plan", "Position from the 3D mesh, smoothed with the phone's "
         "motion sensors.", "Simulated"),
        ("monitor", "Judges' view", "Live camera, detections, place ranking, minimap and "
         "cue log.", "Working"),
    ]
    colors = {"Working": (GREEN_SOFT, GREEN), "Demo": (AMBER_SOFT, RGBColor(0x9A, 0x5B, 0x0B)),
              "Simulated": (SLATE_SOFT, MUTED)}
    w, h, gx, gy = 3.85, 2.0, 0.275, 0.25
    for k, (ico, head, body, status) in enumerate(tiles):
        x = MARGIN + (k % 3) * (w + gx)
        y = 1.95 + (k // 3) * (h + gy)
        rect(s, x, y, w, h, MIST, name=f"{head} tile")
        icon(s, ico, x + 0.55, y + 0.55, 0.62)
        text(s, x + 1.0, y + 0.3, w - 1.2, 0.5, head, size=17, bold=True,
             anchor=MSO_ANCHOR.MIDDLE, name=f"{head} head")
        text(s, x + 0.3, y + 0.95, w - 0.6, 0.95, body, size=14, color=MUTED, name=f"{head} body")
        chip(s, x + w - 0.25 - chip_width(status), y + 0.37, status, *colors[status])
    text(s, MARGIN, 6.55, 12.1, 0.4,
         [[("Honest status: ", {"bold": True, "color": TEXT}),
           ("no accuracy numbers from a real phone yet, and not yet tested with blind users.", {})]],
         size=14, color=MUTED, name="Honest status")
    notes(s, "Only if asked, or skip in the timed pitch. Working = we ran it end to end. Demo = "
          "built for the demo, limited (entrance guidance is not verified-safe navigation). "
          "Simulated = checked on simulated or rendered views only. Numbers: slides/qa.md.")
    return s


def s_roadmap(prs, assets):
    s = new_slide(prs)
    title(s, "Roadmap: from the street to the room",
          sub="Scan points become a graph of routes; Vexcel imagery covers the way to the door.")
    nodes = [("walk", "Street"), ("door", "Front door"), ("home", "Lobby"), ("map", "Any room")]
    xs = [1.55, 4.95, 8.35, 11.75]
    cy = 2.75
    for (ico, label), x in zip(nodes, xs, strict=True):
        icon(s, ico, x, cy, 1.0, circle=INK)
        text(s, x - 1.2, cy + 0.62, 2.4, 0.4, label, size=16, bold=True, align=PP_ALIGN.CENTER,
             name=f"{label} label")
    legs = [
        (0, "Vexcel aerial imagery guides the approach", "Roadmap"),
        (1, "Door handoff to the indoor tour", "Roadmap"),
        (2, "Scan graph routes, room to room", "Started"),
    ]
    for i, words, status in legs:
        x1, x2 = xs[i] + 0.6, xs[i + 1] - 0.6
        arrow(s, x1, cy, x2, cy, color=LINE, width=2.5)
        fill, color = (AMBER_SOFT, RGBColor(0x9A, 0x5B, 0x0B)) if status == "Started" else (
            SLATE_SOFT, MUTED)
        mid = (x1 + x2) / 2
        cw = chip_width(status)
        chip(s, mid - cw / 2, cy - 0.75, status, fill, color)
        text(s, x1 - 0.1, cy + 1.1, x2 - x1 + 0.2, 0.75, words, size=14, color=MUTED,
             align=PP_ALIGN.CENTER, name=f"{status} leg {i}")
    cards = [
        ("satellite", "Vexcel imagery",
         "We pulled aerial imagery of Rabinowitz Hall this weekend; guidance from it is not built."),
        ("compass", "Verified routes",
         "A demo route to the entrance runs on the scan graph. Safe routing needs a surveyed map."),
        ("users", "Blind users first",
         "Our first priority: sessions with blind and low-vision people before more features."),
    ]
    w, top = 3.85, 4.8
    for k, (ico, head, body) in enumerate(cards):
        x = MARGIN + k * (w + 0.275)
        rect(s, x, top, w, 1.95, MIST, name=f"{head} card")
        if k == 0 and assets.get("vexcel"):
            fit_picture(s, assets["vexcel"], x + 0.15, top + 0.15, 1.5, 1.65,
                        "Vexcel aerial image of Rabinowitz Hall")
            text(s, x + 1.8, top + 0.25, w - 1.95, 0.45, head, size=17, bold=True,
                 name="Vexcel head")
            text(s, x + 1.8, top + 0.72, w - 1.95, 0.85,
                 "Pulled this weekend. Guidance from it is not built yet.",
                 size=13, color=MUTED, name="Vexcel body")
            text(s, x + 1.8, top + 1.62, w - 1.95, 0.25, "Imagery © Vexcel", size=10,
                 color=MUTED, name="Vexcel credit")
            continue
        icon(s, ico, x + 0.55, top + 0.55, 0.62)
        text(s, x + 1.0, top + 0.3, w - 1.25, 0.5, head, size=17, bold=True,
             anchor=MSO_ANCHOR.MIDDLE, name=f"{head} head")
        text(s, x + 0.3, top + 0.95, w - 0.6, 0.95, body, size=14, color=MUTED, name=f"{head} body")
    notes(s, "Still 2:00 to 2:35.\n\nNext, scan points become a graph for room to room routes. "
          "Then Vexcel imagery guides someone from the street to the door, with a handoff to the "
          "indoor tour. That part is roadmap, not built. Our first priority is testing with "
          "blind users.")
    return s


def s_close(prs, assets):
    s = new_slide(prs, dark=True)
    title(s, "Why CoStar wins", dark=True)
    rows = [
        ("trend", "A new audience", "Blind and low-vision buyers and renters on CoStar's listing "
         "platforms."),
        ("lock", "A feature competitors cannot copy", "It runs on CoStar's 3D scan library. "
         "Without the data, there is no map."),
        ("layers", "No new mapping", "Scans that already exist become voice-guided tours."),
    ]
    for k, (ico, head, body) in enumerate(rows):
        y = 1.75 + k * 1.15
        icon(s, ico, MARGIN + 0.42, y + 0.42, 0.78)
        text(s, MARGIN + 1.15, y + 0.02, 10.8, 0.45, head, size=21, bold=True, color=PAPER,
             name=f"{head} head")
        text(s, MARGIN + 1.15, y + 0.5, 10.8, 0.45, body, size=16, color=ON_INK_MUTED,
             name=f"{head} body")
    rings(s, 11.55, 5.95, (1.0, 1.55, 2.1), width=1.25)
    icon(s, "volume", 11.55, 5.95, 0.62)
    text(s, MARGIN, 5.35, 9.6, 1.3, ONE, size=24, bold=True, font=HEAD, color=AMBER, line=1.1,
         name="One sentence")
    notes(s, "2:35 to 3:00.\n\nThis gives CoStar a new audience on its listing platforms, and a "
          "feature competitors cannot copy without this data.\n\n" + ONE)
    return s


# ---------------------------------------------------------------- build
ASSETS = {
    "model": Path("data/rabinowitz_combined.png"),
    "topdown": Path("data/matterport/topdown.png"),
    "vexcel": Path("data/vexcel/vexcel_rabinowitz.jpg"),
    "viewer": Path("data/reports/viewer.png"),
}
SLIDES = (s_title, s_problem, s_insight, s_demo, s_how, s_built, s_roadmap, s_close)


def build(out: Path, assets: dict) -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    prs.core_properties.title = "Blindsight"
    prs.core_properties.subject = ONE
    for make in SLIDES:
        make(prs, assets)
    prs.save(out)
    return prs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Blindsight pitch deck.")
    ap.add_argument("--presenting", action="store_true",
                    help="add local images from data/ (output is git-ignored)")
    ap.add_argument("--data-root", type=Path, default=ROOT, help="folder that holds data/")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    assets = {}
    if a.presenting:
        for key, rel in ASSETS.items():
            path = a.data_root / rel
            if path.is_file():
                assets[key] = path
            print(f"  {'using ' if path.is_file() else 'missing'} {rel}")
    out = a.out or HERE / ("blindsight-presenting.pptx" if a.presenting else "blindsight.pptx")
    prs = build(out, assets)
    print(f"wrote {out} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
