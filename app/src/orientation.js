// Upright-portrait detection from the accelerometer (readings in g).
// iOS (CoreMotion): a phone held upright in portrait reads y = -1.

const finite = (v) => Number.isFinite(v);

// Angle in degrees between the measured gravity and the upright-portrait direction (0, -1, 0).
// 0 = perfectly upright, 90 = landscape or lying flat, 180 = upside down.
export function tiltDegrees(g) {
  if (!g || !finite(g.x) || !finite(g.y) || !finite(g.z)) return NaN;
  const mag = Math.hypot(g.x, g.y, g.z);
  if (mag < 0.3) return NaN; // free fall or no reading
  const cos = Math.max(-1, Math.min(1, -g.y / mag));
  return (Math.acos(cos) * 180) / Math.PI;
}

export function isUprightPortrait(g, { maxTiltDeg = 30 } = {}) {
  const tilt = tiltDegrees(g);
  return finite(tilt) && tilt <= maxTiltDeg;
}

// Exponential smoothing so hand jitter does not flicker the gate.
export function smoothGravity(prev, next, alpha = 0.3) {
  if (!prev) return { x: next.x, y: next.y, z: next.z };
  const mix = (a, b) => a + (b - a) * alpha;
  return { x: mix(prev.x, next.x), y: mix(prev.y, next.y), z: mix(prev.z, next.z) };
}
