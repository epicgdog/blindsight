// Tell the server when speech actually played, so it can measure capture-to-audible latency.

// wss://host/ws/phone -> https://host/api/speech
export function speechAckUrl(wsUrl) {
  if (!wsUrl) return null;
  const m = /^(wss?):\/\/([^/]+)/i.exec(wsUrl.trim());
  if (!m) return null;
  return `${m[1].toLowerCase() === "wss" ? "https" : "http"}://${m[2]}/api/speech`;
}

export function ackBody(cueId, captureToAudibleMs) {
  const body = { played: true };
  if (cueId) {
    body.cue_id = cueId;
    if (Number.isFinite(captureToAudibleMs)) body.capture_to_audible_ms = Math.max(0, Math.round(captureToAudibleMs));
  }
  return body;
}

// Never throws: an ack must not disturb the audio path.
export async function sendAck(fetchImpl, url, body) {
  if (!url) return false;
  try {
    const res = await fetchImpl(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return !!res?.ok;
  } catch {
    return false;
  }
}

// Remembers when each frame's capture started and which frame produced each cue.
export function createAckTracker(max = 200) {
  const frames = new Map(); // seq -> capture start (ms)
  const cues = new Map(); // cue id -> seq
  const trim = (m) => { while (m.size > max) m.delete(m.keys().next().value); };
  return {
    noteFrame(seq, startMs) { frames.set(seq, startMs); trim(frames); },
    noteCue(cueId, seq) { cues.set(cueId, seq); trim(cues); },
    captureToAudibleMs(cueId, nowMs) {
      if (!cues.has(cueId)) return null;
      const start = frames.get(cues.get(cueId));
      cues.delete(cueId);
      return start === undefined ? null : nowMs - start;
    },
    size: () => frames.size,
  };
}
