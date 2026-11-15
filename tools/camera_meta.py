"""Summarise the phone's camera metadata (data/camera_meta/*.jsonl, sent by the app every 20th frame).

Prints what the camera reports (model, lens, focal length, sizes) and the intrinsics it implies for
the frames the server actually decodes: focal length in pixels and the horizontal / vertical field
of view, to compare with HFOV_DEG (assumed 55).

Assumption: FocalLenIn35mmFilm is the 35 mm equivalent over the picture's diagonal (43.27 mm), as
Apple writes it for the captured framing. Cross-check against the image-based estimate:
    uv run python3 tools/calibrate.py --estimate-hfov data/onsite/pos

  uv run python3 tools/camera_meta.py              # newest session
  uv run python3 tools/camera_meta.py FILE.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAG_35MM = math.hypot(36.0, 24.0)  # 43.27 mm


def flat(exif: dict | None) -> dict:
    """iOS EXIF may be flat or nested ({Exif}, {TIFF}, ...): merge into one dict."""
    out: dict = {}
    for k, v in (exif or {}).items():
        if isinstance(v, dict):
            out.update({kk: vv for kk, vv in v.items() if not isinstance(vv, dict)})
        else:
            out[k] = v
    return out


def intrinsics(f35: float, w: int, h: int) -> dict:
    """Pinhole numbers for a w x h image whose 35 mm-equivalent focal length is f35."""
    f_px = math.hypot(w, h) * f35 / DIAG_35MM
    return {
        "f_px": round(f_px, 1),
        "hfov_deg": round(math.degrees(2 * math.atan(w / (2 * f_px))), 2),
        "vfov_deg": round(math.degrees(2 * math.atan(h / (2 * f_px))), 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", type=Path)
    a = ap.parse_args(argv)
    path = a.file
    if path is None:
        files = sorted((ROOT / "data" / "camera_meta").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            print("no data/camera_meta/*.jsonl yet: run the app against the server for a few seconds")
            return 1
        path = files[-1]
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    print(f"{path.name}: {len(rows)} samples")
    if not rows:
        return 1
    first = rows[0]
    ex = flat(first.get("exif"))
    keys = ("Make", "Model", "LensMake", "LensModel", "FocalLength", "FocalLenIn35mmFilm", "LensSpecification",
            "FNumber", "PixelXDimension", "PixelYDimension", "Orientation", "ExposureTime", "ISOSpeedRatings")
    for k in keys:
        if k in ex:
            print(f"  {k:<20} {ex[k]}")
    print(f"  {'photo (as taken)':<20} {first.get('photo')}")
    print(f"  {'sent to server':<20} {first.get('sent')}")
    print(f"  {'requested size':<20} {first.get('picture_size')}")
    print(f"  {'available sizes':<20} {first.get('available_sizes')}")
    others = sorted(set(ex) - set(keys))
    print(f"  other EXIF keys: {', '.join(others) or '-'}")

    # Does anything change between samples (lens switch, zoom, size)?
    for k in ("FocalLength", "FocalLenIn35mmFilm", "LensModel"):
        vals = Counter(str(flat(r.get("exif")).get(k)) for r in rows)
        if len(vals) > 1:
            print(f"  NOTE {k} varies: {dict(vals)}")
    sizes = Counter(json.dumps(r.get("sent")) for r in rows)
    if len(sizes) > 1:
        print(f"  NOTE sent size varies: {dict(sizes)}")

    f35 = ex.get("FocalLenIn35mmFilm")
    sent = first.get("sent") or {}
    if f35 and sent.get("width") and sent.get("height"):
        w, h = int(sent["width"]), int(sent["height"])
        k = intrinsics(float(f35), w, h)
        print(f"\nIntrinsics for the {w}x{h} frames the server decodes (from {f35} mm equivalent):")
        print(f"  fx = fy = {k['f_px']} px, cx = {(w - 1) / 2}, cy = {(h - 1) / 2}")
        print(f"  horizontal FOV {k['hfov_deg']} deg, vertical FOV {k['vfov_deg']} deg   (server assumes HFOV_DEG=55)")
        print("  The localizer resizes frames to 360 px wide; FOV is unchanged by that.")
    else:
        print("\nNo FocalLenIn35mmFilm or sent size: cannot derive intrinsics from this sample.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
