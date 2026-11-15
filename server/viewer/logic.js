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
    scan: info[r.place]?.label ?? null,
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
  if (map.walls?.length) return wallMarkers(map, activeId, ranking, trail);
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



// ---- minimap from the mesh: wall outlines and scan points, floor 1 only ----
const WALL_PX_PER_M = 40;
const WALL_MARGIN_M = 2;

function wallMarkers(map, activeId, ranking, trail) {
  const ranked = (ranking ?? []).map((r) => r.place);
  const floor = 0;
  const here = map.points.filter((p) => p.floor === floor);
  const xs = here.map((p) => p.x), ys = here.map((p) => p.y);
  const minX = Math.min(...xs) - WALL_MARGIN_M, maxX = Math.max(...xs) + WALL_MARGIN_M;
  const minY = Math.min(...ys) - WALL_MARGIN_M, maxY = Math.max(...ys) + WALL_MARGIN_M;
  const px = (x) => +((x - minX) * WALL_PX_PER_M).toFixed(2);
  const py = (y) => +((maxY - y) * WALL_PX_PER_M).toFixed(2); // north (larger y) at the top
  const activePoint = map.points.find((p) => p.id === activeId);
  const estimateId = activePoint ? null : ranked[0] ?? null;
  const focus = map.points.find((p) => p.id === (activeId ?? estimateId));
  const otherFloor = !!focus && focus.floor !== floor;
  const markers = here.map((p) => {
    let kind = "point";
    if (p.id === activeId) kind = "active";
    else if (p.id === estimateId) kind = "estimate";
    else if (ranked.slice(0, 3).includes(p.id)) kind = "candidate";
    else if ((trail ?? []).includes(p.id)) kind = "trail";
    return { id: p.id, kind, x: px(p.x), y: py(p.y), rank: ranked.indexOf(p.id) + 1 };
  });
  const walls = map.walls.map(([x1, y1, x2, y2]) => [px(x1), py(y1), px(x2), py(y2)]);
  return {
    floor,
    name: focus ? scanName(focus) : "",
    markers,
    walls,
    viewBox: { x: 0, y: 0, w: +((maxX - minX) * WALL_PX_PER_M).toFixed(2), h: +((maxY - minY) * WALL_PX_PER_M).toFixed(2) },
    otherFloor,
  };
}

// ---- 3D map: scan points at their real heights, walls extruded from the mesh outline ----
// Matterport frame: x, y on the floor plane (meters), z up. Scan points sit at camera
// height, so the floor under floor N is its mean point height minus this (an assumption,
// not read from the API).
export const CAM_HEIGHT_M = 1.4;
export const WALL_HEIGHT_M = 2.5;
// Scan point at the building entrance (scan 28), found by standing at the door with the phone.
export const ENTRANCE_ID = "n0ppizy9u8xcadypd2bawceyc";

export function scene3d(map, activeId, ranking, trail) {
  if (!map?.points?.length) return null;
  const ranked = (ranking ?? []).map((r) => r.place);
  const activePoint = map.points.find((p) => p.id === activeId);
  const estimateId = activePoint ? null : ranked[0] ?? null;
  const focus = map.points.find((p) => p.id === (activeId ?? estimateId)) ?? null;
  const points = map.points.map((p) => {
    let kind = "point";
    if (p.id === activeId) kind = "active";
    else if (p.id === estimateId) kind = "estimate";
    else if (ranked.slice(0, 3).includes(p.id)) kind = "candidate";
    else if ((trail ?? []).includes(p.id)) kind = "trail";
    const out = { id: p.id, kind, x: p.x, y: p.y, z: p.z, floor: p.floor ?? 0, rank: ranked.indexOf(p.id) + 1 };
    if (p.id === ENTRANCE_ID) out.label = "Entrance";
    return out;
  });
  const floors = (map.floors ?? []).map((f) => ({ floor: f.floor, base_z: +(f.z - CAM_HEIGHT_M).toFixed(2) }));
  const ground = floors.find((f) => f.floor === 0)?.base_z ?? Math.min(...map.points.map((p) => p.z)) - CAM_HEIGHT_M;
  const here = points.filter((p) => p.floor === 0);
  const cx = here.reduce((s, p) => s + p.x, 0) / (here.length || 1);
  const cy = here.reduce((s, p) => s + p.y, 0) / (here.length || 1);
  return {
    points,
    floors,
    walls: (map.walls ?? []).map((w) => w.slice(0, 4)),
    ground_z: ground,
    wall_height: WALL_HEIGHT_M,
    center: { x: +cx.toFixed(2), y: +cy.toFixed(2), z: +(ground + 1).toFixed(2) },
    focus: focus ? { id: focus.id, name: scanName(focus), floor: focus.floor ?? 0, otherFloor: focus.floor !== 0 } : null,
  };
}
