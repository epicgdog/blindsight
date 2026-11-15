"""Build the Insight pitch deck (Business and Enterprise track, Peri's Forward Deployed Challenge).

  uv run --no-project --with python-pptx --with lxml --with pillow python slides/build_insight_deck.py
  -> slides/insight.pptx

Reuses the drawing helpers and colours of slides/build_deck.py. Every number cites a source
on the last slide. Fill PERI below from the real conversation to show the consulted business.
"""

import argparse
import math
from pathlib import Path

from build_deck import (
    AMBER,
    AMBER_SOFT,
    GREEN,
    HEAD,
    INK,
    INK_2,
    LINE,
    MARGIN,
    MIST,
    MUTED,
    ON_INK,
    ON_INK_MUTED,
    PAPER,
    TEXT,
    H,
    W,
    arrow,
    chip,
    chip_width,
    icon,
    line,
    new_slide,
    notes,
    oval,
    rect,
    rings,
    text,
    title,
)
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Pt

HERE = Path(__file__).resolve().parent
NAME = "Insight"
LINE_ONE = "Indoor navigation on your phone."
TAGLINE = "This is just the first step to a more visible future."

# Peri's Forward Deployed Challenge: a real business consulted this weekend. Fill all five from
# the actual conversation (short phrases); the slide shows them only when every one is set.
PERI = {"business": "", "person": "", "role": "", "need": "", "evidence": ""}

SOURCES = [
    ("U.S. EPA, Indoor Air Quality research",
     "Americans spend approximately 90 percent of their time indoors",
     "https://www.epa.gov/air-research/indoor-air-quality-exposure-and-characterization-research"),
    ("U.S. Bureau of Transportation Statistics, Passengers (2024)",
     "1.1 billion passengers on U.S. airlines in 2024",
     "https://data.bts.gov/stories/s/Passengers/itj6-vfiw/"),
    ("Institute of International Education, Open Doors 2025",
     "1,177,766 international students in the United States in 2024/25",
     "https://www.iie.org/news/open-doors-2025-press-release/"),
    ("Transportation Research Board, ACRP Research Report 161: Guidelines for Improving Airport "
     "Services for International Customers",
     "1,000 passengers surveyed at the top eight U.S. gateway airports ranked finding their way "
     "as a top priority",
     "https://trb.org/Main/Blurbs/175168.aspx"),
    ("HackPrinceton F26 Main Track and F26 Special Tracks documents",
     "Track descriptions and judging criteria", None),
]


def cite(*n):
    return "[" + ", ".join(str(k) for k in n) + "]"


# ---------------------------------------------------------------- a phone with the route
def phone(s, x, y, w, h, dark=True, bubble=("Turn right", "in 4 steps")):
    """Phone outline with a dotted route from 'you' to a door and a voice bubble."""
    body = rect(s, x, y, w, h, INK_2 if dark else MIST, radius=0.16,
                line=PAPER if dark else LINE, name="Phone")
    body.line.width = Pt(2.5)
    rect(s, x + w / 2 - 0.22, y + 0.12, 0.44, 0.06, ON_INK_MUTED, radius=0.5, name="Phone slot")
    start = (x + w * 0.24, y + h * 0.82)
    corner = (x + w * 0.24, y + h * 0.42)
    turn = (x + w * 0.76, y + h * 0.42)
    goal = (x + w * 0.76, y + h * 0.22)
    pts = [start, corner, turn, goal]
    for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
        n = max(1, int(math.dist((ax, ay), (bx, by)) / 0.17))
        for i in range(n):
            f = i / n
            oval(s, ax + (bx - ax) * f, ay + (by - ay) * f, 0.075, fill=AMBER, name="route dot")
    rings(s, *start, (0.42, 0.62), width=1.25)
    you = oval(s, *start, 0.24, fill=AMBER, outline=PAPER, width=2.0, name="You")
    you.line.width = Pt(2)
    icon(s, "door", *goal, 0.5)
    bw, bh = w * 0.62, 0.62
    bx, by = x + w * 0.33, y + h * 0.55
    rect(s, bx, by, bw, bh, AMBER_SOFT, radius=0.2, name="Voice bubble")
    text(s, bx + 0.14, by + 0.08, bw - 0.2, bh - 0.12,
         [[(bubble[0], {"bold": True, "color": INK})], [(bubble[1], {"color": MUTED})]],
         size=12, space_after=0, line=1.0, name="Bubble text")


# ---------------------------------------------------------------- slides
def s_title(prs):
    s = new_slide(prs, dark=True)
    text(s, MARGIN, 2.1, 7.5, 1.3, NAME, size=80, bold=True, font=HEAD, color=PAPER, name="Title")
    text(s, MARGIN, 3.55, 7.2, 1.0, LINE_ONE, size=28, color=ON_INK, name="Line")
    text(s, MARGIN, 6.45, 9.0, 0.4,
         [[("HackPrinceton 2026", {"bold": True, "color": AMBER}),
           ("   Business and Enterprise track  ·  Forward Deployed Challenge", {})]],
         size=14, color=ON_INK_MUTED, name="Event")
    phone(s, 9.2, 0.85, 3.0, 5.8)
    notes(s, "Open with the story, not the team.")
    return s


def s_story(prs):
    s = new_slide(prs)
    title(s, "Why we're building Insight", sub="Two stories from our own lives.")
    cards = [
        ("plane", "Gerard got lost and missed his flight",
         "The maps on his phone got him to the airport. Inside the terminal they were no help, "
         "and his flight left without him."),
        ("language", "Jeeyan is about to study in South Korea",
         "A semester abroad this spring: train stations, stores and campus buildings, with "
         "signs in a language Jeeyan doesn't speak."),
    ]
    w, gap, top = 5.95, 0.2, 1.95
    for k, (ico, head, body) in enumerate(cards):
        x = MARGIN + k * (w + gap)
        rect(s, x, top, w, 3.3, MIST, name=f"{head[:20]} card")
        icon(s, ico, x + 0.85, top + 0.85, 0.95)
        text(s, x + 0.4, top + 1.55, w - 0.8, 0.5, head, size=20, bold=True, name=f"{head[:20]} head")
        text(s, x + 0.4, top + 2.1, w - 0.8, 1.05, body, size=15, color=MUTED,
             name=f"{head[:20]} body")
    text(s, MARGIN, 5.5, 12.1, 0.4, "Signs and maps only help if you can read them, and if you can "
         "find them. Travelers say finding their way is a top priority at U.S. airports " + cite(4)
         + ".", size=14, color=MUTED, name="Context")
    text(s, MARGIN, 6.05, 12.1, 0.6, [[("What if every building could talk you through it, so you "
                                        "never had to read a sign?",
                                        {"italic": True, "bold": True, "color": AMBER})]],
         size=22, name="Hook")
    notes(s, "Tell both stories in your own words. Gerard got lost inside an airport and missed his "
          "flight: phone maps stop at the door. Jeeyan leaves this spring for a semester in South "
          "Korea, where the signs will be in a language Jeeyan doesn't speak. Other people have "
          "tried indoor navigation; we're building it for moments we've lived.")
    return s


def s_scale(prs):
    s = new_slide(prs)
    title(s, "Indoors is where we live, and where maps stop")
    stats = [
        ("90%", "of Americans' time is spent indoors", "U.S. EPA " + cite(1)),
        ("1.1B", "passengers on U.S. airlines in 2024",
         "Bureau of Transportation Statistics " + cite(2)),
    ]
    w, top = 3.85, 2.0
    for k, (big, words, ref) in enumerate(stats):
        x = MARGIN + k * (w + 0.275)
        rect(s, x, top, w, 3.6, MIST, name=f"{big} card")
        text(s, x + 0.35, top + 0.35, w - 0.7, 1.0, big, size=54, bold=True, font=HEAD, color=AMBER,
             name=f"{big} number")
        text(s, x + 0.35, top + 1.5, w - 0.7, 1.4, words, size=16, color=TEXT, name=f"{big} words")
        text(s, x + 0.35, top + 3.05, w - 0.7, 0.4, ref, size=10, color=MUTED, name=f"{big} source")
    x = MARGIN + 2 * (w + 0.275)
    rect(s, x, top, w, 3.6, INK, name="GPS card")
    icon(s, "wifi", x + 0.85, top + 0.85, 0.95)
    text(s, x + 0.35, top + 1.6, w - 0.7, 0.5, "No GPS inside", size=20, bold=True, color=PAPER,
         name="GPS head")
    text(s, x + 0.35, top + 2.15, w - 0.7, 1.3, "Satellite positioning is unreliable inside "
         "buildings, so phone maps can't lead you to a gate, a ward or a room.", size=15,
         color=ON_INK_MUTED, name="GPS body")
    notes(s, "We spend about 90 percent of our time indoors, and more than a billion people flew "
          "on U.S. airlines last year. Indoors is exactly where phone maps stop working.")
    return s


def s_options(prs):
    s = new_slide(prs)
    title(s, "What venues use today", sub="Each solves part of it. None gives everyone turn-by-turn "
          "directions without new hardware.")
    cols = ["Beacons and Wi-Fi positioning", "Signs, maps and kiosks", "Staff and info desks", NAME]
    rows = [
        ("Turn-by-turn on your phone", ["yes", "no", "no", "yes"]),
        ("No new hardware in the building", ["no", "some", "yes", "yes"]),
        ("Works without asking anyone", ["yes", "yes", "no", "yes"]),
        ("Hands-free, by voice", ["some", "no", "yes", "yes"]),
    ]
    x0, cw, top, rh = 4.75, 2.0, 1.95, 0.82
    rect(s, x0 + 3 * cw + 0.05, top - 0.05, cw - 0.1, 0.75 + rh * len(rows), AMBER_SOFT,
         name="Insight column")
    for k, name in enumerate(cols):
        text(s, x0 + k * cw + 0.1, top + 0.02, cw - 0.2, 0.66, name, size=14, bold=True, color=TEXT,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name=f"{name} header")
    marks = {"yes": ("check", GREEN), "no": ("x", RGBColor(0xB4, 0xBE, 0xCA)), "some": ("minus", AMBER)}
    for r, (label, vals) in enumerate(rows):
        y = top + 0.75 + r * rh
        line(s, MARGIN, y, x0 + 3 * cw, y, LINE, 1)
        text(s, MARGIN, y, x0 - MARGIN - 0.2, rh, label, size=17, color=TEXT, anchor=MSO_ANCHOR.MIDDLE,
             name=f"{label} label")
        for k, v in enumerate(vals):
            ico, fill = marks[v]
            icon(s, ico, x0 + k * cw + cw / 2, y + rh / 2, 0.46, circle=fill)
    text(s, MARGIN, 6.3, 12.1, 0.4, [[("Some", {"bold": True, "color": AMBER}),
                                       (": depends on the venue or the app.", {})]],
         size=13, color=MUTED, name="Legend")
    notes(s, "Beacons need hardware installed and maintained. Signs and kiosks don't give "
          "turn-by-turn directions. Staff can, but only if you find them. Insight needs nothing "
          "but the visitor's phone.")
    return s


def s_solution(prs):
    s = new_slide(prs)
    title(s, "Insight: Google Maps for indoors",
          sub="Hold your phone up and walk. Insight recognises where you are and talks you there.")
    beats = [
        ("pin", "Start", "“I know the way to the entrance, about 40 steps. Walk straight ahead.”"),
        ("route", "Turns", "“In about 4 steps, turn right.” Then: “Turn right now.”"),
        ("compass", "Drift", "“Turn slightly left.”"),
        ("door", "Arrive", "“You're at the entrance.”"),
    ]
    w, gap, top = 2.85, 0.233, 2.0
    for k, (ico, head, quote) in enumerate(beats):
        x = MARGIN + k * (w + gap)
        rect(s, x, top, w, 3.2, MIST, name=f"{head} card")
        icon(s, ico, x + 0.62, top + 0.65, 0.8)
        text(s, x + 0.3, top + 1.3, w - 0.6, 0.5, head, size=19, bold=True, name=f"{head} head")
        text(s, x + 0.3, top + 1.85, w - 0.6, 1.25, [[(quote, {"italic": True})]], size=15,
             color=TEXT, name=f"{head} quote")
    text(s, MARGIN, 5.6, 12.1, 0.9,
         [[("Also: ", {"bold": True, "color": TEXT}),
           ("it says stop when a person or an obstacle is close ahead, and you can just ask: "
            "“Where am I?”, “What's around me?”", {})]],
         size=16, color=MUTED, name="Also")
    notes(s, "Quotes are the exact wording the system uses today. The person needs nothing but "
          "their phone.")
    return s


def s_how(prs):
    s = new_slide(prs)
    title(s, "How it works", sub="Teach a route once. Every phone can follow it.")
    steps = [
        ("record", "1  Teach", "A staff member walks the route once with the app. The recording "
         "becomes the map."),
        ("route", "2  Recognise", "Each visitor's camera view is matched to that walk: how far "
         "along you are, and which way to face."),
        ("mic", "3  Speak", "A Gemini Live voice agent turns it into short directions and answers "
         "questions."),
    ]
    w, top = 3.6, 2.0
    for k, (ico, head, body) in enumerate(steps):
        x = MARGIN + k * (w + 0.65)
        rect(s, x, top, w, 2.9, MIST, name=f"{head} card")
        icon(s, ico, x + 0.62, top + 0.65, 0.8)
        text(s, x + 0.3, top + 1.3, w - 0.6, 0.5, head, size=20, bold=True, name=f"{head} head")
        text(s, x + 0.3, top + 1.85, w - 0.6, 1.0, body, size=14, color=MUTED, name=f"{head} body")
        if k < 2:
            arrow(s, x + w + 0.1, top + 1.45, x + w + 0.55, top + 1.45)
    chips = ["No beacons", "No 3D scans", "No maps to draw", "Any phone with a camera"]
    cx = MARGIN
    for label in chips:
        chip(s, cx, 5.25, label, INK, PAPER)
        cx += chip_width(label) + 0.2
    text(s, MARGIN, 5.9, 12.1, 0.8,
         [[("Status: ", {"bold": True, "color": TEXT}),
           ("built this weekend and tested on simulated walks, including a walk 30 cm off the "
            "taught path with the phone wobbling. Real-phone testing is our next step.", {})]],
         size=14, color=MUTED, name="Status")
    notes(s, "Under the hood: SIFT features and a RANSAC check against keyframes of the taught "
          "walk, the compass for where the turns are, the step counter when the camera loses the "
          "view. Say the status line honestly if asked.")
    return s


def s_demo(prs):
    s = new_slide(prs, dark=True)
    title(s, "Live demo: from here to the entrance", dark=True,
          sub="We taught this route before the pitch by walking it once.")
    beats = [("pin", "01", "Hold the phone up: “I know the way.”"),
             ("route", "02", "Walk: “In about 4 steps, turn right.”"),
             ("door", "03", "Arrive: “You're at the entrance.”")]
    w, top = 3.85, 2.3
    for k, (ico, num, words) in enumerate(beats):
        x = MARGIN + k * (w + 0.275)
        rect(s, x, top, w, 2.6, INK_2, name=f"Beat {num}")
        icon(s, ico, x + 0.62, top + 0.65, 0.8)
        text(s, x + 1.2, top + 0.45, 1.0, 0.4, num, size=14, bold=True, color=ON_INK_MUTED,
             name=f"Beat {num} number")
        text(s, x + 0.3, top + 1.35, w - 0.6, 1.1, [[(words, {"italic": True})]], size=17,
             color=AMBER, name=f"Beat {num} words")
    text(s, MARGIN, 5.4, 12.1, 0.6, "If the live demo fails, we play a recording of the same walk.",
         size=15, color=ON_INK, name="Fallback")
    notes(s, "One person talks, one walks with the phone, one watches the laptop.")
    return s


def s_model(prs):
    s = new_slide(prs)
    title(s, "How we make money", sub="Venues pay. Visitors use it free.")
    cards = [
        ("briefcase", "Who pays", "Airports, universities, train stations, malls, offices and event "
         "venues: anyone whose visitors get lost."),
        ("dollar", "What they pay", "A setup fee while we walk and teach their routes, then a "
         "monthly subscription per building."),
        ("chart", "What they get", "Fewer “where is…?” questions at the desk, visitors "
         "on time, and (next) a dashboard of where people get lost."),
    ]
    w, top = 3.85, 2.0
    for k, (ico, head, body) in enumerate(cards):
        x = MARGIN + k * (w + 0.275)
        rect(s, x, top, w, 3.55, MIST, name=f"{head} card")
        icon(s, ico, x + 0.85, top + 0.85, 0.95)
        text(s, x + 0.4, top + 1.6, w - 0.8, 0.5, head, size=19, bold=True, name=f"{head} head")
        text(s, x + 0.4, top + 2.15, w - 0.8, 1.3, body, size=15, color=MUTED, name=f"{head} body")
    text(s, MARGIN, 5.9, 12.1, 0.6,
         [[("Pricing ", {"bold": True, "color": TEXT}),
           ("will be set with our first pilots.", {})]],
         size=15, color=MUTED, name="Pricing note")
    notes(s, "B2B: the venue is the customer, the visitor is the user. The analytics dashboard is "
          "planned, not built.")
    return s


def s_forward(prs):
    s = new_slide(prs)
    title(s, "Forward deployed: we set it up with you",
          sub="How we work with a venue, from day one.")
    steps = [("desk", "Shadow the desk", "Log the questions visitors ask most, for a day."),
             ("record", "Teach the routes", "Walk the most-asked routes with the app."),
             ("clock", "Pilot for 30 days", "Count desk questions and late arrivals, before and after."),
             ("trend", "Expand", "More routes, more buildings, then the dashboard.")]
    w, gap, top = 2.85, 0.233, 2.0
    for k, (_ico, head, body) in enumerate(steps):
        x = MARGIN + k * (w + gap)
        rect(s, x, top, w, 2.5, MIST, name=f"{head} card")
        oval(s, x + 0.55, top + 0.55, 0.62, fill=AMBER if k == 0 else INK, name=f"{head} number badge")
        text(s, x + 0.24, top + 0.24, 0.62, 0.62, str(k + 1), size=20, bold=True, color=PAPER,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name=f"{head} number")
        text(s, x + 0.3, top + 1.05, w - 0.6, 0.5, head, size=18, bold=True, name=f"{head} head")
        text(s, x + 0.3, top + 1.55, w - 0.6, 0.9, body, size=14, color=MUTED, name=f"{head} body")
    if all(PERI.values()):
        rect(s, MARGIN, 4.85, 12.1, 1.7, AMBER_SOFT, name="Consulted card")
        icon(s, "users", MARGIN + 0.6, 5.7, 0.8, circle=INK)
        text(s, MARGIN + 1.25, 4.98, 10.6, 1.5,
             [[("This weekend we worked with ", {}), (PERI["business"], {"bold": True}),
               (f": {PERI['person']}, {PERI['role']}.", {})],
              [("Their need: ", {"bold": True}), (PERI["need"], {})],
              [("Evidence: ", {"bold": True}), (PERI["evidence"], {})]],
             size=15, color=TEXT, space_after=2, anchor=MSO_ANCHOR.MIDDLE, name="Consulted text")
    else:
        text(s, MARGIN, 4.95, 12.1, 0.6, [[("Teaching a route takes about as long as walking it.",
                                            {"italic": True})]], size=16, color=MUTED,
             name="Teach note")
    notes(s, "Peri's track: name the business you consulted, the person and their role, what "
          "they need, and show the evidence. Fill PERI in slides/build_insight_deck.py.")
    return s


def s_customers(prs):
    s = new_slide(prs)
    title(s, "Where we start", sub="Airports first, then universities: the places in our own stories.")
    stats = [("1.1B", "passengers on U.S. airlines in 2024", "Bureau of Transportation Statistics "
              + cite(2)),
             ("1.2M", "international students at U.S. colleges and universities, 2024/25",
              "Institute of International Education, Open Doors 2025 " + cite(3))]
    for k, (big, small, ref) in enumerate(stats):
        y = 1.95 + k * 2.3
        text(s, MARGIN, y, 5.6, 0.95, big, size=50, bold=True, font=HEAD, color=AMBER,
             name=f"{big} number")
        text(s, MARGIN, y + 0.95, 5.6, 0.75, small, size=16, color=TEXT, name=f"{big} words")
        text(s, MARGIN, y + 1.68, 5.6, 0.3, ref, size=10, color=MUTED, name=f"{big} source")
    order = [("plane", "1  Airports", "Gates, connections and terminals you've never seen, with a "
              "clock running. Gerard missed his flight in one."),
             ("school", "2  Universities", "International students, students abroad like Jeeyan, "
              "first days and visitors: new buildings, often in a new language."),
             ("pin", "3  Then everywhere else", "Train stations, malls, offices and event venues.")]
    for k, (ico, head, body) in enumerate(order):
        y = 1.95 + k * 1.55
        rect(s, 6.75, y, 6.0, 1.35, MIST, name=f"{head} card")
        icon(s, ico, 7.3, y + 0.67, 0.72)
        text(s, 7.85, y + 0.15, 4.75, 0.45, head, size=17, bold=True, name=f"{head} head")
        text(s, 7.85, y + 0.6, 4.75, 0.7, body, size=13, color=MUTED, name=f"{head} body")
    notes(s, "We start where our stories are. Airports: Gerard missed his flight in one, and more "
          "than a billion people flew on U.S. airlines last year. Universities: over a million "
          "international students in the U.S., and students like Jeeyan heading abroad.")
    return s


def s_edge(prs):
    s = new_slide(prs)
    title(s, "Why us, and why Insight")
    rows = [("users", "We've lived it", "Gerard missed a flight. Jeeyan is about to study abroad. "
             "We build for moments we know."),
            ("dollar", "Cheap to set up", "No beacons and no scanning. Walking a route teaches it."),
            ("phone", "Nothing to install", "Camera, compass and step counter on the phone in your "
             "pocket."),
            ("language", "No signs to read", "It recognises the place itself, so it works in any "
             "building, in any country."),
            ("mic", "Voice first", "Hands-free for everyone, and usable by blind and low-vision "
             "visitors."),
            ("chart", "Data venues want", "Where people get lost and what they look for (planned "
             "dashboard).")]
    w, h = 3.95, 2.1
    for k, (ico, head, body) in enumerate(rows):
        x = MARGIN + (k % 3) * (w + 0.125)
        y = 1.95 + (k // 3) * (h + 0.2)
        rect(s, x, y, w, h, AMBER_SOFT if k == 0 else MIST, name=f"{head} card")
        icon(s, ico, x + 0.6, y + 0.6, 0.72)
        text(s, x + 0.3, y + 1.05, w - 0.5, 0.4, head, size=17, bold=True, name=f"{head} head")
        text(s, x + 0.3, y + 1.43, w - 0.5, 0.65, body, size=13, color=MUTED, name=f"{head} body")
    notes(s, "Other teams can build indoor navigation. Our edge is that we've lived the problem, and "
          "that a route is taught by walking it, not by installing hardware or scanning the "
          "building. It never reads a sign, so it works the same in South Korea as in Princeton.")
    return s


def s_next(prs):
    s = new_slide(prs)
    title(s, "What's next", sub="In order. None of this is in the demo yet.")
    cols = [("1", "Next weeks", "Prove it", ["Test on real phones in one building",
                                            "Directions in your own language",
                                            "UI and UX: pick a destination, preview the route",
                                            "Haptic cues alongside the voice"]),
            ("2", "Next months", "Make it a product", ["Run fully on the phone, no laptop",
                                                       "Many routes per building, shared",
                                                       "The venue dashboard"]),
            ("3", "Then", "Grow", ["Paid pilots with an airport and a university",
                                   "Apply to Y Combinator",
                                   "Build Insight full time"])]
    w, top = 3.85, 1.95
    for k, (num, when, goal, items) in enumerate(cols):
        x = MARGIN + k * (w + 0.275)
        rect(s, x, top, w, 4.6, MIST, name=f"{when} card")
        oval(s, x + 0.6, top + 0.6, 0.62, fill=AMBER if k == 0 else INK, name=f"{when} badge")
        text(s, x + 0.29, top + 0.29, 0.62, 0.62, num, size=20, bold=True, color=PAPER,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name=f"{when} number")
        text(s, x + 1.1, top + 0.28, w - 1.3, 0.35, when.upper(), size=11, bold=True, color=MUTED,
             name=f"{when} label")
        text(s, x + 1.1, top + 0.58, w - 1.3, 0.45, goal, size=19, bold=True, name=f"{when} goal")
        for i, item in enumerate(items):
            y = top + 1.4 + i * (0.95 if max(len(c[3]) for c in cols) < 4 else 0.76)
            oval(s, x + 0.42, y + 0.17, 0.12, fill=AMBER, name="item dot")
            text(s, x + 0.65, y, w - 0.9, 0.8, item, size=15, color=TEXT, name=item[:20])
    notes(s, "Be clear that these are next steps.")
    return s


def s_close(prs):
    s = new_slide(prs, dark=True)
    text(s, MARGIN, 2.0, 7.5, 1.3, NAME, size=72, bold=True, font=HEAD, color=PAPER, name="Name")
    text(s, MARGIN, 3.35, 7.5, 0.8, LINE_ONE, size=28, color=ON_INK, name="Line")
    text(s, MARGIN, 4.6, 7.8, 1.2, [[(TAGLINE, {"italic": True})]], size=22, color=AMBER,
         name="Tagline")
    phone(s, 9.2, 0.85, 3.0, 5.8, bubble=("You're at", "the entrance"))
    notes(s, "Insight: indoor navigation on your phone. " + TAGLINE)
    return s


def s_sources(prs):
    s = new_slide(prs)
    title(s, "Sources", sub="Every number in this deck, where it comes from. Checked on "
          "November 15, 2026.")
    top, rh = 1.95, 0.78
    for k, (who, what, url) in enumerate(SOURCES):
        y = top + k * rh
        text(s, MARGIN, y, 0.5, rh, f"{k + 1}", size=16, bold=True, font=HEAD, color=AMBER,
             name=f"Source {k + 1} number")
        box = text(s, MARGIN + 0.55, y, 11.55, rh, [[(who, {"bold": True, "color": TEXT}),
                                                     ("  " + what, {})]],
                   size=12, color=MUTED, space_after=1, name=f"Source {k + 1}")
        if url:
            p = box.text_frame.add_paragraph()
            r = p.add_run()
            r.text = url
            r.font.name, r.font.size = "Calibri", Pt(10)
            r.font.color.rgb = RGBColor(0x1F, 0x5F, 0xAE)
            r.hyperlink.address = url
    notes(s, "Reference slide; do not present.")
    return s


SLIDES = (s_title, s_story, s_scale, s_options, s_solution, s_how, s_demo, s_model, s_forward,
          s_customers, s_edge, s_next, s_close, s_sources)


def build(out: Path) -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    prs.core_properties.title = NAME
    prs.core_properties.subject = LINE_ONE
    for make in SLIDES:
        make(prs)
    prs.save(out)
    return prs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Insight pitch deck.")
    ap.add_argument("--out", type=Path, default=HERE / "insight.pptx")
    a = ap.parse_args(argv)
    prs = build(a.out)
    print(f"wrote {a.out} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
