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
