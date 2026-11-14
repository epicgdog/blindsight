// Pure viewer helpers (no DOM) so they can be unit tested in node.
export const stateClass = (s) =>
  ({ LOCKED: "state-locked", UNCERTAIN: "state-uncertain", LOST: "state-lost" })[s] ?? "state-lost";

export const pushHistory = (h, v, max = 60) => [...h, v].slice(-max);

export function sparklinePoints(h, w, ht) {
  if (!h.length) return "";
  const lo = Math.min(...h), hi = Math.max(...h), span = hi - lo || 1;
  return h
    .map((v, i) => {
      const x = h.length === 1 ? 0 : (i / (h.length - 1)) * w;
      const y = hi === lo ? ht / 2 : ht - ((v - lo) / span) * ht;
      return `${+x.toFixed(2)},${+y.toFixed(2)}`;
    })
    .join(" ");
}

export function boxStyle([x1, y1, x2, y2]) {
  const p = (v) => `${+(v * 100).toFixed(2)}%`;
  return { left: p(x1), top: p(y1), width: p(x2 - x1), height: p(y2 - y1) };
}

export function appendCue(log, cue, ts, max = 50) {
  if (!cue || log.some((e) => e.id === cue.id && e.text === cue.text && ts - e.ts < 5000)) return log;
  return [...log, { id: cue.id, text: cue.text, ts }].slice(-max);
}

export function fpsFromTimes(times) {
  if (times.length < 2) return 0;
  const dt = times[times.length - 1] - times[0];
  return dt > 0 ? ((times.length - 1) * 1000) / dt : 0;
}

// best_ref may be an index, a face URL, or a face name such as "face_2" (Part 1 types it as str | None).
const stem = (u) => String(u).split("/").pop().replace(/\.[a-z]+$/i, "");
export const refStrip = (urls, best) =>
  urls.map((url, i) => ({
    url, index: i,
    highlight: best != null && (url === best || i === best || stem(url) === stem(best)),
  }));

export const clock = (ms) => new Date(ms).toTimeString().slice(0, 8);

// ---- live pano ranking and footprint ----
export const placeName = (id, info = {}) => {
  const p = info[id];
  if (p?.room) return p.room;
  if (p?.label) return `Scan ${p.label}`;
  return String(id).slice(0, 8);
};

export const thumbUrl = (place) => `/data/pano/${place}/face_2.jpg`;

export const simPercent = (sim) => Math.max(0, Math.min(100, Math.round(sim * 100)));

// Server order is already best-first; mark the pano the localizer is locked to.
export const rankRows = (match, activePano, info = {}) =>
  (match?.ranking ?? []).map((r, i) => ({
    rank: i + 1,
    place: r.place,
    name: placeName(r.place, info),
    percent: simPercent(r.sim),
    inliers: r.inliers ?? 0,
    here: r.place === activePano,
    thumb: thumbUrl(r.place),
  }));

// Footprint x is unwrapped around the view centre, so draw copies at -1, 0 and +1 panos wide.
export function footprintPaths(poly, w, h) {
  if (!poly?.length) return [];
  const pts = (dx) => poly.map(([x, y]) => `${+(x * w + dx).toFixed(2)},${+(y * h).toFixed(2)}`).join(" ");
  return [pts(-w), pts(0), pts(w)];
}

// ---- regions of the pano that are ignored (people, furniture) ----
// Draw each region once, plus a wrapped copy when it crosses the left or right edge.
export function maskPolygons(regions, w, h) {
  const out = [];
  for (const poly of regions ?? []) {
    const xs = poly.map((p) => p[0] * w);
    const lo = Math.min(...xs), hi = Math.max(...xs);
    for (const dx of [0, -w, w]) {
      if (hi + dx < 0 || lo + dx > w) continue;
      out.push(poly.map(([x, y]) => `${+(x * w + dx).toFixed(2)},${+(y * h).toFixed(2)}`).join(" "));
    }
  }
  return out;
}

export const maskSummary = (n) => (n > 0 ? `${n} region${n === 1 ? "" : "s"} ignored (people, furniture)` : "");

// ---- minimap: scan points seen from above, floors split by height ----
// Uniform scale so distances keep their shape; y flips so larger y (north) is at the top.
export function mapProject(points, bounds, w, h, pad = 20) {
  if (!points?.length || !bounds) return {};
  const spanX = Math.max(bounds.max_x - bounds.min_x, 1e-6);
  const spanY = Math.max(bounds.max_y - bounds.min_y, 1e-6);
  const scale = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
  const offX = (w - spanX * scale) / 2, offY = (h - spanY * scale) / 2;
  const out = {};
  for (const p of points) {
    out[p.id] = {
      x: +(offX + (p.x - bounds.min_x) * scale).toFixed(2),
      y: +(h - offY - (p.y - bounds.min_y) * scale).toFixed(2),
    };
  }
  return out;
}

export const floorOfPlace = (points, id) => points?.find((p) => p.id === id)?.floor ?? null;

export function pushTrail(trail, place, max = 8) {
  if (!place) return trail;
  const next = trail.filter((p) => p !== place);
  next.push(place);
  return next.slice(-max);
}

const scanName = (p) => (p?.room ? p.room : p?.label != null ? `Scan ${p.label}` : "");

// Markers for the active floor: the active place, ranked candidates, recent trail, other points.
export function mapMarkers(map, activeId, ranking, trail, w, h) {
  if (!map?.points?.length) return { floor: null, name: "", markers: [] };
  const ranked = (ranking ?? []).map((r) => r.place);
  const focus = activeId ?? ranked[0] ?? null;
  const floor = floorOfPlace(map.points, focus) ?? 0;
  const here = map.points.filter((p) => p.floor === floor);
  const proj = mapProject(here, map.bounds, w, h, 24);
  const markers = here.map((p) => {
    let kind = "point";
    if (p.id === activeId) kind = "active";
    else if (ranked.slice(0, 3).includes(p.id)) kind = "candidate";
    else if ((trail ?? []).includes(p.id)) kind = "trail";
    return { id: p.id, kind, x: proj[p.id].x, y: proj[p.id].y, rank: ranked.indexOf(p.id) + 1 };
  });
  const active = map.points.find((p) => p.id === activeId);
  return { floor, name: active ? scanName(active) : "", markers };
}

// ---- readouts and errors ----
export const yawText = (yaw) => (Number.isFinite(yaw) ? `${Math.round(yaw)}°` : "–");

export function errorText(status, body) {
  if (status >= 200 && status < 300) return "";
  if (typeof body?.detail === "string") return body.detail;
  return status >= 500 ? `Server error (${status})` : `Request refused (${status})`;
}
