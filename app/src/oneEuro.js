// One Euro filter (Casiez et al. 2012): little smoothing when moving fast, strong smoothing when
// slow, so the dot neither jitters while standing nor lags while walking. Times in seconds.
const alpha = (cutoffHz, dt) => { const tau = 1 / (2 * Math.PI * cutoffHz); return 1 / (1 + tau / dt); };

export function createOneEuro({ minCutoff = 0.6, beta = 0.8, dCutoff = 1.0 } = {}) {
  let x = null, dx = 0, t = null;
  return {
    filter(value, now) {
      if (x == null || t == null || now <= t) { x = value; t = now; dx = 0; return x; }
      const dt = now - t;
      t = now;
      dx += alpha(dCutoff, dt) * ((value - x) / dt - dx);
      x += alpha(minCutoff + beta * Math.abs(dx), dt) * (value - x);
      return x;
    },
    reset() { x = null; t = null; dx = 0; },
  };
}

// Two filters for a map point {x, y} in metres.
export function createPointFilter(opts) {
  const fx = createOneEuro(opts), fy = createOneEuro(opts);
  return {
    filter(p, now) { return { ...p, x: fx.filter(p.x, now), y: fy.filter(p.y, now) }; },
    reset() { fx.reset(); fy.reset(); },
  };
}

// Heading in degrees: filtered as a unit vector so 359 -> 1 does not swing through 180.
export function createAngleFilter(opts) {
  const fc = createOneEuro(opts), fs = createOneEuro(opts);
  return {
    filter(deg, now) {
      const r = (deg * Math.PI) / 180;
      const c = fc.filter(Math.cos(r), now), s = fs.filter(Math.sin(r), now);
      return ((Math.atan2(s, c) * 180) / Math.PI + 360) % 360;
    },
    reset() { fc.reset(); fs.reset(); },
  };
}
