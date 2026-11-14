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
