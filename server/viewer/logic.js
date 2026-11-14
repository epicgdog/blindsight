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
