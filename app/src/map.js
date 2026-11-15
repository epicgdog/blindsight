// Navigation screen helpers (no React) so they can be unit tested in node. The map math is copied
// from server/viewer/logic.js: Metro cannot import files outside app/.
import { serverBase } from "./voice.js";

export const hasXY = (p) => Number.isFinite(p?.x) && Number.isFinite(p?.y);

// ---- frame result fields ----
// The match evidence of every frame: r.match is null on unmatched frames, the debug copy is not.
export const frameMatch = (r) => r?.match ?? r?.localization_debug?.match ?? null;

// Tracked camera position (mesh frame, metres), kept by the server between fixes.
export const framePosition = (r) => {
  const p = frameMatch(r)?.position;
  return hasXY(p) ? p : null;
};

export const placeName = (id, info = {}) => {
  const p = info[id];
  if (p?.room) return p.room;
  if (p?.label) return `Scan ${p.label}`;
  return String(id).slice(0, 8);
};

export const simPercent = (sim) => Math.max(0, Math.min(100, Math.round(sim * 100)));

// Server order is already best-first; mark the pano the localizer is locked to.
export const rankRows = (match, activePano, info = {}, max = 5) =>
  (match?.ranking ?? []).slice(0, max).map((r, i) => ({
    rank: i + 1,
    place: r.place,
    name: placeName(r.place, info),
    scan: info[r.place]?.label ?? null,
    percent: simPercent(r.sim),
    inliers: r.inliers ?? 0,
    here: r.place === activePano,
  }));

// Candidate screening before the pick (server/localize.py _screen).
export const VERDICT = { kept: "kept", pitch: "ruled out: pitch vs phone", far: "ruled out: too far from track", still: "ruled out: standing still" };

// Plain-language reasons this frame was not accepted, from the localizer's debug block.
export function whyLines(d) {
  if (!d) return [];
  const out = [];
  if (d.reason) out.push(`Reason: ${String(d.reason).replaceAll("_", " ")}`);
  out.push(`Matched this frame: ${d.matched ? "yes" : "no"} · best inliers ${d.best_inliers ?? 0} (need ${d.thresholds?.N_MIN ?? "?"}) · grid ${d.best_grid_cells ?? 0}/9 (need ${d.thresholds?.GRID_CELLS_MIN ?? "?"})`);
  if (d.competitor_inliers) out.push(`Competing view inliers: ${d.competitor_inliers}`);
  if (d.imu_rejected) out.push(`Rejected by pitch gate: match pitch vs phone ${d.imu_pitch}°`);
  if (d.jump_rejected) out.push("Rejected: too far from the tracked position");
  if (d.distinctive_evidence && d.distinctive_evidence.required && !d.distinctive_evidence.passed)
    out.push(`Rejected: only ${d.distinctive_evidence.count} distinctive inliers vs the other place`);
  if (d.pending_jump) out.push("Large yaw jump waiting for a second frame");
  if (d.embedded === false) out.push("Retrieval reused from an earlier frame (EMBED_EVERY_N_FRAMES)");
  // Candidates are tuples from the main localizer and objects from the property localizer.
  const cands = (Array.isArray(d.candidates) ? d.candidates : []).map((x) => Array.isArray(x) ? x
    : [x?.name ?? x?.ref ?? "", x?.place ?? x?.pano_id ?? "", x?.inliers ?? 0, x?.grid ?? x?.grid_cells ?? 0, x?.verdict]);
  const c = [...cands].sort((a, b) => (b[2] ?? 0) - (a[2] ?? 0)).slice(0, 4);
  if (d.screened === false && cands.some((x) => x[4] && x[4] !== "kept"))
    out.push("No plausible candidate passed; judged unfiltered (lets the tracker restart on a repeated far match)");
  if (c.length) out.push("Verified: " + c.map(([name, place, inl, grid, v]) => `${String(place).slice(0, 6)}/${name} ${inl} in, ${grid} cells${v && v !== "kept" ? ` [${VERDICT[v] ?? v}]` : ""}`).join(" · "));
  if (d.total != null) out.push(`Localizer ${d.total} ms (embed ${d.embed}, extract ${d.extract}, verify ${d.verify})`);
  return out;
}

// Detection box (normalized x1, y1, x2, y2) as percentage offsets for an absolutely placed View.
export function boxStyle([x1, y1, x2, y2]) {
  const p = (v) => `${+(v * 100).toFixed(2)}%`;
  return { left: p(x1), top: p(y1), width: p(x2 - x1), height: p(y2 - y1) };
}

export const headingText = (h) =>
  !h ? "–" : Number.isFinite(h.heading_deg)
    ? `${Math.round(h.heading_deg)}° ±${h.sigma_deg ?? "?"}° (${h.source ?? "?"})`
    : "unknown";

// ---- server urls (http base from the websocket url, like talk.js / ack.js) ----
export const panoUrl = (wsUrl) => { const b = serverBase(wsUrl); return b ? `${b}/api/pano` : null; };
export const metricsUrl = (wsUrl) => { const b = serverBase(wsUrl); return b ? `${b}/metrics` : null; };
// Dropped pin: preview (GET, no change) and set or clear (POST).
export const destUrl = (wsUrl, x, y) => {
  const b = serverBase(wsUrl);
  if (!b) return null;
  return x == null ? `${b}/api/agent/destination` : `${b}/api/agent/destination?x=${x.toFixed(2)}&y=${y.toFixed(2)}`;
};
export const navUrl = (wsUrl, on) => { const b = serverBase(wsUrl); return b ? `${b}/api/agent/nav?on=${on ? 1 : 0}` : null; };

// ---- static map: ground floor only, like the web viewer ----
const MARGIN_M = 2;
// Scan point at the building entrance (scan 28), the fallback pin when no route is known yet.
export const ENTRANCE_ID = "n0ppizy9u8xcadypd2bawceyc";

// Floor-0 extent in metres: scan points plus a margin, else the server's bounds.
export function floorExtent(map) {
  const here = (map?.points ?? []).filter((p) => (p.floor ?? 0) === 0);
  if (!here.length) {
    const b = map?.bounds;
    return b ? { minX: b.min_x, maxX: b.max_x, minY: b.min_y, maxY: b.max_y } : null;
  }
  const xs = here.map((p) => p.x), ys = here.map((p) => p.y);
  return { minX: Math.min(...xs) - MARGIN_M, maxX: Math.max(...xs) + MARGIN_M, minY: Math.min(...ys) - MARGIN_M, maxY: Math.max(...ys) + MARGIN_M };
}

// All walls as one SVG path in map metres (drawn under the view matrix), one element for speed.
export const wallPath = (map) =>
  (map?.walls ?? []).map(([x1, y1, x2, y2]) => `M${+x1.toFixed(2)} ${+y1.toFixed(2)}L${+x2.toFixed(2)} ${+y2.toFixed(2)}`).join("");

// The route as SVG polyline points in map metres.
export const routePoints = (path) =>
  path?.pts?.length > 1 ? path.pts.map(([x, y]) => `${+x.toFixed(2)},${+y.toFixed(2)}`).join(" ") : null;

export function entrancePoint(path, map) {
  if (path?.goal) return { x: path.goal[0], y: path.goal[1] };
  const p = (map?.points ?? []).find((q) => q.id === ENTRANCE_ID);
  return p ? { x: p.x, y: p.y } : null;
}

// ---- view: map metres to screen ----
// A view is { cx, cy, rot, scale, ax, ay }: map point (cx, cy) lands on screen (ax, ay), the map is
// turned so heading `rot` (clockwise from +y, north) points up, `scale` px per metre. y flips so
// north is up when rot is 0.
export function project(v, x, y) {
  const h = (v.rot * Math.PI) / 180, c = Math.cos(h), s = Math.sin(h);
  const dx = x - v.cx, dy = y - v.cy;
  return [v.ax + v.scale * (dx * c - dy * s), v.ay - v.scale * (dx * s + dy * c)];
}

// The same mapping as an SVG matrix(a b c d e f), for a group holding everything in metres.
export function viewMatrix(v) {
  const h = (v.rot * Math.PI) / 180, c = Math.cos(h), s = Math.sin(h), k = v.scale;
  const m = [k * c, -k * s, -k * s, -k * c, v.ax - k * (v.cx * c - v.cy * s), v.ay + k * (v.cx * s + v.cy * c)];
  return `matrix(${m.map((n) => +n.toFixed(4)).join(" ")})`;
}

// Screen angle (clockwise from up) of a map heading under this view.
export const screenAngle = (v, headingDeg) => headingDeg - v.rot;

// North-up overview: the whole extent fitted into rect { x, y, w, h }.
export function fitView(ext, rect, pad = 24) {
  if (!ext) return null;
  const spanX = Math.max(ext.maxX - ext.minX, 1e-6), spanY = Math.max(ext.maxY - ext.minY, 1e-6);
  const scale = Math.min((rect.w - 2 * pad) / spanX, (rect.h - 2 * pad) / spanY);
  return { cx: (ext.minX + ext.maxX) / 2, cy: (ext.minY + ext.maxY) / 2, rot: 0, scale: Math.max(scale, 1e-3),
    ax: rect.x + rect.w / 2, ay: rect.y + rect.h / 2 };
}

// Heading-up: you at the anchor, facing up.
export const followView = (pos, headingDeg, anchor, scale) =>
  ({ cx: pos.x, cy: pos.y, rot: headingDeg, scale, ax: anchor.x, ay: anchor.y });

export const FOLLOW_PX_PER_M = 30;

// follow only when there is something to follow and a facing to turn the map by.
export const mapMode = (pref, position, headingDeg) =>
  pref === "follow" && hasXY(position) && Number.isFinite(headingDeg) ? "follow" : "overview";

// ---- tilt: the map layer is a flat SVG under perspective + rotateX, Google Maps style ----
export const TILT_DEG = 50;

// The layer is larger than the screen (centred) so the tilted plane still covers it: at 50 degrees
// with perspective 3h, 1.4 x 2.4 is enough to reach the top corners. Kept that small because the
// SVG is rasterised at full size. Perspective scales with the height so every phone looks the same.
export const LAYER_W = 1.4, LAYER_H = 2.4;
export function mapLayout(w, h) {
  const lw = Math.round(LAYER_W * w), lh = Math.round(LAYER_H * h);
  const left = -(lw - w) / 2, top = -(lh - h) / 2;
  return { lw, lh, left, top, perspective: 3 * h, region: { x: -left, y: -top, w, h } };
}

// Flat layer offset (u, v) from the layer centre -> on-screen offset after rotateX(tilt) under
// perspective p (top recedes). Mirrors the RN transform [{ perspective }, { rotateX }].
export function tiltPoint(u, v, tiltDeg, p) {
  const t = (tiltDeg * Math.PI) / 180;
  const y = v * Math.cos(t), z = v * Math.sin(t);
  const k = p / (p - z);
  return [u * k, y * k];
}

// Inverse for the vertical axis: the flat offset that lands at screen offset ys.
export function untiltY(ys, tiltDeg, p) {
  const t = (tiltDeg * Math.PI) / 180;
  return (ys * p) / (Math.cos(t) * p + ys * Math.sin(t));
}

// Where the puck sits in follow mode, in layer coordinates: lower third of the screen after tilt.
export function followAnchor(layout, tiltDeg = TILT_DEG) {
  const screenH = layout.region.h;
  const ys = screenH * (2 / 3) - screenH / 2; // lower third, from the screen centre
  return { x: layout.lw / 2, y: layout.lh / 2 + untiltY(ys, tiltDeg, layout.perspective) };
}

// ---- gliding between frame results (about 2 Hz) ----
export const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : 1 - (1 - t) ** 3);

export function lerpAngle(a, b, t) {
  const d = ((((b - a) % 360) + 540) % 360) - 180; // shortest way round
  return a + d * t;
}

const ANGLES = new Set(["rot", "ph"]);

// Interpolate every numeric field; angles the short way. Non-numbers (null heading) take b.
export function lerpView(a, b, t) {
  if (!a || !b) return b;
  const out = {};
  for (const k of Object.keys(b)) {
    const x = a[k], y = b[k];
    if (!Number.isFinite(x) || !Number.isFinite(y)) out[k] = y;
    else out[k] = ANGLES.has(k) ? lerpAngle(x, y, t) : x + (y - x) * t;
  }
  return out;
}

// ---- banner and bottom bar ----
// Arrow for a signed turn (positive is right), degrees.
export function turnArrow(deg) {
  if (!Number.isFinite(deg)) return null;
  const a = Math.abs(deg), side = deg > 0 ? "right" : "left";
  if (a <= 15) return "straight";
  if (a <= 45) return `slight-${side}`;
  if (a <= 110) return side;
  if (a < 150) return `sharp-${side}`;
  return "uturn";
}

// Arrow for the next maneuver word from the guide ("turn left", "turn right", "turn around", "the entrance").
export const nextArrow = (next) =>
  next === "turn left" ? "left" : next === "turn right" ? "right" : next === "turn around" ? "uturn" : "straight";

const ARROW_TEXT = { "slight-left": "Turn slightly left", "slight-right": "Turn slightly right", left: "Turn left", right: "Turn right",
  "sharp-left": "Turn sharp left", "sharp-right": "Turn sharp right", uturn: "Turn around" };

// Show "turn" in the banner only when facing well away from the route; a smaller error is
// left to the walk (the heading itself is only good to about 10-20 degrees).
export const ALIGN_DEG = 60;

export const metersText = (m) => (Number.isFinite(m) ? `${Math.max(0, Math.round(m))} m` : "");
export const leftText = (m) => (Number.isFinite(m) ? `${metersText(m)} left` : "");

export const WALK_MPS = 1.0;
export const timeLeftS = (m, speed = WALK_MPS) => (Number.isFinite(m) ? Math.max(0, m) / speed : null);
export function durationText(s) {
  if (!Number.isFinite(s)) return "";
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.ceil(s / 60)} min`;
}

const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);

// Banner content: { title, meters, arrow, tone } for every screen state.
export function banner({ connected, nav, position }) {
  if (!connected) return { title: "Connecting…", meters: "", arrow: null, tone: "wait" };
  if (nav?.arrived) return { title: `You're at ${nav.destination ?? "the entrance"}`, meters: "", arrow: "arrive", tone: "done" };
  if (!nav?.active) return { title: "Add a pin to choose where to go", meters: "", arrow: null, tone: "off" };
  if (!hasXY(position) || nav.paused || !nav.next) return { title: "Hold steady, finding you…", meters: "", arrow: null, tone: "wait" };
  const meters = metersText(nav.next_m);
  // Facing away from the route: the turn to make now comes first.
  if (Number.isFinite(nav.turn_deg) && Math.abs(nav.turn_deg) > ALIGN_DEG) {
    const arrow = turnArrow(nav.turn_deg);
    return { title: ARROW_TEXT[arrow] ?? "Turn", meters: "", arrow, tone: "go" };
  }
  if (nav.next_is_destination || nav.next === "the entrance")
    return { title: `Continue to ${nav.destination ?? nav.next}`, meters, arrow: "straight", tone: "go" };
  return { title: cap(nav.next), meters, arrow: nextArrow(nav.next), tone: "go" };
}

// Bottom bar: time and metres left while guiding, else nothing to show.
export function bottomBar(nav) {
  if (!nav?.active || !Number.isFinite(nav.remaining_m)) return { time: "", left: "" };
  return { time: durationText(timeLeftS(nav.remaining_m)), left: leftText(nav.remaining_m) };
}

export const accuracyText = (p) => (hasXY(p) && Number.isFinite(p.sigma_m) ? `±${p.sigma_m.toFixed(1)} m` : "");
