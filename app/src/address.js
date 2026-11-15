// Turn whatever the user typed/scanned into the phone WebSocket URL.
export function normalizeServerUrl(input) {
  if (!input || !input.trim()) return null;
  let s = input.trim();
  if (!/^[a-z]+:\/\//i.test(s)) {
    const host = s.split("/")[0].split(":")[0].toLowerCase();
    const secure = host.endsWith(".ts.net") || host.endsWith(".trycloudflare.com");
    s = (secure ? "wss://" : "ws://") + s;
  }
  s = s.replace(/^https:\/\//i, "wss://").replace(/^http:\/\//i, "ws://");
  s = s.replace(/\/+$/, "");
  if (!s.endsWith("/ws/phone")) s += "/ws/phone";
  return s;
}
