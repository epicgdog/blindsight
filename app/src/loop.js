// Sequential capture loop: never starts a capture before the previous send returns.
const SLOW_MS = 250;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function runCaptureLoop({
  isRunning, capture, send, getHeading, now = Date.now, onTiming = () => {}, retryDelayMs = 200,
}) {
  let seq = 0;
  while (isRunning()) {
    try {
      const t0 = now();
      const shot = await capture();
      const captureMs = now() - t0;
      onTiming({ captureMs, slow: captureMs > SLOW_MS });
      seq += 1;
      const header = { seq, ts_ms: now(), heading_deg: getHeading() ?? null, w: shot.w, h: shot.h };
      await send(shot.bytes, header);
      await sleep(0); // yield to the event loop so socket events are processed
    } catch (e) {
      await sleep(retryDelayMs);
    }
  }
}
