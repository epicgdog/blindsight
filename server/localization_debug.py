"""Explain rejection without weakening matching or exposing camera images."""


def localization_reason(evidence, settings, matched, state):
    if matched:
        return "locked" if state == "LOCKED" else "waiting_for_consistent_frames"
    best = evidence.best_demo(settings.T_LO)
    if best is None:
        return "no_candidate_above_retrieval_threshold"
    if best.inliers < settings.N_MIN:
        return "insufficient_geometric_inliers"
    if best.grid < settings.GRID_CELLS_MIN:
        return "matches_not_spread_across_image"
    if best.inliers < settings.PLACE_RATIO * evidence.best_comp_inliers():
        return "another_scan_matches_better"
    return "geometric_verification_failed"
