"""Export authorized property geometry; candidates are never approved routes."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_matterport as mp


def main():
    cfg = mp.load_api_config()
    auth = mp.auth_headers(cfg, mp.credentials(cfg))
    model_id = "P3S6VUTP3qB"
    rooms = mp.graphql(cfg, auth, cfg["rooms_query"], {"id": model_id})
    assets = mp.graphql(cfg, auth, "query($id:ID!){model(id:$id){assets{meshes{id format resolution status}}}}", {"id": model_id})
    panos = mp.graphql(cfg, auth, cfg["pano_query"], {"id": model_id})
    nodes = []
    for pano in panos["data"]["model"]["panoLocations"]:
        p = pano["position"]
        # Floor assignment is deliberately not inferred from scan height alone.
        nodes.append(dict(id=pano["id"], label=f"Scan {pano['label']}", x_m=p["x"],
                          y_m=p["y"], camera_z_m=p["z"], floor=None, verified=False))
    out = ROOT / "data" / "spatial"
    out.mkdir(parents=True, exist_ok=True)
    (out / "source.json").write_text(json.dumps(dict(model_id=model_id, scan_nodes=nodes,
           geometry=rooms["data"]["model"], mesh_assets=assets["data"]["model"]["assets"]["meshes"]), indent=2))
    meshes = assets["data"]["model"]["assets"]["meshes"]
    report = {"model_id": model_id, "scans": len(nodes), "meshes": meshes,
              "metric_landmarks_ready": False, "reviewed_routes_ready": False,
              "blocker": "Metric mesh assets are locked" if all(m["status"] == "locked" for m in meshes) else "Build registered metric image landmarks and review routes"}
    (out / "readiness.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
