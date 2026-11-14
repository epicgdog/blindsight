"""Part 3 evaluation on the HELD-OUT test split (no server needed).

Reports the Phase 4 targets with their test-split numbers, labelled SIMULATED:
  * per-frame false-match rate on held-out negatives (target < 1%) and precision
  * false LOCKED in negative walkthroughs (target 0 of 20)
  * recall on held-out positives (target >= 90%), LOCKED within 2 s (target 20 of 20)
  * yaw error against ground truth: median (< 8 deg) and p95 (< 20 deg)
  * latency per candidate (< 150 ms) and overall rate (>= 2 Hz)
  * ablation: false LOCKED with retrieval alone versus with geometry
  * stress: negatives from held-out scan points that are NOT competing places

Thresholds come from data/calibration.json (calib split). Nothing is tuned here.

  python tools/eval_localization.py
  python tools/eval_localization.py --write-notes NOTES-part3.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
from calibrate import build_localizer, cache_file, rates, raw_eval, raw_settings, read_rgb  # noqa: E402
from geometry import ang_diff  # noqa: E402
from localize import LOCKED, Settings  # noqa: E402
from refs import DATA_DIR  # noqa: E402

BEGIN, END = "<!-- p3-eval:begin -->", "<!-- p3-eval:end -->"


def pct(x: float) -> str:
    return f"{100 * x:.2f}%"


def yaw_errors(items: list[dict], fes, s: Settings) -> list[float]:
    errs = []
    for it, fe in zip(items, fes):
        ok, b = fe.decide(s)
        if ok and b is not None and b.yaw is not None:
            errs.append(ang_diff(b.yaw, it["yaw"]))
    return errs


def run_sequences(L, root: Path, seqs: list[dict], max_lock_s: float = 2.0) -> dict:
    out = []
    for sq in seqs:
        L.reset()
        t, lock_t, lock_frame, locked_frames, yaw_err, proc = 0.0, None, None, 0, [], []
        for k, fr in enumerate(sq["frames"]):
            img = read_rgb(root / fr["file"])
            t0 = time.perf_counter()
            loc = L.update(img, [], fr["heading_deg"])
            dt = time.perf_counter() - t0
            proc.append(dt)
            t += max(1.0 / sq["fps"], dt)  # latest-frame-wins stream: one frame per processing slot
            if loc.state == LOCKED:
                locked_frames += 1
                if lock_t is None:
                    lock_t, lock_frame = t, k + 1
                if loc.yaw_deg is not None:
                    yaw_err.append(ang_diff(loc.yaw_deg, fr["yaw"]))
        out.append({"id": sq["id"], "label": sq["label"], "pano_id": sq["pano_id"], "lock_s": lock_t,
                    "lock_frame": lock_frame, "locked_frames": locked_frames, "n": len(sq["frames"]),
                    "yaw_err_med": float(np.median(yaw_err)) if yaw_err else None,
                    "proc_ms_mean": 1000 * float(np.mean(proc))})
    pos = [r for r in out if r["label"] == "pos"]
    neg = [r for r in out if r["label"] == "neg"]
    return {
        "pos_trials": len(pos),
        "pos_locked_within_2s": sum(r["lock_s"] is not None and r["lock_s"] <= max_lock_s for r in pos),
        "pos_lock_s": [r["lock_s"] for r in pos],
        "neg_walkthroughs": len(neg),
        "neg_false_locked": sum(r["locked_frames"] > 0 for r in neg),
        "neg_locked_frames": sum(r["locked_frames"] for r in neg),
        "proc_ms_mean": float(np.mean([r["proc_ms_mean"] for r in out])) if out else None,
        "detail": out,
    }


def latency(fes) -> dict:
    per_cand = [(fe.verify_ms / max(1, len(fe.cands))) for fe in fes]
    total = [fe.total_ms for fe in fes]
    return {
        "verify_ms_per_candidate_p50": float(np.median(per_cand)),
        "per_candidate_incl_share_of_extract_p95": float(np.percentile(
            [(fe.verify_ms + fe.extract_ms) / max(1, len(fe.cands)) for fe in fes], 95)),
        "extract_ms_p50": float(np.median([fe.extract_ms for fe in fes])),
        "embed_ms_p50": float(np.median([fe.embed_ms for fe in fes])),
        "frame_ms_p50_all_candidates": float(np.median(total)),
        "candidates_per_frame": float(np.mean([len(fe.cands) for fe in fes])),
    }


def evaluate(data_dir: Path, manifest_path: Path, cal_path: Path, use_cache=True, sequences=True,
             stress=True) -> dict:
    manifest = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    cal = json.loads(cal_path.read_text())
    s = Settings.load(calibration=cal, use_config=False, use_env=False)
    L = build_localizer(data_dir, raw_settings(s), cal["matcher"], cal["retriever"],
                        competitors=cal.get("competitors", "auto"))
    test = [f for f in manifest["frames"] if f["split"] == "test"]
    cache = cache_file(data_dir / "eval" / "cache", manifest, L) if use_cache else None
    fes = raw_eval(L, root, test, cache, "test")
    pos_items = [f for f in test if f["label"] == "pos"]
    pos = [fe for f, fe in zip(test, fes) if f["label"] == "pos"]
    neg_items = [f for f in test if f["label"] == "neg"]
    neg = [fe for f, fe in zip(test, fes) if f["label"] == "neg"]
    geo, ret = rates(pos, neg, s, "geometry"), rates(pos, neg, s, "retrieval")
    errs = yaw_errors(pos_items, pos, s)
    buckets = {}
    for lo, hi in ((0, 2), (2, 4), (4, 8), (8, 1e9)):
        sel = [fe for f, fe in zip(neg_items, neg) if lo <= f.get("dist_m", 1e9) < hi]
        if sel:
            buckets[f"{lo}-{hi if hi < 1e9 else 'inf'} m"] = {
                "n": len(sel), "false_matches": int(sum(fe.decide(s)[0] for fe in sel))}
    res = {
        "simulated": bool(manifest.get("simulated", True)),
        "model_id": manifest.get("model_id"), "pano_id": manifest.get("pano_id"), "label": manifest.get("label"),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {k: cal.get(k) for k in ("matcher", "retriever", "competitors", "N_MIN", "PLACE_RATIO", "T_LO",
                                           "T_HI", "GRID_CELLS_MIN", "LOCK_NEED", "HFOV_DEG")},
        "frames": {"geometry": geo, "retrieval_only": ret, "neg_by_distance": buckets,
                   "yaw_err_median": float(np.median(errs)) if errs else None,
                   "yaw_err_p95": float(np.percentile(errs, 95)) if errs else None,
                   "yaw_err_max": float(np.max(errs)) if errs else None, "yaw_n": len(errs)},
        "latency": latency(fes),
    }
    if sequences:
        seqs = [q for q in manifest["sequences"] if q["split"] == "test"]
        L.apply_settings(s)
        res["walkthroughs"] = run_sequences(L, root, seqs)
        L.mode = "retrieval"
        L.apply_settings(s)
        res["walkthroughs_retrieval_only"] = run_sequences(L, root, seqs)
        L.mode = "geometry"
    if stress and manifest.get("holdout_panos"):
        hold = set(manifest["holdout_panos"])
        comps = [c for c in L.competitors if c not in hold]
        L2 = build_localizer(data_dir, raw_settings(s), cal["matcher"], cal["retriever"], competitors=comps,
                             verbose=False)
        items = [f for f in neg_items if f.get("holdout")]
        cache2 = cache_file(data_dir / "eval" / "cache", manifest, L2, "stress") if use_cache else None
        fes2 = raw_eval(L2, root, items, cache2, "stress")
        st = {"holdout_panos": sorted(hold), "competitors": comps, "n_neg": len(items),
              "false_matches": int(sum(fe.decide(s)[0] for fe in fes2))}
        st["false_match_rate"] = st["false_matches"] / max(1, len(items))
        if sequences:
            L2.apply_settings(s)
            seqs = [q for q in manifest["sequences"] if q["label"] == "neg" and q.get("holdout")]
            w = run_sequences(L2, root, seqs)
            st["walkthroughs"], st["false_locked"] = w["neg_walkthroughs"], w["neg_false_locked"]
        res["stress"] = st
    res["targets"] = targets(res)
    return res


def targets(r: dict) -> list[dict]:
    f, lat, w = r["frames"], r["latency"], r.get("walkthroughs")
    rows = [
        ("LOCKED precision on held-out negatives: per-frame false-match rate", "< 1%",
         pct(f["geometry"]["false_match_rate"]), f["geometry"]["false_match_rate"] < 0.01),
        ("Recall: held-out positives matched", ">= 90%", pct(f["geometry"]["recall"]),
         f["geometry"]["recall"] >= 0.90),
        ("Yaw error median", "< 8 deg", "n/a" if f["yaw_err_median"] is None else f"{f['yaw_err_median']:.2f} deg",
         f["yaw_err_median"] is not None and f["yaw_err_median"] < 8),
        ("Yaw error p95", "< 20 deg", "n/a" if f["yaw_err_p95"] is None else f"{f['yaw_err_p95']:.2f} deg",
         f["yaw_err_p95"] is not None and f["yaw_err_p95"] < 20),
        ("Latency per candidate (verify + share of extraction, p95)", "< 150 ms",
         f"{lat['per_candidate_incl_share_of_extract_p95']:.0f} ms",
         lat["per_candidate_incl_share_of_extract_p95"] < 150),
    ]
    if w:
        hz = 1000.0 / w["proc_ms_mean"] if w["proc_ms_mean"] else 0
        rows += [
            ("False LOCKED in negative walkthroughs", "0 of 20", f"{w['neg_false_locked']} of {w['neg_walkthroughs']}",
             w["neg_false_locked"] == 0 and w["neg_walkthroughs"] >= 20),
            ("LOCKED within 2 s", "20 of 20", f"{w['pos_locked_within_2s']} of {w['pos_trials']}",
             w["pos_locked_within_2s"] == w["pos_trials"] and w["pos_trials"] >= 20),
            ("Overall localization rate (update(), this machine)", ">= 2 Hz", f"{hz:.1f} Hz", hz >= 2.0),
        ]
    return [{"metric": m, "target": t, "measured": v, "pass": bool(p)} for m, t, v, p in rows]


def markdown(r: dict) -> str:
    c = r["config"]
    lines = [
        f"Run {r['created_utc']}, model `{r['model_id']}`, pano `{r['pano_id']}` ({r['label']}). "
        f"**{'SIMULATED' if r['simulated'] else 'REAL'} data, held-out test split.**",
        f"Config: matcher `{c['matcher']}`, retriever `{c['retriever']}`, N_MIN {c['N_MIN']}, "
        f"PLACE_RATIO {c['PLACE_RATIO']}, T_LO {c['T_LO']}, T_HI {c['T_HI']}, LOCK_NEED {c['LOCK_NEED']}, "
        f"HFOV {c['HFOV_DEG']}, competing places {len(c['competitors'] or [])}.",
        "",
        "| Metric | Target | Measured (test split) | Pass |",
        "|---|---|---|---|",
    ]
    lines += [f"| {t['metric']} | {t['target']} | {t['measured']} | {'PASS' if t['pass'] else 'FAIL'} |"
              for t in r["targets"]]
    g, ro = r["frames"]["geometry"], r["frames"]["retrieval_only"]
    lines += ["", f"Per frame: {g['tp']}/{g['n_pos']} positives matched, {g['fp']}/{g['n_neg']} negatives "
                  f"falsely matched (precision {pct(g['precision'])})."]
    lines += [f"Ablation, per frame false-match rate: retrieval alone {pct(ro['false_match_rate'])} "
              f"({ro['fp']}/{ro['n_neg']}) vs with geometry {pct(g['false_match_rate'])} ({g['fp']}/{g['n_neg']})."]
    if "walkthroughs_retrieval_only" in r:
        a, b = r["walkthroughs_retrieval_only"], r["walkthroughs"]
        lines += [f"Ablation, negative walkthroughs with a false LOCKED: retrieval alone "
                  f"{a['neg_false_locked']} of {a['neg_walkthroughs']} vs with geometry "
                  f"{b['neg_false_locked']} of {b['neg_walkthroughs']}."]
    if r["frames"]["neg_by_distance"]:
        lines += ["Negatives by distance from the locked scan point: " + ", ".join(
            f"{k}: {v['false_matches']}/{v['n']} false" for k, v in r["frames"]["neg_by_distance"].items()) + "."]
    if "stress" in r:
        st = r["stress"]
        extra = f", {st['false_locked']} of {st['walkthroughs']} walkthroughs falsely LOCKED" if "false_locked" in st else ""
        lines += [f"Stress (held-out scan points {', '.join(st['holdout_panos'])} removed from competing places): "
                  f"{st['false_matches']}/{st['n_neg']} frames falsely matched ({pct(st['false_match_rate'])}){extra}."]
    la = r["latency"]
    lines += [f"Latency p50: extract {la['extract_ms_p50']:.0f} ms, embed {la['embed_ms_p50']:.0f} ms, verify "
              f"{la['verify_ms_per_candidate_p50']:.0f} ms per candidate, {la['candidates_per_frame']:.1f} candidates "
              f"per frame in raw evaluation (runtime verifies competitors only when needed)."]
    return "\n".join(lines)


def write_notes(path: Path, md: str) -> None:
    text = path.read_text() if path.exists() else ""
    block = f"{BEGIN}\n{md}\n{END}"
    if BEGIN in text and END in text:
        pre, rest = text.split(BEGIN, 1)
        post = rest.split(END, 1)[1]
        text = pre + block + post
    else:
        text = text.rstrip() + "\n\n## Phase 4 results (tools/eval_localization.py)\n\n" + block + "\n"
    path.write_text(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--manifest", type=Path, default=None)
    ap.add_argument("--calibration", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None, help="default <data-dir>/eval/results.json")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-sequences", action="store_true")
    ap.add_argument("--no-stress", action="store_true")
    ap.add_argument("--write-notes", type=Path, default=None)
    a = ap.parse_args(argv)
    manifest = a.manifest or a.data_dir / "synthetic" / "manifest.json"
    cal = a.calibration or a.data_dir / "calibration.json"
    r = evaluate(a.data_dir, manifest, cal, not a.no_cache, not a.no_sequences, not a.no_stress)
    out = a.out or a.data_dir / "eval" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, indent=1))
    md = markdown(r)
    print(md)
    if a.write_notes:
        write_notes(a.write_notes, md)
        print(f"updated {a.write_notes}")
    return 0 if all(t["pass"] for t in r["targets"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
