"""Build slides/blindsight.pptx (minimal deck) with python-pptx.  Run: uv run python3 slides/build_deck.py"""
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

OUT = Path(__file__).with_name("blindsight.pptx")
ONE = "Blindsight lets blind and low-vision people tour a property independently, guided by voice, using CoStar 3D data."

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.33), Inches(7.5)


def slide(title, lines=(), note=None):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s.shapes.add_textbox(Inches(0.7), Inches(0.5), Inches(12), Inches(1.2)).text_frame
    tb.word_wrap = True
    tb.text = title
    tb.paragraphs[0].font.size, tb.paragraphs[0].font.bold = Pt(40), True
    body = s.shapes.add_textbox(Inches(0.7), Inches(2), Inches(12), Inches(4.5)).text_frame
    body.word_wrap = True
    for i, ln in enumerate(lines):
        p = body.paragraphs[0] if i == 0 else body.add_paragraph()
        p.text, p.font.size = ln, Pt(28)
    if note:
        s.notes_slide.notes_text_frame.text = note
    return s


def box(s, x, y, w, h, text, fill=(59, 130, 246)):
    sh = s.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid(); sh.fill.fore_color.rgb = RGBColor(*fill)
    sh.text_frame.text = text
    sh.text_frame.paragraphs[0].font.size = Pt(20)
    return sh


slide("Blindsight", [ONE])
slide("The problem", ["A person who cannot see cannot browse listings.", "Touring a space alone is risky."])
slide("The insight", ["CoStar already holds a detailed 3D record of a huge number of spaces.",
                      "Built for sighted buyers. Nobody has turned it into guidance."])
s = slide("How it works")
for i, t in enumerate(["Phone video", "Object detection", "Geometric match vs Matterport pano", "Spoken cues"]):
    box(s, 0.7 + i * 3.1, 3, 2.8, 1.5, t)
slide("Live demo", ["Blindfolded volunteer, one scan point: the main lobby.",
                    "Laptop shows detections, lock state, inliers, yaw, cue log."])
s = slide("Roadmap (not built): scan positions as graph nodes")
for i, t in enumerate(["Scan position A", "Scan position B", "Scan position C"]):
    box(s, 0.7 + i * 2.6, 2.6, 2.2, 1, t, (100, 100, 120))
box(s, 0.7, 4.4, 5, 1, "Vexcel imagery: street to the door", (180, 120, 30))
box(s, 6, 4.4, 5, 1, "Door handoff to the indoor tour", (180, 120, 30))
slide("Why CoStar wins", ["A new audience for its listing platforms.", "A feature competitors cannot copy without this data.", "", ONE])
prs.save(OUT)
print("wrote", OUT)
