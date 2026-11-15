"""Property-wide scan recognition. A scan match is not a metric walking pose.

Real phone photos teach it places (data/onsite/places/<scan number>_<name>/*.jpg):
each photo is verified against that scan's panorama views, gets the pano-frame yaw
the verification recovers, and becomes an extra reference for that scan. The folder
name after the scan number is spoken as the place name. A photo that does not verify
against its scan is skipped, so a mislabelled folder cannot teach a wrong place.

  python -m server.property_localize --try DIR   # which place is each photo recognised as
"""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from server.disambiguation import distinctive_support
from server.geometry import (
    blank_boxes,
    extract_features,
    intrinsics,
    movable_boxes_px,
    normalize_contrast,
)
from server.localize import ONSITE_NEG, PHOTO_EXT, Location, RefView, _Ref, heading_observation
from server.localize import Localizer as SingleLocalizer

PLACES_DIR = ("onsite", "places")  # under the data directory
PHOTOS_PER_PLACE = 40  # most photos per folder used as references
VERIFY_PER_PHOTO = 8  # panorama views of the labelled scan checked per photo


def read_photo(path) -> np.ndarray | None:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)  # applies the photo's EXIF rotation
    return None if img is None else cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def place_name(words: str) -> str | None:
    name = " ".join(words.replace("-", " ").replace("_", " ").split())
    return name or None


def choose_place(candidates, settings):
    best_by_place = {}
    for candidate in candidates:
        old = best_by_place.get(candidate.place)
        if old is None or (candidate.inliers, candidate.grid) > (old.inliers, old.grid):
            best_by_place[candidate.place] = candidate
    ranked = sorted(best_by_place.values(), key=lambda c: (c.inliers, c.grid), reverse=True)
    if not ranked:
        return None, "no_candidates", 0
    winner = ranked[0]
    competitor = ranked[1].inliers if len(ranked) > 1 else 0
    if winner.place == ONSITE_NEG:
        return None, "unknown_place_matches_better", competitor
    if winner.inliers < settings.N_MIN:
        return None, "insufficient_geometric_inliers", competitor
    if winner.grid < settings.GRID_CELLS_MIN or winner.yaw is None:
        return None, "insufficient_spatial_or_heading_evidence", competitor
    # A tie must never become a location simply because of array order.
    if (
        winner.inliers <= competitor
        or winner.inliers < max(1.25, settings.PLACE_RATIO) * competitor
    ):
        return None, "ambiguous_between_scan_positions", competitor
    return winner, "geometrically_verified", competitor


class Localizer(SingleLocalizer):
    def __init__(self, data_dir=None, pano_id=None, **kwargs):
        from pathlib import Path

        from server.refs import DATA_DIR

        root = Path(data_dir) if data_dir else DATA_DIR
        available = sorted(
            p.name
            for p in (root / "pano").iterdir()
            if p.is_dir() and all((p / f"face_{i}.jpg").exists() for i in range(6))
        )
        if len(available) < 2:
            raise ValueError(
                "Property localization requires at least two complete scan panoramas. Run p3-fetch with --neighbors 65."
            )
        source = root / "matterport" / "panoLocations.json"
        if source.exists():
            raw = json.loads(source.read_text())
            expected = {p["id"] for p in raw["data"]["model"]["panoLocations"]}
            missing = expected - set(available)
            if missing:
                raise ValueError(
                    f"Property index missing {len(missing)} of {len(expected)} scans. Run: uv run python scripts/fetch_matterport.py --model P3S6VUTP3qB --neighbors 65"
                )
            available = [place for place in available if place in expected]
            metadata = json.loads((root / "pano/pano.json").read_text())
            if (pano_id or metadata["pano_id"]) not in expected:
                raise ValueError("Selected panorama does not belong to the saved property manifest")
        kwargs.pop("competitors", None)
        if kwargs.get("mode", "geometry") != "geometry":
            raise ValueError("Property localization requires geometric verification")
        super().__init__(
            data_dir=root, pano_id=pano_id, competitors=available + [ONSITE_NEG], **kwargs
        )
        self.scan_ids = available
        self._tracking_place = None
        self.labels = {}
        try:
            meta = json.loads((root / "pano/pano.json").read_text())
            self.labels = meta.get("places", {})
            for entry in meta.get("negatives", []):
                self.labels[entry["pano_id"]] = entry
            self.labels[meta["pano_id"]] = {"label": meta.get("label")}
        except (OSError, ValueError, KeyError):
            pass
        self.scan_label = {}  # pano id -> Matterport scan number ("34")
        if source.exists():
            for p in json.loads(source.read_text())["data"]["model"]["panoLocations"]:
                if p.get("label") is not None:
                    self.scan_label[p["id"]] = str(p["label"])
        self.photo_places = self._load_photo_places(root / Path(*PLACES_DIR))
        if kwargs.get("verbose", True):
            for line in self.photo_places_summary():
                print(f"[property] {line}", file=sys.stderr)

    # -- phone photos that teach places ---------------------------------------
    def scan_name(self, pano_id) -> str:
        return self.scan_label.get(pano_id, pano_id or "-")

    def resolve_folder(self, folder: str) -> tuple[str | None, str | None]:
        """'34_front_door' -> (pano id of scan 34, 'front door'). A folder may also start
        with the full pano id. Returns (None, None) when no scan matches."""
        for pano_id in sorted(self.scan_ids, key=len, reverse=True):
            if folder == pano_id or folder.startswith(pano_id + "_"):
                return pano_id, place_name(folder[len(pano_id) :])
        head, _, rest = folder.partition("_")
        by_number = {num: pid for pid, num in self.scan_label.items() if pid in self.scan_ids}
        if head in by_number:
            return by_number[head], place_name(rest)
        return None, None

    def _load_photo_places(self, places_dir: Path) -> list[dict]:
        report = []
        if not places_dir.is_dir():
            return report
        for folder in sorted(p for p in places_dir.iterdir() if p.is_dir()):
            pano_id, name = self.resolve_folder(folder.name)
            entry = {
                "folder": folder.name,
                "pano_id": pano_id,
                "scan": self.scan_name(pano_id),
                "name": name,
                "added": 0,
                "skipped": [],
                "heic": 0,
            }
            report.append(entry)
            if pano_id is None:
                continue
            entry["heic"] = sum(
                1 for p in folder.iterdir() if p.suffix.lower() in (".heic", ".heif")
            )
            files = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXT)[
                :PHOTOS_PER_PLACE
            ]
            for f in files:
                yaw = self._add_place_photo(f, pano_id, entry["added"])
                if yaw is None:
                    entry["skipped"].append(f.name)
                else:
                    entry["added"] += 1
            if name and entry["added"]:
                self.labels[pano_id] = {"label": name}
        return report

    def _add_place_photo(self, path: Path, pano_id: str, k: int) -> float | None:
        """Verify one photo against its scan's panorama views; add it if it passes."""
        rgb = read_photo(path)
        if rgb is None:
            return None
        raw = self._prep(rgb)
        norm = normalize_contrast(raw)
        h, w = raw.shape[:2]
        feats = extract_features(self.matcher, norm)
        pano_views = [
            i for i, r in enumerate(self._refs) if r.place == pano_id and r.view.source == "pano"
        ]
        if not pano_views:
            return None
        sims = self._emb[pano_views] @ self.embedder.embed_batch([norm])[0]
        bow = self._bow.query(feats)[pano_views]
        order = list(
            dict.fromkeys(
                [pano_views[j] for j in np.argsort(-sims)[:VERIFY_PER_PHOTO]]
                + [pano_views[j] for j in np.argsort(-bow)[: VERIFY_PER_PHOTO // 2]]
            )
        )
        sim_of = dict(zip(pano_views, sims.tolist(), strict=True))
        K = intrinsics(w, h, self.s.HFOV_DEG)
        self._verified_live_points = {}
        cands = [self._verify(i, float(sim_of[i]), feats, K) for i in order]
        teaching_points = dict(self._verified_live_points)
        best = max(cands, key=lambda c: (c.inliers, c.grid), default=None)
        if (
            best is None
            or best.yaw is None
            or best.inliers < self.s.N_MIN
            or best.grid < self.s.GRID_CELLS_MIN
        ):
            return None
        # The folder label may break a near tie, but never overrides another scan (or an
        # unknown-place photo) that lines up better: that photo was taken somewhere else.
        _, _, _, others, _ = self.recognize(rgb)
        rival = max((c.inliers for c in others if c.place != pano_id), default=0)
        accepted, _, _ = choose_place([best] + [c for c in others if c.place != pano_id], self.s)
        if best.inliers < rival or accepted is None or accepted.place != pano_id:
            return None
        support = distinctive_support(
            best, others, self._verified_live_points | teaching_points, (w, h), self.s.N_MIN
        )
        if not support["passed"]:
            return None
        tag = place_name(path.parent.name) or "photo"
        view = RefView(
            f"photo_{tag.replace(' ', '-')[:20]}_{k:02d}_y{int(round(best.yaw)) % 360:03d}",
            float(best.yaw),
            float(best.pitch or 0.0),
            raw,
            "photo",
            float(best.roll or 0.0),
            self.s.HFOV_DEG,
        )
        self._refs.append(_Ref(view, pano_id, False, feats, intrinsics(w, h, self.s.HFOV_DEG)))
        self._emb = np.vstack([self._emb, self.embedder.embed_batch([norm])])
        self._bow.add(feats)
        self._is_demo = np.append(self._is_demo, False)
        self._is_pano_demo = np.append(self._is_pano_demo, False)
        self._sims = None
        return float(best.yaw)

    def photo_places_summary(self) -> list[str]:
        lines = []
        for e in self.photo_places:
            if e["pano_id"] is None:
                lines.append(
                    f"{e['folder']}: no scan number matches this folder name; expected e.g. 34_front_door"
                )
                continue
            msg = f"{e['folder']}: {e['added']} photos added to scan {e['scan']}" + (
                f" as '{e['name']}'" if e["name"] else ""
            )
            if e["skipped"]:
                msg += f"; {len(e['skipped'])} skipped (did not line up with scan {e['scan']}: {', '.join(e['skipped'][:5])})"
            if e["heic"]:
                msg += f"; {e['heic']} HEIC photos ignored (convert to JPEG)"
            lines.append(msg)
        return lines

    def _load_place(self, place, demo):
        # Uniform yaw coverage across every place; no privileged demo panorama.
        super()._load_place(place, demo=False)

    def reset(self):
        super().reset()
        self._tracking_place = None
        self.last_debug = {}

    def info(self):
        result = super().info()
        result.update(
            scope="property",
            indexed_scans=len({r.place for r in self._refs if r.place != ONSITE_NEG}),
            scan_ids=sorted({r.place for r in self._refs if r.place != ONSITE_NEG}),
            photo_places=[
                {k: e[k] for k in ("folder", "scan", "name", "added")}
                | {"skipped": len(e["skipped"])}
                for e in getattr(self, "photo_places", [])
            ],
        )
        return result

    def add_reference(self, frame_rgb):
        # Unregistered warm-up frames cannot safely acquire a place identity.
        return False

    def place_label(self, pano_id):
        data = self.labels.get(pano_id, {})
        label = data.get("room") or data.get("label")
        return None if label and str(label).isdigit() else label

    def recognize(self, frame_rgb, detections=(), tracking=None):
        """One frame on its own: shortlist places, verify geometrically, pick a winner."""
        self._verified_live_points = {}
        image = normalize_contrast(self._prep(frame_rgb))
        h, w = image.shape[:2]
        boxes = movable_boxes_px(detections, w, h)
        t = time.perf_counter()
        similarities = self._emb @ self.embedder.embed_batch([blank_boxes(image, boxes)])[0]
        embed_ms = (time.perf_counter() - t) * 1000
        features = extract_features(self.matcher, image, boxes)
        bow = self._bow.query(features)
        # Rank places, not just views: one place cannot consume the entire shortlist.
        places = {}
        for i, ref in enumerate(self._refs):
            places.setdefault(ref.place, []).append(i)
        retrieval_places = sorted(
            places, key=lambda p: max(similarities[i] for i in places[p]), reverse=True
        )[:6]
        bow_places = sorted(places, key=lambda p: max(bow[i] for i in places[p]), reverse=True)[:4]
        shortlist = list(
            dict.fromkeys(retrieval_places + bow_places + ([tracking] if tracking else []))
        )
        indices = []
        for place in shortlist:
            ids = places[place]
            indices.extend(
                dict.fromkeys(
                    [max(ids, key=lambda i: similarities[i]), max(ids, key=lambda i: bow[i])]
                )
            )
        K = intrinsics(w, h, self.s.HFOV_DEG)
        candidates = [self._verify(i, similarities[i], features, K) for i in indices]
        winner, reason, competitor = choose_place(candidates, self.s)
        self._distinctive = {"required": False, "passed": False, "count": 0, "grid": 0}
        if winner is not None:
            self._distinctive = distinctive_support(
                winner, candidates, self._verified_live_points, (w, h), self.s.N_MIN
            )
            if not self._distinctive["passed"]:
                winner, reason = None, "insufficient_distinguishing_features"
        return winner, reason, competitor, candidates, embed_ms

    def update(self, frame_rgb, detections, heading_deg):
        with self._lock:
            start = time.perf_counter()
            winner, reason, competitor, candidates, embed_ms = self.recognize(
                frame_rgb, detections, self._tracking_place
            )
            if winner is None:
                self._tracking_place = None
                self._sm.reset()
                state, yaw, confidence = "UNCERTAIN", None, 0.0
            else:
                if winner.place != self._tracking_place:
                    self._sm.reset()
                    self._tracking_place = winner.place
                observation = self._sm.step(
                    True, winner.yaw, heading_deg, min(1, winner.inliers / (2 * self.s.N_MIN))
                )
                state, yaw, confidence = observation.state, observation.yaw, observation.conf
                if state != "LOCKED":
                    reason = "waiting_for_consistent_frames"
            place = winner.place if winner is not None and state == "LOCKED" else None
            label = self.place_label(place)
            # Both localization engines must publish the same verified evidence to the voice
            # agent. Rankings alone identify a scan; heading_obs calibrates its map-frame heading.
            best_by_place = {}
            for candidate in candidates:
                previous = best_by_place.get(candidate.place)
                if previous is None or candidate.inliers > previous.inliers:
                    best_by_place[candidate.place] = candidate
            ranking = [
                {
                    "place": c.place,
                    "inliers": int(c.inliers),
                    "sim": float(c.sim),
                    "ref": int(c.idx),
                }
                for c in sorted(best_by_place.values(), key=lambda c: -c.inliers)
            ]
            matched = place is not None
            match = {
                "ranking": ranking,
                "heading_obs": heading_observation(matched, winner, None),
                "pano": place,
                "best_ref": int(winner.idx) if winner is not None else None,
            }
            self.last_debug = dict(
                scope="property",
                matched=matched,
                match=match,
                selected_scan_only=False,
                indexed_scans=len(self.scan_ids),
                indexed_scan_ids=self.scan_ids,
                candidate_pano_id=winner.place if winner else None,
                reason=reason,
                best_inliers=winner.inliers if winner else 0,
                competitor_inliers=competitor,
                distinctive_evidence=self._distinctive,
                embed=round(embed_ms, 1),
                total=round((time.perf_counter() - start) * 1000, 1),
                candidates=[
                    dict(pano_id=c.place, inliers=c.inliers, grid=c.grid) for c in candidates
                ],
            )
            return Location(
                place,
                label,
                state,
                float(winner.sim) if winner else 0.0,
                winner.inliers if winner else 0,
                yaw,
                confidence,
                winner.name if winner else None,
            )


def try_places(data_dir, photo_dir, **localizer_kwargs) -> int:
    """Recognise each photo on its own (no temporal fusion) and compare with the place
    its folder names. Folders named like the teaching folders (34_front_door) expect
    that scan; any other folder (e.g. elsewhere/) expects no match at all."""
    photo_dir = Path(photo_dir).expanduser().resolve()
    loc = Localizer(data_dir, **localizer_kwargs)
    places_dir = (Path(loc.data_dir) / Path(*PLACES_DIR)).resolve()
    if photo_dir == places_dir or places_dir in photo_dir.parents:
        print(
            "warning: these are the teaching photos themselves; test with a different set of photos "
            "or every answer will look right"
        )
    groups = [(None, photo_dir)] + [(p.name, p) for p in sorted(photo_dir.iterdir()) if p.is_dir()]
    heic = 0
    rows = []
    for folder, d in groups:
        expected = "?" if folder is None else (loc.resolve_folder(folder)[0] or "none")
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".heic", ".heif"):
                heic += 1
            if f.suffix.lower() not in PHOTO_EXT:
                continue
            rgb = read_photo(f)
            if rgb is None:
                continue
            winner, reason, rival, _, _ = loc.recognize(rgb)
            got = winner.place if winner else None
            if expected == "?":
                verdict = "-"
            elif expected == "none":
                verdict = "ok" if got is None else "FALSE MATCH"
            elif got == expected:
                verdict = "right"
            else:
                verdict = "missed" if got is None else "WRONG PLACE"
            rows.append(
                (
                    f"{folder + '/' if folder else ''}{f.name}",
                    expected,
                    got,
                    winner,
                    rival,
                    reason,
                    verdict,
                )
            )

    def show(pid):
        if pid in (None, "none"):
            return "not here" if pid == "none" else "-"
        if pid == "?":
            return "?"
        name = loc.place_label(pid)
        return f"scan {loc.scan_name(pid)}" + (f" {name}" if name else "")

    print(
        f"{'photo':<34} {'should be':<22} {'recognised as':<22} {'inliers':>7} {'rival':>5}  result / reason"
    )
    for name, expected, got, winner, rival, reason, verdict in rows:
        print(
            f"{name[:34]:<34} {show(expected)[:22]:<22} {show(got)[:22]:<22} "
            f"{winner.inliers if winner else 0:>7} {rival:>5}  {verdict} ({reason})"
        )
    counts = {
        v: sum(1 for r in rows if r[-1] == v)
        for v in ("right", "WRONG PLACE", "missed", "ok", "FALSE MATCH")
    }
    print(
        f"\n{len(rows)} photos: {counts['right']} right, {counts['WRONG PLACE']} wrong place, "
        f"{counts['missed']} missed, {counts['ok']} correctly not matched, {counts['FALSE MATCH']} false matches."
    )
    print(
        "'rival' = inliers of the best other place. Taught places: "
        + ("; ".join(loc.photo_places_summary()) or "none (no folders in data/onsite/places)")
    )
    if heic:
        print(
            f"{heic} HEIC photos ignored: convert them with  sips -s format jpeg *.HEIC --out jpg/"
        )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Property localizer: test phone photos against every scan"
    )
    ap.add_argument(
        "--try",
        dest="try_dir",
        type=Path,
        required=True,
        help="folder of photos; subfolders named like 34_front_door say where each was taken",
    )
    ap.add_argument("--data-dir", type=Path, default=None)
    a = ap.parse_args(argv)
    return try_places(a.data_dir, a.try_dir)


if __name__ == "__main__":
    raise SystemExit(main())
