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

// ---- embedding picks and why a frame did not match ----
// The match evidence of every frame: r.match is null on unmatched frames, the debug copy is not.
export const frameMatch = (r) => r?.match ?? r?.localization_debug?.match ?? null;

export const refUrl = (p) =>
  `/api/ref/${p.ref}.jpg?place=${encodeURIComponent(p.place)}&name=${encodeURIComponent(p.name)}`;

export const pickRows = (match, info = {}) =>
  (match?.picks ?? []).map((p, i) => ({
    rank: i + 1,
    url: refUrl(p),
    place: p.place,
    name: placeName(p.place, info),
    view: p.name,
    sim: p.sim,
    inliers: p.inliers,
    verdict: VERDICT[p.verdict] ?? null,
    chosen: match.best_ref === p.ref,
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
  const c = [...(d.candidates ?? [])].sort((a, b) => (b[2] ?? 0) - (a[2] ?? 0)).slice(0, 4);
  if (d.screened === false && (d.candidates ?? []).some((x) => x[4] && x[4] !== "kept"))
    out.push("No plausible candidate passed; judged unfiltered (lets the tracker restart on a repeated far match)");
  if (c.length) out.push("Verified: " + c.map(([name, place, inl, grid, v]) => `${String(place).slice(0, 6)}/${name} ${inl} in, ${grid} cells${v && v !== "kept" ? ` [${VERDICT[v] ?? v}]` : ""}`).join(" · "));
  if (d.total != null) out.push(`Localizer ${d.total} ms (embed ${d.embed}, extract ${d.extract}, verify ${d.verify})`);
  return out;
}

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
export function mapMarkers(map, activeId, ranking, trail, w, h, position = null) {
  if (!map?.points?.length) return { floor: null, name: "", markers: [] };
  if (map.walls?.length) return wallMarkers(map, activeId, ranking, trail, position);
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
  if (hasXY(position)) {
    const you = mapProject([...here, { id: "__you", x: position.x, y: position.y }], map.bounds, w, h, 24).__you;
    markers.push({ id: "you", kind: "you", x: you.x, y: you.y, rank: 0 });
  }
  const active = map.points.find((p) => p.id === activeId);
  const project = (x, y) => {
    const q = mapProject([{ id: "__p", x, y }], map.bounds, w, h, 24).__p;
    return [q.x, q.y];
  };
  return { floor, name: active ? scanName(active) : "", markers, project };
}

// ---- facing arrow ----
// heading_deg is the fused map-frame heading (server/heading.py): clockwise from +y (north, up on
// the map). Both maps flip y, so on the SVG the direction is (sin h, -cos h).
export function headingArrow(x, y, headingDeg, len) {
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(headingDeg)) return null;
  const h = (headingDeg * Math.PI) / 180;
  const dx = Math.sin(h), dy = -Math.cos(h);
  const tip = [x + dx * len, y + dy * len];
  const wing = len * 0.35, back = len * 0.6;
  const l = [x + dx * back - dy * wing, y + dy * back + dx * wing];
  const r = [x + dx * back + dy * wing, y + dy * back - dx * wing];
  const f = (p) => `${+p[0].toFixed(2)},${+p[1].toFixed(2)}`;
  return { line: [x, y, ...tip].map((v) => +v.toFixed(2)), head: `${f(tip)} ${f(l)} ${f(r)}` };
}

export const headingText = (h) =>
  !h ? "" : Number.isFinite(h.heading_deg)
    ? `facing ${Math.round(h.heading_deg)}° ±${h.sigma_deg ?? "?"}° (${h.source ?? "?"})`
    : Number.isFinite(h.compass_deg) ? `facing unknown (compass ${Math.round(h.compass_deg)}°, not calibrated to the map yet)` : "facing unknown";

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

const hasXY = (p) => Number.isFinite(p?.x) && Number.isFinite(p?.y);

function wallMarkers(map, activeId, ranking, trail, position = null) {
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
  if (hasXY(position)) markers.push({ id: "you", kind: "you", x: px(position.x), y: py(position.y), rank: 0 });
  const walls =map.walls.map(([x1, y1, x2, y2]) => [px(x1), py(y1), px(x2), py(y2)]);
  return {
    floor,
    name: focus ? scanName(focus) : "",
    markers,
    walls,
    viewBox: { x: 0, y: 0, w: +((maxX - minX) * WALL_PX_PER_M).toFixed(2), h: +((maxY - minY) * WALL_PX_PER_M).toFixed(2) },
    otherFloor,
    project: (x, y) => [px(x), py(y)],
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

// The route (map metres) as SVG points, and the straight line from its start to the entrance.
export function pathLines(path, project) {
  if (!project || !path?.pts?.length) return null;
  const f = ([x, y]) => project(x, y).join(",");
  return {
    route: path.pts.map(f).join(" "),
    straight: path.goal ? [path.pts[0], path.goal].map(f).join(" ") : null,
  };
}

export function scene3d(map, activeId, ranking, trail, position = null, headingDeg = null, path = null) {
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
  // The tracked camera position (mesh frame, same as the scan points), shown as its own dot.
  if (hasXY(position) && Number.isFinite(position.z)) {
    points.push({ id: "you", kind: "you", x: position.x, y: position.y, z: position.z, floor: 0, rank: 0, label: "You" });
  }
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
    heading_deg: Number.isFinite(headingDeg) ? headingDeg : null,
    path: path?.pts?.length ? path.pts : null,
    goal: path?.goal ?? null,
  };
}
