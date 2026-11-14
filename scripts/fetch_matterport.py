"""Part 3, Phase 3: Matterport panoLocations and skybox faces for model 6sd6A5r5EVp.

Source of truth for the API is the hackathon quickstart:
  https://betaapp.matterport.com/2026-princeton-hackathon/quickstart
Nothing API-specific is guessed here. Endpoint, auth scheme, credential variable
names, the GraphQL query and where the face URLs live are read from
data/matterport/api.json, which is filled in from the quickstart (template:
data/matterport/api.example.json). Credentials are read from .env.local and are never
printed or logged.

What the script does:
 1. query panoLocations for the model, print a table (id, label, position), save the
    raw JSON to data/matterport/panoLocations.json
 2. rank candidates: label mentions lobby/entry, central position, many keypoints in
    the horizon faces; pick one and write data/pano/pano.json (PANO_ID)
 3. download the 2k skybox faces for the chosen pano AND its nearest scan points
    (hard negatives and competing places) to data/pano/<id>/face_0..5.jpg
 4. verify face layout (server/refs.py) and write the top-down plot, horizon image
    and contact sheets for the human to recognise the spot (A3)

  python scripts/fetch_matterport.py --check          # key works? can it see the model?
  python scripts/fetch_matterport.py --list           # every scan point with its room and height
  python scripts/fetch_matterport.py --label "the main lobby"
  python scripts/fetch_matterport.py --pano-id <ID>   # override the pick
  python scripts/fetch_matterport.py --model zvKHR9DHNjJ   # another model (e.g. the quickstart's house)
  python scripts/fetch_matterport.py --import-faces DIR --pano-id <ID>   # manual fallback

Pano labels in the API are scan numbers ("22"), so the spoken name comes from --label.
Face links are signed and expire: they are used within one run, and the copy saved to
data/matterport/panoLocations.json has the signatures stripped.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import certifi
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from refs import check_pano  # noqa: E402

# Exact property selected by the user. Do not silently substitute another space
# when API credentials cannot access this model. --model is an explicit override.
MODEL_ID = "6sd6A5r5EVp"
DATA = ROOT / "data"
MP_DIR = DATA / "matterport"
PANO_DIR = DATA / "pano"
API_CFG = MP_DIR / "api.json"
ENV_FILE = ROOT / ".env.local"
LABEL_RE = re.compile(r"lobby|entr|foyer|reception|atrium|vestibule", re.I)
EXIT_NO_CREDENTIALS = 3
EXIT_NO_API_CONFIG = 2


# ---------------------------------------------------------------- config / credentials
def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """KEY=VALUE lines from .env.local. Values are returned, never printed."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        env[k] = v.strip().strip('"').strip("'")
    return env


def load_api_config(path: Path = API_CFG) -> dict:
    if not path.exists():
        print(f"{path} missing. Fill it from the quickstart (see {MP_DIR / 'api.example.json'}):\n"
              "  https://betaapp.matterport.com/2026-princeton-hackathon/quickstart\n"
              "Field names, endpoint and env var names must come from the quickstart, not guesses.",
              file=sys.stderr)
        raise SystemExit(EXIT_NO_API_CONFIG)
    cfg = json.loads(path.read_text())
    missing = [k for k in ("endpoint", "env_vars", "auth", "pano_query", "pano_list_path") if not cfg.get(k)]
    if missing:
        print(f"{path} incomplete, fill from the quickstart: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(EXIT_NO_API_CONFIG)
    return cfg


def credentials(cfg: dict) -> list[str] | None:
    """Credential values, or None when a network proxy injects the auth header for the
    API host (P3_AUTH_VIA_PROXY=1, e.g. a cloud session with a saved network secret)."""
    if os.environ.get("P3_AUTH_VIA_PROXY") == "1":
        return None
    env = {**load_env_file(), **{k: v for k, v in os.environ.items() if k in cfg["env_vars"]}}
    missing = [k for k in cfg["env_vars"] if not env.get(k)]
    if missing:
        print(f"Matterport credentials missing: create {ENV_FILE.name} with {', '.join(missing)} "
              "(human action A1). Stopping, as the Part 3 doc requires.", file=sys.stderr)
        raise SystemExit(EXIT_NO_CREDENTIALS)
    return [env[k] for k in cfg["env_vars"]]


def auth_headers(cfg: dict, creds: list[str] | None) -> dict[str, str]:
    if creds is None:
        return {}
    scheme = cfg["auth"]
    if scheme == "basic":
        tok = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
        return {"Authorization": f"Basic {tok}"}
    if scheme == "bearer":
        return {"Authorization": f"Bearer {creds[0]}"}
    if scheme.startswith("header:"):
        return {scheme.split(":", 1)[1]: creds[0]}
    raise ValueError(f"unsupported auth scheme {scheme!r} in api.json")


# ---------------------------------------------------------------- API
USER_AGENT = "blindsight-part3/1.0"  # Python's default "Python-urllib" agent is often refused by API firewalls


class ApiError(RuntimeError):
    pass


def tls_context():
    """Verified TLS on python.org macOS installs, honoring explicit CA configuration."""
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def graphql(cfg: dict, headers: dict, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(cfg["endpoint"], data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=60, context=tls_context()) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", "replace").strip()
        hint = {401: "the key or token is wrong", 403: "refused: wrong key/token, or the request was blocked"}
        raise ApiError(f"HTTP {e.code} from {cfg['endpoint']} ({hint.get(e.code, 'see the response')}). "
                       f"Response: {detail or '(empty)'}") from None
    if data.get("errors"):
        raise RuntimeError(f"API errors: {json.dumps(data['errors'])[:500]}")
    return data


def model_vars(cfg: dict, **extra) -> dict:
    """GraphQL variables: the model id under the name the configured query declares."""
    return {cfg.get("query_variable", "modelId"): MODEL_ID, **extra}


def redact_signatures(obj):
    """Drop query strings (signatures) from URLs before saving API responses."""
    if isinstance(obj, dict):
        return {k: redact_signatures(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_signatures(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("http") and "?" in obj:
        return obj.split("?", 1)[0] + "?<signature removed>"
    return obj


def dig(obj, path):
    for key in path:
        obj = obj[key]
    return obj


def pano_fields(p: dict, cfg: dict) -> tuple[str, str, np.ndarray]:
    f = cfg.get("fields", {})
    pid = str(dig(p, f.get("id", ["id"])))
    label = dig(p, f.get("label", ["label"])) if f.get("label", ["label"]) else None
    pos = dig(p, f.get("position", ["position"]))
    if isinstance(pos, dict):
        pos = [pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0)]
    return pid, (label or ""), np.asarray(pos, float)


def face_urls(cfg: dict, headers: dict, pano: dict, pano_id: str) -> list[str]:
    src = pano
    if cfg.get("skybox_query"):
        src = graphql(cfg, headers, cfg["skybox_query"], model_vars(cfg, panoId=pano_id))
    status_path = cfg.get("skybox_status_path")
    if status_path:
        status = dig(src, status_path)
        if status != "available":
            raise RuntimeError(f"skybox status for {pano_id} is {status!r}")
    urls = dig(src, cfg["face_urls_path"])
    if isinstance(urls, str):
        urls = [urls.format(face=i) for i in range(6)]
    if isinstance(urls, list) and urls and isinstance(urls[0], dict):
        urls = [dig(u, cfg.get("face_url_key", ["url"])) for u in urls]
    if len(urls) != 6:
        raise RuntimeError(f"expected 6 face URLs for {pano_id}, got {len(urls)}; check face_urls_path")
    return list(urls)


def download(url: str, dest: Path, headers: dict | None = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120, context=tls_context()) as r:
        data = r.read()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"not an image: {dest.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), img, [cv2.IMWRITE_JPEG_QUALITY, 95])


# ---------------------------------------------------------------- ranking
def horizontal(positions: np.ndarray) -> tuple[np.ndarray, int]:
    """Project positions to the floor plane: the axis with least spread is vertical."""
    v = int(np.argmin(positions.std(axis=0))) if len(positions) > 2 else 2
    keep = [i for i in range(3) if i != v]
    return positions[:, keep], v


def horizon_keypoints(pdir: Path) -> int:
    sift = cv2.SIFT_create()
    n = 0
    for k in range(1, 5):
        img = cv2.imread(str(pdir / f"face_{k}.jpg"), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            n += len(sift.detect(cv2.resize(img, (512, 512), interpolation=cv2.INTER_AREA), None))
    return n


def rank(panos: list[dict], kp: dict[str, int] | None = None) -> list[dict]:
    xy, _ = horizontal(np.array([p["pos"] for p in panos]))
    c = xy.mean(axis=0)
    d = np.linalg.norm(xy - c, axis=1)
    dn = d / (d.max() + 1e-9)
    kmax = max((kp or {}).values(), default=0) or 1
    out = []
    for p, dd in zip(panos, dn):
        lab = 1.0 if LABEL_RE.search(f"{p['label'] or ''} {p.get('room') or ''}") else 0.0
        kps = (kp or {}).get(p["id"], 0) / kmax
        out.append({**p, "score": round(2 * lab + (1 - dd) + kps, 4), "label_hit": bool(lab),
                    "centrality": round(1 - dd, 3), "horizon_kp": (kp or {}).get(p["id"]), "room": p.get("room")})
    return sorted(out, key=lambda r: -r["score"])


def topdown_plot(panos: list[dict], chosen: str, neighbors: set[str], path: Path) -> None:
    P = np.array([p["pos"] for p in panos])
    xy, v = horizontal(P)
    cz = P[[p["id"] for p in panos].index(chosen), v]
    lo, hi = xy.min(0), xy.max(0)
    span = max(float((hi - lo).max()), 1e-6)
    S, m = 900, 60
    img = np.full((S, S, 3), 255, np.uint8)
    pts = ((xy - lo) / span * (S - 2 * m) + m).astype(int)
    pts[:, 1] = S - pts[:, 1]
    for p, (x, y), z in zip(panos, pts, P[:, v]):
        col = (0, 0, 220) if p["id"] == chosen else (0, 140, 255) if p["id"] in neighbors else (120, 120, 120)
        same_floor = abs(z - cz) < 1.5
        cv2.circle(img, (x, y), 12 if p["id"] == chosen else 6, col, -1 if same_floor else 2)
        cv2.putText(img, (p["label"] or p["id"])[:18], (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    cv2.putText(img, f"model {MODEL_ID}: red = chosen pano, orange = downloaded neighbours", (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
    cv2.putText(img, "hollow = another floor; labels are Matterport scan numbers", (20, 54),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def spoken_label(chosen: dict, override: str | None) -> str | None:
    """Name used in the spoken location cue. API pano labels are scan numbers ("22"),
    which must not be spoken, so a label without letters is dropped."""
    if override:
        return override
    lab = (chosen.get("label") or "").strip()
    if re.search(r"[A-Za-z]", lab):
        return lab
    room = chosen.get("room")
    if room and "/" not in room and room != "unlabelled":
        return f"the {room.lower()}"
    return None


def write_pano_json(chosen: dict, panos: list[dict], downloaded: set[str], selected_by: str,
                    label: str | None = None, model_name: str | None = None) -> dict:
    _, v = horizontal(np.array([p["pos"] for p in panos]))
    pos_of = {p["id"]: np.asarray(p["pos"], float) for p in panos}  # 3D: other floors are far
    c = pos_of[chosen["id"]]
    meta = {
        "model_id": MODEL_ID, "model_name": model_name, "pano_id": chosen["id"], "label": label,
        "scan_label": chosen["label"] or None, "room": chosen.get("room"),
        "position": dict(zip("xyz", map(float, chosen["pos"]))), "vertical_axis": "xyz"[v],
        "selected_by": selected_by,
        "negatives": sorted(
            [{"pano_id": p["id"], "label": p["label"], "room": p.get("room"),
              "position": dict(zip("xyz", map(float, p["pos"]))),
              "dist_m": round(float(np.linalg.norm(pos_of[p["id"]] - c)), 2)}
             for p in panos if p["id"] != chosen["id"] and p["id"] in downloaded],
            key=lambda n: n["dist_m"]),
    }
    PANO_DIR.mkdir(parents=True, exist_ok=True)
    (PANO_DIR / "pano.json").write_text(json.dumps(meta, indent=1))
    return meta


# ---------------------------------------------------------------- rooms
def _chain(pairs: list[tuple[str, str]], verts: dict) -> list[tuple[float, float]]:
    """Order a room's boundary edges into a polygon; convex hull if they don't form one loop."""
    pairs = [q for q in pairs if q[0] in verts and q[1] in verts]
    if not pairs:
        return []
    order, used = [pairs[0][0], pairs[0][1]], {0}
    while len(used) < len(pairs):
        last = order[-1]
        nxt = next((i for i, q in enumerate(pairs) if i not in used and last in q), None)
        if nxt is None:
            break
        used.add(nxt)
        a, b = pairs[nxt]
        order.append(b if a == last else a)
    if len(used) < len(pairs) or order[-1] != order[0]:
        pts = np.array([verts[x] for q in pairs for x in q], np.float32)
        return [tuple(map(float, q)) for q in cv2.convexHull(pts).reshape(-1, 2)]
    return [verts[x] for x in order[:-1]]


def room_polygons(resp: dict) -> list[dict]:
    """Rooms (name = most confident classification) as floor-plan polygons, from the
    quickstart's Rooms query: wall-graph vertices joined by edges, rooms as edge loops."""
    m = resp["data"]["model"]
    verts, edges = {}, {}
    for f in m.get("floors") or []:
        for vx in f.get("vertices") or []:
            verts[vx["id"]] = (float(vx["position"]["x"]), float(vx["position"]["y"]))
        for e in f.get("edges") or []:
            ids = [x["id"] for x in e.get("vertices") or []]
            if len(ids) == 2:
                edges[e["id"]] = (ids[0], ids[1])
    rooms = []
    for r in m.get("rooms") or []:
        cls = sorted(r.get("classifications") or [], key=lambda c: -(c.get("confidence") or 0))
        label = cls[0].get("label") if cls else None
        if label in (None, "", "Void"):
            continue
        eids = [e["id"] for e in ((r.get("boundary") or {}).get("edges") or [])]
        poly = _chain([edges[e] for e in eids if e in edges], verts)
        if len(poly) >= 3:
            rooms.append({"id": r["id"], "label": label, "poly": np.array(poly, np.float32)})
    return rooms


def assign_rooms(panos: list[dict], rooms: list[dict]) -> None:
    """Name the room each scan point stands in. Floors overlap in x, y and scan points
    carry no floor id, so in a multi-storey building a point can fall inside rooms on
    two floors: then both names are given, "Lobby / Office" (the z column tells floors)."""
    for p in panos:
        x, y = float(p["pos"][0]), float(p["pos"][1])
        hits = [r for r in rooms if cv2.pointPolygonTest(r["poly"], (x, y), False) >= 0]
        hits.sort(key=lambda r: cv2.contourArea(r["poly"]))
        p["room"] = " / ".join(dict.fromkeys(r["label"] for r in hits)) or None


def print_table(panos: list[dict]) -> None:
    print(f"{'id':<28} {'scan':>4}  {'room':<26} {'x':>7} {'y':>7} {'z':>6}")
    for p in sorted(panos, key=lambda q: (round(q["pos"][2]), q.get("room") or "~", int(q["label"])
                                          if str(q["label"]).isdigit() else 0)):
        x, y, z = p["pos"]
        print(f"{p['id']:<28} {str(p['label'] or '-'):>4}  {(p.get('room') or '-')[:26]:<26} "
              f"{x:7.2f} {y:7.2f} {z:6.2f}")


# ---------------------------------------------------------------- fallback
def import_faces(src: Path, pano_id: str) -> None:
    """Manual fallback: copy face_0..5.jpg (or four horizon screenshots face_1..4)."""
    dst = PANO_DIR / pano_id
    dst.mkdir(parents=True, exist_ok=True)
    sizes = []
    for k in range(6):
        f = src / f"face_{k}.jpg"
        if f.exists():
            shutil.copy(f, dst / f.name)
            sizes.append(cv2.imread(str(f)).shape[:2])
    if not sizes:
        raise SystemExit(f"no face_*.jpg in {src}")
    S = min(min(s) for s in sizes)
    for k in range(6):
        f = dst / f"face_{k}.jpg"
        img = cv2.imread(str(f)) if f.exists() else None
        if img is None:  # up/down missing in the screenshot fallback: neutral grey, logged
            img = np.full((S, S, 3), 128, np.uint8)
            print(f"face_{k} missing: wrote a neutral placeholder (log this fallback in NOTES-part3.md)")
        if img.shape[0] != img.shape[1] or img.shape[0] != S:
            h, w = img.shape[:2]
            m = min(h, w)
            img = cv2.resize(img[(h - m) // 2:(h + m) // 2, (w - m) // 2:(w + m) // 2], (S, S))
        cv2.imwrite(str(f), img, [cv2.IMWRITE_JPEG_QUALITY, 95])


# ---------------------------------------------------------------- access check
def check_access(cfg: dict, headers: dict) -> int:
    """Quickstart section 1 key check, then whether the key can see MODEL_ID."""
    root = graphql(cfg, headers, cfg["key_check_query"], {}) if cfg.get("key_check_query") else None
    if root:
        rf = root["data"]["rootFolder"]
        print(f"Key OK. '{rf.get('name')}' holds {rf.get('modelCountSummary')} models")
    try:
        d = graphql(cfg, headers, cfg["model_check_query"], model_vars(cfg))
        m = d["data"]["model"]
    except Exception as ex:  # noqa: BLE001
        print(f"Model {MODEL_ID}: NOT reachable with this key ({type(ex).__name__}: {str(ex)[:300]})")
        return 4
    if not m:
        print(f"Model {MODEL_ID}: NOT reachable with this key (no model returned)")
        return 4
    print(f"Model {MODEL_ID}: '{m.get('name')}', {len(m.get('panoLocations') or [])} panoLocations, "
          f"visibility {m.get('visibility')}")
    return 0


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    global MODEL_ID
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-json", type=Path, default=None, help="use a saved panoLocations response")
    ap.add_argument("--pano-id", default=None, help="force this pano instead of the ranked pick")
    ap.add_argument("--neighbors", type=int, default=20,
                    help="nearest scan points to download (hard negatives and competing places)")
    ap.add_argument("--candidates", type=int, default=5, help="top candidates whose faces are scored by keypoints")
    ap.add_argument("--import-faces", type=Path, default=None, help="manual fallback: directory of face_*.jpg")
    ap.add_argument("--model", default=MODEL_ID, help="Matterport model id (default: the doc's demo model)")
    ap.add_argument("--label", default=None, help='spoken place name, e.g. "the main lobby"')
    ap.add_argument("--check", action="store_true", help="only check the key and whether it can see the model")
    ap.add_argument("--list", action="store_true", help="only list scan points with their rooms; download nothing")
    a = ap.parse_args(argv)
    MODEL_ID = a.model

    if a.import_faces:
        if not a.pano_id:
            raise SystemExit("--import-faces needs --pano-id")
        import_faces(a.import_faces, a.pano_id)
        if not (PANO_DIR / "pano.json").exists():
            (PANO_DIR / "pano.json").write_text(json.dumps({"model_id": MODEL_ID, "pano_id": a.pano_id,
                                                            "label": None, "selected_by": "manual import",
                                                            "negatives": []}, indent=1))
        print(json.dumps(check_pano(PANO_DIR / a.pano_id)["chosen"], indent=1))
        return 0

    cfg = load_api_config(API_CFG)
    headers: dict = {}
    if a.check:
        try:
            return check_access(cfg, auth_headers(cfg, credentials(cfg)))
        except ApiError as ex:
            print(f"Key check FAILED: {ex}", file=sys.stderr)
            print("Check .env.local: two lines, MATTERPORT_API_KEY=<API key> and MATTERPORT_API_TOKEN=<API token>, "
                  "values from /costar-keys with no quotes or spaces.", file=sys.stderr)
            return 5
    if a.from_json:
        raw = json.loads(a.from_json.read_text())
    else:
        headers = auth_headers(cfg, credentials(cfg))
        raw = graphql(cfg, headers, cfg["pano_query"], model_vars(cfg))
        MP_DIR.mkdir(parents=True, exist_ok=True)
        (MP_DIR / "panoLocations.json").write_text(json.dumps(redact_signatures(raw), indent=1))
    model_name = dig(raw, cfg["model_name_path"]) if cfg.get("model_name_path") else None
    plist = dig(raw, cfg["pano_list_path"])
    panos = []
    for p in plist:
        pid, label, pos = pano_fields(p, cfg)
        panos.append({"id": pid, "label": label, "pos": pos.tolist(), "raw": p})
    print(f"{len(panos)} panoLocations for model {MODEL_ID}" + (f" ({model_name})" if model_name else ""))
    if cfg.get("rooms_query") and not a.from_json:
        try:
            assign_rooms(panos, room_polygons(graphql(cfg, headers, cfg["rooms_query"], model_vars(cfg))))
        except Exception as ex:  # noqa: BLE001 - room names are a convenience
            print(f"room names unavailable ({type(ex).__name__}: {str(ex)[:200]})", file=sys.stderr)
    print_table(panos)
    if a.list:
        return 0

    if not headers and (cfg.get("skybox_query") or cfg.get("faces_need_auth")):
        headers = auth_headers(cfg, credentials(cfg))
    by_id = {p["id"]: p for p in panos}

    def fetch_faces(pid: str) -> bool:
        d = PANO_DIR / pid
        if all((d / f"face_{k}.jpg").exists() for k in range(6)):
            return True
        try:
            urls = face_urls(cfg, headers, by_id[pid]["raw"], pid)
            for k, u in enumerate(urls):
                download(u, d / f"face_{k}.jpg", headers if cfg.get("faces_need_auth") else None)
            return True
        except Exception as ex:  # noqa: BLE001
            print(f"faces for {pid} failed: {type(ex).__name__}: {str(ex)[:200]}", file=sys.stderr)
            return False

    prelim = rank(panos)
    kp = {}
    for p in prelim[: a.candidates]:
        if fetch_faces(p["id"]):
            kp[p["id"]] = horizon_keypoints(PANO_DIR / p["id"])
    ranked = rank(panos, kp)
    MP_DIR.mkdir(parents=True, exist_ok=True)
    (MP_DIR / "candidates.json").write_text(json.dumps([{k: v for k, v in r.items() if k != "raw"}
                                                        for r in ranked], indent=1))
    chosen = by_id[a.pano_id] if a.pano_id else next(r for r in ranked if r["id"] in kp or not kp)
    if not fetch_faces(chosen["id"]):
        raise SystemExit("could not download the chosen pano's faces: use the manual fallback (--import-faces)")
    P = np.array([p["pos"] for p in panos], float)  # 3D distance: a scan point upstairs is not a neighbour
    ci = [p["id"] for p in panos].index(chosen["id"])
    order = np.argsort(np.linalg.norm(P - P[ci], axis=1))
    neighbors = [panos[i]["id"] for i in order if panos[i]["id"] != chosen["id"]][: a.neighbors]
    downloaded = {pid for pid in neighbors if fetch_faces(pid)} | {chosen["id"]}
    why = "--pano-id override" if a.pano_id else "fetch_matterport.py ranking (label, centrality, horizon keypoints)"
    label = spoken_label(chosen, a.label)
    meta = write_pano_json(chosen, panos, downloaded, why, label, model_name)
    topdown_plot(panos, chosen["id"], set(neighbors), MP_DIR / "topdown.png")
    rep = check_pano(PANO_DIR / chosen["id"])
    for n in meta["negatives"]:
        check_pano(PANO_DIR / n["pano_id"])
    print(json.dumps({"PANO_ID": chosen["id"], "label": label, "scan_label": chosen["label"],
                      "room": chosen.get("room"),
                      "negatives": len(meta["negatives"]), "layout_ok": rep["ok"], "layout": rep["chosen"]}, indent=1))
    if not label:
        print('no spoken label: the scan label is a number. Re-run with --label "the main lobby" '
              "(or edit \"label\" in data/pano/pano.json)", file=sys.stderr)
    print(f"look at {MP_DIR/'topdown.png'}, {PANO_DIR/chosen['id']/'horizon.jpg'} and "
          f"{PANO_DIR/chosen['id']/'contact_cross.jpg'} before the demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
