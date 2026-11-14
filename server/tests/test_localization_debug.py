from types import SimpleNamespace

import pytest

from server.localization_debug import localization_reason


@pytest.mark.parametrize(
    "best,comp,matched,state,reason",
    [
        (None, 0, False, "UNCERTAIN", "no_candidate_above_retrieval_threshold"),
        ((10, 5), 0, False, "UNCERTAIN", "insufficient_geometric_inliers"),
        ((40, 1), 0, False, "UNCERTAIN", "matches_not_spread_across_image"),
        ((40, 5), 60, False, "UNCERTAIN", "another_scan_matches_better"),
        ((40, 5), 0, True, "UNCERTAIN", "waiting_for_consistent_frames"),
        ((40, 5), 0, True, "LOCKED", "locked"),
    ],
)
def test_rejection_explanation(best, comp, matched, state, reason):
    settings = SimpleNamespace(T_LO=0.8, N_MIN=30, GRID_CELLS_MIN=3, PLACE_RATIO=1.5)
    evidence = SimpleNamespace(
        best_demo=lambda _: SimpleNamespace(inliers=best[0], grid=best[1]) if best else None,
        best_comp_inliers=lambda: comp,
    )
    assert localization_reason(evidence, settings, matched, state) == reason
