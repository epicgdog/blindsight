// Sequential capture loop: never starts a capture before the previous send returns.
const SLOW_MS = 250;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Frame rate follows the phone's motion: walking or turning sends frames as fast as the server
// answers; held still sends one about every STILL_MS (enough for the place fix, far fewer
// redundant frames). Unknown motion counts as moving.
export const STILL_MS = 650;
export const TURN_FAST_DEG_S = 20;

export function headingRate(prev, heading, t) {
  if (!prev || prev.heading == null || heading == null || t <= prev.t) return 0;
  const d = Math.abs(((heading - prev.heading + 540) % 360) - 180);
  return (1000 * d) / (t - prev.t);
}

export function frameIntervalMs({ moving, turnDegPerS = 0 }) {
  if (turnDegPerS >= TURN_FAST_DEG_S) return 0;
  return moving === false ? STILL_MS : 0;
}

export async function runCaptureLoop({
  isRunning, capture, send, getHeading, now = Date.now, onTiming = () => {}, retryDelayMs = 200,
  onFrame = () => {}, getAttitude = () => null, onRate = () => {}, getSteps = () => null,
}) {
  let seq = 0;
  let lastHeading = null;
  let lastInterval = null;
  while (isRunning()) {
    try {
      const t0 = now();
      const shot = await capture();
      const captureMs = now() - t0;
      onTiming({ captureMs, slow: captureMs > SLOW_MS });
      seq += 1;
      onFrame({ seq, captureStartMs: t0 });
      const header = { seq, ts_ms: now(), heading_deg: getHeading() ?? null, w: shot.w, h: shot.h };
      const att = getAttitude();
      if (att) Object.assign(header, { pitch_deg: att.pitch_deg, roll_deg: att.roll_deg });
      if (att && typeof att.moving === "boolean") header.moving = att.moving;
      const steps = getSteps();
      if (Number.isInteger(steps) && steps >= 0) header.steps = steps;
      await send(shot.bytes, header);
      const t1 = now();
      const turn = headingRate(lastHeading, header.heading_deg, t1);
      lastHeading = { heading: header.heading_deg, t: t1 };
      const interval = frameIntervalMs({ moving: att?.moving, turnDegPerS: turn });
      if (interval !== lastInterval) { lastInterval = interval; onRate({ interval_ms: interval, moving: att?.moving ?? null, turn_deg_s: Math.round(turn) }); }
      // yield to the event loop either way so socket events are processed
      await sleep(Math.max(0, interval - (t1 - t0)));
    } catch (e) {
      await sleep(retryDelayMs);
    }
  }
}
