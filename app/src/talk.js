// Hold-to-talk: record a clip, POST it to the server's voice agent, speak the reply.
import { AUDIO_MODE } from "./audio.js";

// Recording needs the session in record mode; switch back to AUDIO_MODE after the clip.
export const RECORD_MODE = { ...AUDIO_MODE, allowsRecording: true };

export function agentTalkUrl(wsUrl) {
  if (!wsUrl) return null;
  const m = /^(wss?):\/\/([^/]+)/i.exec(wsUrl.trim());
  if (!m) return null;
  return `${m[1].toLowerCase() === "wss" ? "https" : "http"}://${m[2]}/api/agent/talk`;
}

// Clips shorter than this are an accidental tap, not speech.
export const MIN_CLIP_MS = 400;

export async function postClip(fetchFn, url, uri, { FormDataCtor = FormData, timeoutMs = 65000 } = {}) {
  const form = new FormDataCtor();
  form.append("audio", { uri, name: "clip.m4a", type: "audio/m4a" });
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const r = await fetchFn(url, { method: "POST", body: form, signal: ctrl?.signal });
    if (!r.ok) return { reply: r.status === 409 ? "Please wait for the current answer." : r.status === 503 ? "The voice assistant is not ready. Check the server." : "Sorry, the voice assistant had a problem.", status: r.status };
    const body = await r.json();
    return { reply: body.reply || "Sorry, I didn't catch that.", heard: body.heard ?? "" };
  } catch (e) {
    return { reply: e?.name === "AbortError" ? "The voice assistant took too long. Please try again." : "I can't reach the voice assistant. Check the server connection.", error: e?.name ?? "error" };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// Serializes preparation, release, upload and playback even when press-out arrives early.
export function createHoldToTalk({ prepare, record, finish, restore, send, announce, state,
  busy, now = Date.now }) {
  let phase = "idle", preparation = null, started = 0;
  return {
    begin() {
      if (phase !== "idle") return;
      phase = "preparing"; busy(true); state("preparing");
      preparation = (async () => {
        try { await prepare(); await record(); started = now(); phase = "recording"; state("listening"); }
        catch { phase = "failed"; }
      })();
    },
    async end() {
      if (phase === "idle" || phase === "thinking" || phase === "stopping") return;
      await preparation;
      if (phase === "stopping" || phase === "thinking" || phase === "idle") return;
      const failed = phase === "failed";
      phase = "stopping";
      try {
        if (failed) { await restore(); announce("Microphone not available."); return; }
        const held = now() - started;
        const uri = await finish(); await restore();
        if (!uri || held < MIN_CLIP_MS) return;
        phase = "thinking"; state("thinking");
        const result = await send(uri);
        announce(result.reply);
      } catch { await restore().catch(() => {}); announce("The recording could not be sent. Please try again."); }
      finally { await restore().catch(() => {}); phase = "idle"; busy(false); state("idle"); }
    },
  };
}
