// Camera pitch and roll from the accelerometer's gravity reading (iOS CoreMotion axes, units of g:
// x right, y toward the top edge, z out of the screen; upright portrait reads y = -1).
// The back camera looks along -z, so sin(pitch) = gravity.z. pitch: + looks up, - looks down.
// roll: rotation of the image about the camera axis, 0 when the phone's top edge points up.

const finite = (v) => Number.isFinite(v);
const deg = (r) => (r * 180) / Math.PI;
const round1 = (v) => Math.round(v * 10) / 10;

export function attitudeFromGravity(g) {
  if (!g || !finite(g.x) || !finite(g.y) || !finite(g.z)) return null;
  const n = Math.hypot(g.x, g.y, g.z);
  if (n < 0.3) return null; // free fall or no reading
  const x = g.x / n, y = g.y / n, z = g.z / n;
  const pitch = deg(Math.asin(Math.max(-1, Math.min(1, z))));
  const roll = deg(Math.atan2(-x, -y));
  return { pitch_deg: round1(pitch) + 0, roll_deg: round1(roll) + 0 };
}

// Low-pass filter so hand shake and walking do not jitter the attitude.
export function smoothGravity(prev, next, alpha = 0.2) {
  if (!prev) return { x: next.x, y: next.y, z: next.z };
  const mix = (a, b) => a + (b - a) * alpha;
  return { x: mix(prev.x, next.x), y: mix(prev.y, next.y), z: mix(prev.z, next.z) };
}

// Walking detector from the raw accelerometer (units of g, gravity included). Walking bounces
// the magnitude by roughly 0.1-0.3 g every step; standing or turning on the spot stays within a
// few hundredths. Reports moving when the magnitude's spread over the last windowMs passes
// moveG, and still again only once it falls below stillG (hysteresis). null until the
// window has enough samples.
export function createMotionDetector({ windowMs = 1500, moveG = 0.06, stillG = 0.035, minSamples = 8 } = {}) {
  let samples = []; // { t, m }
  let moving = null;
  return {
    push(g, t) {
      if (!g || !finite(g.x) || !finite(g.y) || !finite(g.z) || !finite(t)) return moving;
      samples.push({ t, m: Math.hypot(g.x, g.y, g.z) });
      samples = samples.filter((s) => t - s.t <= windowMs);
      if (samples.length < minSamples) return moving;
      const mean = samples.reduce((a, s) => a + s.m, 0) / samples.length;
      const sd = Math.sqrt(samples.reduce((a, s) => a + (s.m - mean) ** 2, 0) / samples.length);
      if (moving !== true && sd > moveG) moving = true;
      else if (moving !== false && sd < stillG) moving = false;
      return moving;
    },
    get moving() { return moving; },
  };
}
