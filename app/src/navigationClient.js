// Latest-wins tracking, strict expiry, and speech priority. No movement offline.
export function createNavigationClient({ base, fetchImpl = fetch, speak, stop, now = Date.now }) {
  let session = null, busy = false, active = false, epoch = 0, lastPose = 0;
  let speakingUntil = 0, priority = 99, lastText = "", lastSpoken = 0;
  async function post(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1000);
    try {
      const r = await fetchImpl(`${base}/api/spatial/${path}`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
      if (!r.ok) throw new Error(`Spatial server returned ${r.status}`);
      return await r.json();
    } finally { clearTimeout(timer); }
  }
  function cue(result, sentAt) {
    const c = result?.cue;
    if (!c || now() - sentAt > c.expires_in_ms) return;
    if (c.text === lastText && now() - lastSpoken < 5000) return;
    if (now() < speakingUntil && c.priority >= priority) return;
    stop(); priority = c.priority; lastText = c.text; lastSpoken = now();
    speakingUntil = now() + Math.max(2000, c.text.length * 65);
    speak(c.text);
  }
  function emergency(text) {
    stop(); priority = 0; speakingUntil = now() + 3000;
    lastText = text; lastSpoken = now();
    // Native module speaks emergency warnings directly.
  }
  return {
    async start() { const generation = ++epoch; const r = await post("sessions", {});
      if (generation !== epoch) return; session = r.session_id; active = true; lastPose = now(); },
    stop() { active = false; session = null; epoch++; stop(); },
    emergency,
    watchdog() { if (active && now() - lastPose > 600) { stop(); return false; } return true; },
    async tracking(packet) {
      if (!active || busy) return;
      lastPose = now(); busy = true; const generation = epoch, sentAt = now();
      try { const r = await post(`sessions/${session}/tracking`, packet);
        if (active && generation === epoch) cue(r, sentAt);
        return r;
      } catch { stop(); return { state: "STOP", error: "Spatial connection unavailable" }; }
      finally { busy = false; }
    },
    async command(text) {
      if (!active) return;
      const generation = epoch, sentAt = now();
      const r = await post(`sessions/${session}/command`, { text });
      if (active && generation === epoch) cue(r, sentAt);
      return r;
    },
  };
}
