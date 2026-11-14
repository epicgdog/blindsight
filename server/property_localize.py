"""Property-wide scan recognition. A scan match is not a metric walking pose."""

import json
import time

from server.geometry import (
    blank_boxes,
    extract_features,
    intrinsics,
    movable_boxes_px,
    normalize_contrast,
)
from server.localize import ONSITE_NEG, Location
from server.localize import Localizer as SingleLocalizer


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
        )
        return result

    def add_reference(self, frame_rgb):
        # Unregistered warm-up frames cannot safely acquire a place identity.
        return False

    def update(self, frame_rgb, detections, heading_deg):
        with self._lock:
            start = time.perf_counter()
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
            bow_places = sorted(places, key=lambda p: max(bow[i] for i in places[p]), reverse=True)[
                :4
            ]
            shortlist = list(
                dict.fromkeys(
                    retrieval_places
                    + bow_places
                    + ([self._tracking_place] if self._tracking_place else [])
                )
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
            data = self.labels.get(place, {})
            label = data.get("room") or data.get("label")
            if label and str(label).isdigit():
                label = None
            self.last_debug = dict(
                scope="property",
                selected_scan_only=False,
                indexed_scans=len(self.scan_ids),
                indexed_scan_ids=self.scan_ids,
                candidate_pano_id=winner.place if winner else None,
                reason=reason,
                best_inliers=winner.inliers if winner else 0,
                competitor_inliers=competitor,
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
