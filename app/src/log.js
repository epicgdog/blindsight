// One log format for the Expo console: "12:03:04.512 [voice] play /api/agent/clip/ab12 {queue:0}".
const pad = (n, w = 2) => String(n).padStart(w, "0");

export function stamp(d = new Date()) {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

export function format(tag, msg, extra) {
  let tail = "";
  if (extra !== undefined) {
    try { tail = " " + JSON.stringify(extra); } catch { tail = " [unserializable]"; }
  }
  return `${stamp()} [${tag}] ${msg}${tail}`;
}

export function makeLog(tag, sink = (line) => console.log(line)) {
  return (msg, extra) => sink(format(tag, msg, extra));
}

// Server paths are long; keep the part that identifies the clip or phrase.
export function shortUri(uri) {
  if (!uri) return "";
  const m = /\/api\/(agent\/clip\/[^?]+|voice\?text=.*)$/.exec(uri);
  if (!m) return uri;
  return m[1].startsWith("voice") ? `tts "${decodeURIComponent(m[1].slice(11)).slice(0, 60)}"` : m[1];
}
