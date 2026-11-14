"""Part 3 reference views: Matterport skybox faces -> perspective views at phone FOV.

Data layout (all under data/, Part 3 owns it):
  data/pano/pano.json                  selected pano: pano_id, label, position, others
  data/pano/<PANO_ID>/face_0..5.jpg    2k skybox faces (0 up, 1-4 horizon, 5 down)
  data/pano/<PANO_ID>/layout.json      verified face layout (written by --check)
  data/pano/<PANO_ID>/refs/            72 reference views + refs.json (written by --build)

Face order is never assumed: detect_layout() scores colour continuity across all 12
cube edges for every candidate arrangement and keeps the best one, and the contact
sheets are written so a person (or agent) can look at them.

CLI:
  python server/refs.py --check      verify layout, write contact sheet + equirect
  python server/refs.py --build      build the 72 reference views + contact sheet
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import cam_to_world, pixel_dirs_cam  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("P3_DATA_DIR", ROOT / "data"))

REF_YAWS = tuple(range(0, 360, 15))  # 24 headings
REF_PITCHES = (-10, 0, 10)  # 3 pitches -> 72 views
REF_SIZE = (360, 480)  # (w, h): portrait 3:4, the iPhone still/preview aspect
DEFAULT_HFOV = 55.0  # Part 1 base config HFOV_DEG; refs are rebuilt if it changes


def ref_name(yaw: float, pitch: float) -> str:
    return f"y{int(round(yaw)) % 360:03d}_p{int(round(pitch)):+03d}"


# ---------------------------------------------------------------- face layout
@dataclass
class CubeLayout:
    """Orientation of the six faces in the pano frame.

    horizon_yaws: yaw of the centre of faces 1..4. (0, 90, 180, 270) means faces go
    clockwise (turning right); (0, 270, 180, 90) means counter-clockwise.
    up_yaw / down_yaw: in-plane rotation of faces 0 / 5, as the yaw of a camera
    pitched to +90 / -90 degrees. 0 means the face edge that adjoins face 1 is the
    bottom edge of face 0 and the top edge of face 5 (the usual skybox convention).
    """

    horizon_yaws: tuple = (0.0, 90.0, 180.0, 270.0)
    up_yaw: float = 0.0
    down_yaw: float = 0.0
    # Faces stored left-right mirrored. Seams cannot detect this (a mirrored cube is a
    # consistent cube of the mirrored scene), so it is a manual override: set it in
    # layout.json if text reads backwards in contact_cross.jpg. The warm-up would also
    # fail to verify against a mirrored pano, since descriptors are not mirror-invariant.
    mirrored: bool = False
    seam_score: float | None = None  # mean abs colour step across matched edges
    seam_ratio: float | None = None  # matched / mismatched edge error, << 1 is good
    verified_by: str = ""

    def rotation(self, face: int) -> np.ndarray:
        if face == 0:
            return cam_to_world(self.up_yaw, 90.0)
        if face == 5:
            return cam_to_world(self.down_yaw, -90.0)
        return cam_to_world(self.horizon_yaws[face - 1], 0.0)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "CubeLayout":
        d = dict(d)
        d["horizon_yaws"] = tuple(d.get("horizon_yaws", (0, 90, 180, 270)))
        return cls(**d)


def load_faces(pano_dir: Path) -> list[np.ndarray]:
    """Six RGB faces; raises if any is missing or they are not equal-sized squares."""
    faces = []
    for i in range(6):
        p = Path(pano_dir) / f"face_{i}.jpg"
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"missing skybox face {p}")
        faces.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    shapes = {f.shape for f in faces}
    if len(shapes) != 1 or faces[0].shape[0] != faces[0].shape[1]:
        raise ValueError(f"faces must be six equal-sized squares, got {sorted(shapes)}")
    return faces


def remap_flat(img: np.ndarray, mx: np.ndarray, my: np.ndarray) -> np.ndarray:
    """cv2.remap for maps of any shape (remap caps each side at 32767 pixels)."""
    shp = mx.shape
    n = int(np.prod(shp))
    if mx.ndim == 2 and max(shp) < 32767:
        return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    cols = 1024
    pad = (-n) % cols
    fx = np.concatenate([mx.ravel(), np.zeros(pad, np.float32)]).reshape(-1, cols)
    fy = np.concatenate([my.ravel(), np.zeros(pad, np.float32)]).reshape(-1, cols)
    out = cv2.remap(img, fx, fy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    out = out.reshape(-1, *img.shape[2:])[:n]
    return out.reshape(*shp, *img.shape[2:])


class CubeSampler:
    """Samples the cube map in any world direction (bilinear, one remap per call)."""

    PAD = 2

    def __init__(self, faces: list[np.ndarray], layout: CubeLayout | None = None):
        self.layout = layout or CubeLayout()
        if self.layout.mirrored:
            faces = [np.ascontiguousarray(f[:, ::-1]) for f in faces]
        self.S = faces[0].shape[0]
        p = self.PAD
        padded = [cv2.copyMakeBorder(f, p, p, p, p, cv2.BORDER_REPLICATE) for f in faces]
        self.atlas = np.ascontiguousarray(np.vstack(padded))  # (6*(S+2p), S+2p, 3)
        self.Cs = np.stack([self.layout.rotation(k) for k in range(6)])  # (6, 3, 3)
        self.fwd = self.Cs[:, :, 2]  # (6, 3) face forward axes in world

    def _maps(self, dirs: np.ndarray, face: np.ndarray | None = None):
        shp = dirs.shape[:-1]
        d = dirs.reshape(-1, 3)
        k = np.argmax(d @ self.fwd.T, axis=1) if face is None else np.full(len(d), face)
        cam = np.einsum("nji,nj->ni", self.Cs[k], d)  # C_k^T d
        z = np.maximum(cam[:, 2], 1e-9)
        S, p = self.S, self.PAD
        px = (cam[:, 0] / z + 1.0) * S / 2.0 - 0.5
        py = (cam[:, 1] / z + 1.0) * S / 2.0 - 0.5
        if face is not None:
            px, py = np.clip(px, 0, S - 1), np.clip(py, 0, S - 1)
        mx = (px + p).astype(np.float32).reshape(shp)
        my = (k * (S + 2 * p) + p + py).astype(np.float32).reshape(shp)
        return mx, my

    def sample(self, dirs: np.ndarray, face: int | None = None) -> np.ndarray:
        """Colours for world directions dirs (..., 3). face forces sampling one face."""
        mx, my = self._maps(dirs, face)
        return remap_flat(self.atlas, mx, my)

    def render(self, yaw: float, pitch: float = 0.0, roll: float = 0.0,
               size: tuple[int, int] = REF_SIZE, hfov: float = DEFAULT_HFOV) -> np.ndarray:
        """Perspective view (RGB, size=(w, h)) from the pano centre."""
        w, h = size
        cam = pixel_dirs_cam(w, h, hfov)
        world = cam @ cam_to_world(yaw, pitch, roll).T
        return self.sample(world)

    def equirect(self, w: int = 2048, h: int = 1024) -> np.ndarray:
        lon = (np.arange(w) + 0.5) / w * 2 * np.pi
        lat = np.pi / 2 - (np.arange(h) + 0.5) / h * np.pi
        lo, la = np.meshgrid(lon, lat)
        dirs = np.dstack([np.sin(lo) * np.cos(la), np.sin(la), np.cos(lo) * np.cos(la)])
        return self.sample(dirs)


def _edge_dirs(C: np.ndarray, side: str, n: int, S: int) -> np.ndarray:
    """World directions along one border of a face camera C (half a pixel inside)."""
    e = 1.0 - 1.0 / S
    t = np.linspace(-e, e, n)
    one = np.full(n, e)
    cam = {
        "right": np.column_stack([one, t, np.ones(n)]),
        "left": np.column_stack([-one, t, np.ones(n)]),
        "top": np.column_stack([t, -one, np.ones(n)]),
        "bottom": np.column_stack([t, one, np.ones(n)]),
    }[side]
    return cam @ C.T


def _edge_error(sampler: CubeSampler, a: int, a_side: str, b: int, n: int = 256) -> float:
    dirs = _edge_dirs(sampler.Cs[a], a_side, n, sampler.S)
    ca = sampler.sample(dirs, face=a).astype(np.float32)
    cb = sampler.sample(dirs, face=b).astype(np.float32)
    return float(np.mean(np.abs(ca - cb)))


def seam_report(faces: list[np.ndarray], layout: CubeLayout) -> dict:
    """Colour step across each of the 12 cube edges under a layout hypothesis, plus a
    baseline: raw border columns of faces that are NOT neighbours (k and k+2)."""
    s = CubeSampler(faces, layout)
    cw = layout.horizon_yaws[1] % 360 == 90
    side = "right" if cw else "left"
    horizon = [_edge_error(s, k, side, k % 4 + 1) for k in range(1, 5)]
    up = [_edge_error(s, k, "top", 0) for k in range(1, 5)]
    down = [_edge_error(s, k, "bottom", 5) for k in range(1, 5)]
    wrong = [
        float(np.mean(np.abs(faces[k][:, -1].astype(np.float32) - faces[(k + 1) % 4 + 1][:, 0].astype(np.float32))))
        for k in range(1, 5)
    ]
    return {"horizon": horizon, "up": up, "down": down, "mismatched": wrong}


def _top_seam_error(faces: list[np.ndarray], yaws: tuple, up_face: int) -> tuple[float, float]:
    """Best (rotation, error) for face `up_face` placed above the horizon faces."""
    arranged = list(faces)
    arranged[0] = faces[up_face]
    best = None
    for rot in (0.0, 90.0, 180.0, 270.0):
        s = CubeSampler(arranged, CubeLayout(horizon_yaws=yaws, up_yaw=rot))
        e = float(np.mean([_edge_error(s, k, "top", 0) for k in range(1, 5)]))
        if best is None or e < best[1]:
            best = (rot, e)
    return best


def detect_layout(faces: list[np.ndarray]) -> tuple[CubeLayout, dict]:
    """Choose the face arrangement with the smallest colour step across cube edges.

    Tries clockwise and counter-clockwise horizon order and the four in-plane rotations
    of the up and down faces. Also flags horizon faces that look upside down (or faces
    0 and 5 swapped): face 5 would then fit the top edges better than face 0.
    Mirroring is not detectable this way; see CubeLayout.mirrored.
    """
    results = {}
    best_h = None
    for name, yaws in (("cw", (0.0, 90.0, 180.0, 270.0)), ("ccw", (0.0, 270.0, 180.0, 90.0))):
        s = CubeSampler(faces, CubeLayout(horizon_yaws=yaws))
        side = "right" if name == "cw" else "left"
        err = float(np.mean([_edge_error(s, k, side, k % 4 + 1) for k in range(1, 5)]))
        results[f"horizon_{name}"] = err
        if best_h is None or err < best_h[1]:
            best_h = (yaws, err)
    yaws = best_h[0]
    best_up, best_down = None, None
    for rot in (0.0, 90.0, 180.0, 270.0):
        s = CubeSampler(faces, CubeLayout(horizon_yaws=yaws, up_yaw=rot, down_yaw=rot))
        eu = float(np.mean([_edge_error(s, k, "top", 0) for k in range(1, 5)]))
        ed = float(np.mean([_edge_error(s, k, "bottom", 5) for k in range(1, 5)]))
        results[f"up_{int(rot)}"], results[f"down_{int(rot)}"] = eu, ed
        if best_up is None or eu < best_up[1]:
            best_up = (rot, eu)
        if best_down is None or ed < best_down[1]:
            best_down = (rot, ed)
    flipped_err = _top_seam_error(faces, yaws, up_face=5)[1]
    layout = CubeLayout(horizon_yaws=yaws, up_yaw=best_up[0], down_yaw=best_down[0])
    rep = seam_report(faces, layout)
    matched = float(np.mean(rep["horizon"] + rep["up"] + rep["down"]))
    mismatched = float(np.mean(rep["mismatched"]))
    layout.seam_score = round(matched, 3)
    layout.seam_ratio = round(matched / max(mismatched, 1e-6), 3)
    layout.verified_by = "refs.detect_layout seam continuity"
    upside_down = bool(flipped_err < best_up[1])
    report = {
        "candidates": {k: round(v, 3) for k, v in results.items()},
        "chosen": layout.to_json(),
        "upside_down_suspected": upside_down,
        "face5_as_up_error": round(flipped_err, 3),
        "edges": {k: [round(x, 3) for x in v] for k, v in rep.items()},
        "ok": bool(layout.seam_ratio < 0.5 and not upside_down),
    }
    return layout, report


# ---------------------------------------------------------------- reference views
@dataclass
class RefView:
    name: str
    yaw: float
    pitch: float
    image: np.ndarray = field(repr=False)  # RGB uint8, raw (not contrast-normalized)
    source: str = "pano"  # "pano" (rendered from the skybox) or "onsite" (warm-up)
    roll: float = 0.0
    hfov: float = DEFAULT_HFOV


def build_refs(sampler: CubeSampler, hfov: float = DEFAULT_HFOV, size=REF_SIZE,
               yaws=REF_YAWS, pitches=REF_PITCHES) -> list[RefView]:
    return [
        RefView(ref_name(y, p), float(y), float(p), sampler.render(y, p, 0.0, size, hfov), "pano", 0.0, hfov)
        for p, y in itertools.product(pitches, yaws)
    ]


def save_refs(refs: list[RefView], out_dir: Path, extra: dict | None = None) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = []
    for r in refs:
        fn = f"{r.name}.jpg"
        cv2.imwrite(str(out_dir / fn), cv2.cvtColor(r.image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        meta.append({"name": r.name, "file": fn, "yaw": r.yaw, "pitch": r.pitch, "roll": r.roll,
                     "hfov": r.hfov, "source": r.source, "w": r.image.shape[1], "h": r.image.shape[0]})
    (out_dir / "refs.json").write_text(json.dumps({**(extra or {}), "refs": meta}, indent=1))


def load_refs(ref_dir: Path) -> tuple[list[RefView], dict]:
    ref_dir = Path(ref_dir)
    meta = json.loads((ref_dir / "refs.json").read_text())
    refs = []
    for m in meta["refs"]:
        img = cv2.imread(str(ref_dir / m["file"]), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(ref_dir / m["file"])
        refs.append(RefView(m["name"], m["yaw"], m["pitch"], cv2.cvtColor(img, cv2.COLOR_BGR2RGB),
                            m.get("source", "pano"), m.get("roll", 0.0), m.get("hfov", DEFAULT_HFOV)))
    return refs, meta


def contact_sheet(images: list[np.ndarray], cols: int, thumb_w: int = 160,
                  labels: list[str] | None = None) -> np.ndarray:
    """Grid of thumbnails (RGB) with optional labels, for visual checks."""
    if not images:
        raise ValueError("no images")
    h0, w0 = images[0].shape[:2]
    tw, th = thumb_w, int(round(thumb_w * h0 / w0))
    rows = math.ceil(len(images) / cols)
    sheet = np.full((rows * (th + 4), cols * (tw + 4), 3), 32, np.uint8)
    for i, img in enumerate(images):
        r, c = divmod(i, cols)
        t = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        if labels:
            cv2.putText(t, labels[i], (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(t, labels[i], (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
        y, x = r * (th + 4) + 2, c * (tw + 4) + 2
        sheet[y:y + th, x:x + tw] = t
    return sheet


def faces_cross(faces: list[np.ndarray], layout: CubeLayout, thumb: int = 256) -> np.ndarray:
    """Unfolded cube (horizon strip in yaw order, up above and down below face 1),
    each face resampled through the verified layout so a correct layout reads as one
    continuous scene. Labels show the original face index."""
    s = CubeSampler(faces, layout)
    order = list(np.argsort([y % 360 for y in layout.horizon_yaws]) + 1)  # faces by yaw
    canvas = np.full((3 * thumb, 4 * thumb, 3), 32, np.uint8)
    for col, k in enumerate(order):
        yaw = layout.horizon_yaws[k - 1]
        canvas[thumb:2 * thumb, col * thumb:(col + 1) * thumb] = s.render(yaw, 0, 0, (thumb, thumb), 90.0)
        cv2.putText(canvas, f"face_{k} yaw {yaw:.0f}", (col * thumb + 6, thumb + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1, cv2.LINE_AA)
    canvas[0:thumb, 0:thumb] = s.render(0.0, 90.0, 0, (thumb, thumb), 90.0)
    canvas[2 * thumb:3 * thumb, 0:thumb] = s.render(0.0, -90.0, 0, (thumb, thumb), 90.0)
    cv2.putText(canvas, "face_0 (up)", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1, cv2.LINE_AA)
    cv2.putText(canvas, "face_5 (down)", (6, 2 * thumb + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1,
                cv2.LINE_AA)
    return canvas


# ---------------------------------------------------------------- pano metadata
def load_pano_meta(data_dir: Path = DATA_DIR) -> dict:
    p = Path(data_dir) / "pano" / "pano.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run `make p3-fetch` (scripts/fetch_matterport.py) first")
    meta = json.loads(p.read_text())
    if os.environ.get("PANO_ID"):
        meta["pano_id"] = os.environ["PANO_ID"]
    return meta


def pano_dir(data_dir: Path = DATA_DIR, pano_id: str | None = None) -> Path:
    pid = pano_id or load_pano_meta(data_dir)["pano_id"]
    return Path(data_dir) / "pano" / pid


def load_layout(pdir: Path) -> CubeLayout:
    p = Path(pdir) / "layout.json"
    if p.exists():
        return CubeLayout.from_json(json.loads(p.read_text())["chosen"])
    return CubeLayout()


def pano_info(data_dir: Path = DATA_DIR, thresholds: dict | None = None) -> dict:
    """What Part 1 serves at GET /api/pano: label, face and ref paths, thresholds.

    Paths are relative to data_dir; Part 1 decides the URL prefix it mounts them on.
    """
    meta = load_pano_meta(data_dir)
    pid = meta["pano_id"]
    pdir = Path(data_dir) / "pano" / pid
    refs = []
    rj = pdir / "refs" / "refs.json"
    if rj.exists():
        refs = [{"name": r["name"], "yaw": r["yaw"], "pitch": r["pitch"], "path": f"pano/{pid}/refs/{r['file']}"}
                for r in json.loads(rj.read_text())["refs"]]
    layout = load_layout(pdir)
    return {
        "pano_id": pid,
        "label": meta.get("label") or "this spot",
        "model_id": meta.get("model_id"),
        "faces": [f"pano/{pid}/face_{i}.jpg" for i in range(6)],
        "horizon_faces": [1, 2, 3, 4],
        "face_yaws": {str(k): layout.horizon_yaws[k - 1] for k in range(1, 5)},
        "layout_verified": bool(layout.verified_by),
        "refs": refs,
        "thresholds": thresholds or {},
    }


# ---------------------------------------------------------------- CLI
def _write(path: Path, img_rgb: np.ndarray, q: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])


def check_pano(pdir: Path) -> dict:
    faces = load_faces(pdir)
    layout, report = detect_layout(faces)
    (pdir / "layout.json").write_text(json.dumps(report, indent=1))
    _write(pdir / "contact_faces.jpg", contact_sheet(faces, 6, 200, [f"face_{i}" for i in range(6)]))
    _write(pdir / "contact_cross.jpg", faces_cross(faces, layout))
    s = CubeSampler(faces, layout)
    _write(pdir / "equirect.jpg", s.equirect(2048, 1024))
    horizon = np.hstack([s.render(y, 0, 0, (400, 400), 90.0) for y in (0, 90, 180, 270)])
    _write(pdir / "horizon.jpg", horizon)
    return report


def build_and_save(pdir: Path, hfov: float = DEFAULT_HFOV, size=REF_SIZE) -> list[RefView]:
    faces = load_faces(pdir)
    layout = load_layout(pdir)
    if not layout.verified_by:
        print("warning: layout.json missing, run --check first; using default layout", file=sys.stderr)
    refs = build_refs(CubeSampler(faces, layout), hfov, size)
    save_refs(refs, pdir / "refs", {"pano_dir": str(pdir), "hfov": hfov, "size": list(size),
                                     "layout": layout.to_json()})
    imgs = [r.image for r in refs]
    _write(pdir / "refs" / "contact_refs.jpg", contact_sheet(imgs, 24, 96, [r.name for r in refs]))
    return refs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--pano-id", default=None)
    ap.add_argument("--check", action="store_true", help="verify face layout, write contact sheets")
    ap.add_argument("--build", action="store_true", help="build 72 reference views")
    ap.add_argument("--all", action="store_true",
                    help="also check the layout of every other downloaded scan point (competitors/negatives)")
    ap.add_argument("--hfov", type=float, default=float(os.environ.get("HFOV_DEG", DEFAULT_HFOV)))
    ap.add_argument("--size", default=f"{REF_SIZE[0]}x{REF_SIZE[1]}")
    a = ap.parse_args(argv)
    pdir = pano_dir(a.data_dir, a.pano_id)
    if not (a.check or a.build):
        a.check = a.build = True
    if a.check:
        rep = check_pano(pdir)
        print(json.dumps({"layout": rep["chosen"], "ok": rep["ok"],
                          "upside_down_suspected": rep["upside_down_suspected"]}, indent=1))
        print(f"look at {pdir/'contact_cross.jpg'} and {pdir/'equirect.jpg'} to confirm upright, continuous faces")
        if not rep["ok"]:
            print("LAYOUT NOT CONFIRMED: seams do not match; inspect the contact sheets", file=sys.stderr)
    if a.all:
        for n in load_pano_meta(a.data_dir).get("negatives", []):
            d = Path(a.data_dir) / "pano" / n["pano_id"]
            if (d / "face_1.jpg").exists():
                rep = check_pano(d)
                print(f"{n['pano_id']}: layout ok={rep['ok']} upside_down={rep['upside_down_suspected']}")
    if a.build:
        w, h = (int(v) for v in a.size.lower().split("x"))
        refs = build_and_save(pdir, a.hfov, (w, h))
        print(f"wrote {len(refs)} reference views to {pdir/'refs'} (contact sheet: contact_refs.jpg)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
