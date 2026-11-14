"""Part 3: synthetic data -> calibration (calib split only) -> evaluation (held-out split).

Runs the real tools on a tiny procedural dataset and checks that evaluation reproduces
its own numbers exactly and that calibration never reads the test split.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibrate  # noqa: E402
import eval_localization  # noqa: E402
import make_synthetic  # noqa: E402
from p3_room import Room, write_dataset  # noqa: E402


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = write_dataset(tmp_path_factory.mktemp("p3eval"), Room(), S=256, ids=["room_p0", "room_p1", "room_p3"])
    m = make_synthetic.build(root, root / "synthetic", n_pos=24, n_neg=60, n_seq=2, seq_pos_frames=5,
                             seq_neg_frames=6, seed=0, size=(270, 360))
    return root, m


def test_synthetic_split_and_contents(dataset):
    root, m = dataset
    assert m["simulated"] is True
    c = m["counts"]
    assert c["neg"] >= 60 and c["calib_pos"] == 18 and c["test_pos"] == 6 and c["calib_neg"] == 45
    assert {f["pano_id"] for f in m["frames"] if f["label"] == "neg"} == {"room_p1", "room_p3"}
    assert all(s["split"] == "test" for s in m["sequences"])
    assert m["holdout_panos"] == ["room_p3"]
    assert all((root / "synthetic" / f["file"]).exists() for f in m["frames"])


def test_calibration_reads_only_the_calib_split(dataset):
    root, m = dataset
    assert calibrate.main(["--data-dir", str(root), "--matcher", "sift", "--retriever", "classic"]) == 0
    cal = json.loads((root / "calibration.json").read_text())
    assert cal["split"] == "calib" and cal["simulated"] is True
    assert cal["N_MIN"] >= 30 and cal["PLACE_RATIO"] >= 1.0
    assert cal["matcher"] == "sift" and cal["retriever"] == "classic-hog"
    lines = [json.loads(x) for c in (root / "eval" / "cache").glob("raw_*.jsonl") for x in c.read_text().splitlines()]
    seen = {x["file"] for x in lines}
    calib = {f["file"] for f in m["frames"] if f["split"] == "calib"}
    assert seen == calib


def test_eval_reproduces_its_numbers(dataset):
    root, _ = dataset
    if not (root / "calibration.json").exists():
        calibrate.main(["--data-dir", str(root), "--matcher", "sift", "--retriever", "classic"])
    man, cal = root / "synthetic" / "manifest.json", root / "calibration.json"
    r1 = eval_localization.evaluate(root, man, cal, use_cache=False)
    r2 = eval_localization.evaluate(root, man, cal, use_cache=False)
    for key in ("geometry", "retrieval_only", "yaw_err_median", "yaw_err_p95", "neg_by_distance"):
        assert r1["frames"][key] == r2["frames"][key], key
    for key in ("pos_locked_within_2s", "neg_false_locked", "neg_locked_frames"):
        assert r1["walkthroughs"][key] == r2["walkthroughs"][key], key
    assert r1["stress"]["false_matches"] == r2["stress"]["false_matches"]
    assert [t["metric"] for t in r1["targets"]][:2] == [
        "LOCKED precision on held-out negatives: per-frame false-match rate", "Recall: held-out positives matched"]
    md = eval_localization.markdown(r1)
    assert "SIMULATED" in md and "Ablation" in md
    notes = root / "NOTES.md"
    eval_localization.write_notes(notes, md)
    eval_localization.write_notes(notes, md)  # idempotent: one block
    assert notes.read_text().count(eval_localization.BEGIN) == 1
