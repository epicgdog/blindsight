// Camera heading from gravity and the magnetic field, both in device axes (expo-sensors).
// Down D = gravity, East = D x m, North = E x D. The back camera looks along -z of the device,
// so heading = atan2(-E_z, -N_z), clockwise from magnetic north, 0..360. Null when either is
// missing or the camera points nearly straight up or down (heading undefined there).
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (v) => { const n = Math.hypot(...v); return n > 1e-9 ? v.map((x) => x / n) : null; };

export function cameraHeading(gravity, mag) {
  if (!gravity || !mag) return null;
  const D = norm([gravity.x, gravity.y, gravity.z]);
  if (!D || Math.abs(D[2]) > 0.97) return null;
  const E = norm(cross(D, [mag.x, mag.y, mag.z]));
  if (!E) return null;
  const N = cross(E, D);
  const deg = (Math.atan2(-E[2], -N[2]) * 180) / Math.PI;
  return (deg + 360) % 360;
}
