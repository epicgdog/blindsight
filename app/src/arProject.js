// AR path on the camera image, without ARKit: the route (map metres) is drawn on the floor from
// where the server says you stand and face, the phone's tilt (accelerometer) and an assumed
// camera height. Only the stretch to the next corner (plus a short stub showing the turn) is
// drawn, so heading errors stay small and the path does not run through the wall past a corner.
// Pure, tested in node.

export const AR = {
  heightM: 1.4, // camera above the floor
  vfovDeg: 67, // vertical field of view of the preview in portrait (iPhone main camera, 4:3)
  aheadM: 12, // never draw further than this
  stubM: 2.5, // past the next corner
  turnDeg: 35, // a direction change above this is a corner
  halfWidthM: 0.35, // half the ribbon width
  stepM: 0.5, // ribbon sample spacing
  chevronM: 1.5, // chevron spacing
  minZ: 0.35, // closest depth drawn (metres in front of the camera)
};

const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;
export const wrap180 = (a) => ((((a + 180) % 360) + 360) % 360) - 180;

// Point p in the camera's frame: f metres forward (horizontal), r metres to the right.
// Map: x east, y north; heading clockwise from north.
export function toCamera(p, pos, headingDeg) {
  const h = rad(headingDeg), dx = p[0] - pos[0], dy = p[1] - pos[1];
  return { f: dx * Math.sin(h) + dy * Math.cos(h), r: dx * Math.cos(h) - dy * Math.sin(h) };
}

export const focalPx = (height, vfovDeg) => height / 2 / Math.tan(rad(vfovDeg) / 2);

// Screen pixel of a point f forward, r right, up metres above the floor; null when behind or
// too close. cam: { width, height, pitchDeg (+ looks up), rollDeg (+ phone turned clockwise),
// heightM, vfovDeg, minZ }.
export function project(f, r, up, cam) {
  const p = rad(cam.pitchDeg || 0), y = up - (cam.heightM ?? AR.heightM);
  const z = f * Math.cos(p) + y * Math.sin(p);
  if (!(z >= (cam.minZ ?? AR.minZ))) return null;
  const yUp = -f * Math.sin(p) + y * Math.cos(p);
  const F = focalPx(cam.height, cam.vfovDeg ?? AR.vfovDeg);
  const dx = (F * r) / z, dy = (-F * yUp) / z;
  const k = rad(cam.rollDeg || 0), c = Math.cos(k), s = Math.sin(k);
  return [cam.width / 2 + dx * c + dy * s, cam.height / 2 - dx * s + dy * c];
}

function nearestOnPath(pts, p) {
  let best = null;
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    const t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0;
    const q = [a[0] + dx * t, a[1] + dy * t], d = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (!best || d < best.d) best = { i, q, d };
  }
  return best;
}

// The route from your nearest point on it onward: up to aheadM, or to the first corner plus
// stubM. { pts, corner: { pt, turn (+ right) } | null, end (reaches the route's end), length }.
export function pathAhead(pts, pos, o = AR) {
  if (!pts || pts.length < 2 || !pos) return null;
  const n = nearestOnPath(pts, pos);
  const out = [n.q];
  let cur = n.q, dist = 0, corner = null, prev = null;
  for (let k = n.i + 1; k < pts.length; k++) {
    const nxt = pts[k], seg = Math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]);
    if (seg < 1e-6) continue;
    const bearing = deg(Math.atan2(nxt[0] - cur[0], nxt[1] - cur[1]));
    if (prev != null && !corner && Math.abs(wrap180(bearing - prev)) > o.turnDeg) {
      corner = { pt: cur, turn: wrap180(bearing - prev), at: dist };
    }
    const limit = corner ? Math.min(o.aheadM, corner.at + o.stubM) : o.aheadM;
    if (dist + seg >= limit) {
      const t = (limit - dist) / seg;
      out.push([cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t]);
      return { pts: out, corner, end: false, length: limit };
    }
    out.push(nxt); dist += seg; prev = bearing; cur = nxt;
  }
  return { pts: out, corner, end: true, length: dist };
}

// Points every step metres along a polyline, with the unit direction there.
function samples(pts, step) {
  const out = [];
  let s = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1], L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (L < 1e-6) continue;
    const d = [(b[0] - a[0]) / L, (b[1] - a[1]) / L];
    for (let t = out.length ? Math.ceil(s / step) * step - s : 0; t < L; t += step) {
      out.push({ c: [a[0] + d[0] * t, a[1] + d[1] * t], d, s: s + t });
    }
    s += L;
    if (i === pts.length - 2) out.push({ c: b, d, s });
  }
  return out;
}

// Everything to draw for one camera pose:
//   quads: ribbon pieces [{ pts: 4 screen points, alpha }], chevrons: [{ pts }],
//   pin: { base, top } at the destination when it is within reach, corner: turn ahead or null,
//   hint: "left" | "right" | "behind" when the path is out of view, else null,
//   rings: detected obstacles as floor circles [{ pts }] (obstacles: [{ x, y, r_m }] map metres).
export function arScene({ route, pos, headingDeg, cam, endIsDest = true, obstacles = [], o = AR }) {
  const empty = { quads: [], chevrons: [], pin: null, corner: null, hint: null, rings: [] };
  const ahead = pathAhead(route, pos, o);
  if (!ahead || !Number.isFinite(headingDeg)) return empty;
  const P = (p, up = 0) => { const c = toCamera(p, pos, headingDeg); return project(c.f, c.r, up, cam); };
  const S = samples(ahead.pts, o.stepM);
  const edge = (smp, side) => [smp.c[0] + smp.d[1] * side * o.halfWidthM, smp.c[1] - smp.d[0] * side * o.halfWidthM];
  const quads = [];
  for (let i = 0; i < S.length - 1; i++) {
    const q = [P(edge(S[i], -1)), P(edge(S[i + 1], -1)), P(edge(S[i + 1], 1)), P(edge(S[i], 1))];
    if (q.every(Boolean)) quads.push({ pts: q, alpha: 0.75 * (1 - (0.6 * S[i].s) / Math.max(ahead.length, 1)) });
  }
  const chevrons = [];
  for (let s = 1; s < ahead.length - 0.3; s += o.chevronM) {
    const smp = S.reduce((b, x) => (Math.abs(x.s - s) < Math.abs(b.s - s) ? x : b), S[0]);
    const { c, d } = smp, n = [d[1], -d[0]];
    const at = (fwd, side) => [c[0] + d[0] * fwd + n[0] * side, c[1] + d[1] * fwd + n[1] * side];
    const pts = [at(0.3, 0), at(-0.2, 0.26), at(-0.05, 0), at(-0.2, -0.26)].map((p) => P(p));
    if (pts.every(Boolean)) chevrons.push({ pts });
  }
  let pin = null;
  if (ahead.end && endIsDest) {
    const last = ahead.pts[ahead.pts.length - 1], base = P(last), top = P(last, 1.8);
    if (base && top) pin = { base, top };
  }
  // where the path goes, a few metres ahead: off screen -> an arrow at the edge
  const target = S.reduce((b, x) => (Math.abs(x.s - 3) < Math.abs(b.s - 3) ? x : b), S[0]).c;
  const t = toCamera(target, pos, headingDeg);
  const bearing = deg(Math.atan2(t.r, t.f));
  const halfH = deg(Math.atan(cam.width / 2 / focalPx(cam.height, cam.vfovDeg ?? o.vfovDeg)));
  const hint = Math.abs(bearing) > 120 ? "behind" : Math.abs(bearing) > halfH ? (bearing > 0 ? "right" : "left") : null;
  const rings = [];
  for (const ob of obstacles ?? []) {
    const r = ob.r_m ?? 0.4, ring = [];
    for (let k = 0; k < 16; k++) {
      const a = (k / 16) * 2 * Math.PI;
      ring.push(P([ob.x + r * Math.cos(a), ob.y + r * Math.sin(a)]));
    }
    if (ring.every(Boolean)) rings.push({ pts: ring, cls: ob.cls });
  }
  return { quads, chevrons, pin, corner: ahead.corner, hint, rings };
}
