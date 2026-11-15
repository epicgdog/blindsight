// Hold-to-talk: record a clip, POST it to the server's voice agent, speak the reply.
import { AUDIO_MODE } from "./audio.js";

// Recording needs the session in record mode; switch back to AUDIO_MODE after the clip.
export const RECORD_MODE = { ...AUDIO_MODE, allowsRecording: true };

export function agentQuietUrl(wsUrl, on) {
  const talk = agentTalkUrl(wsUrl);
  return talk ? talk.replace(/\/talk$/, `/quiet?on=${on ? "true" : "false"}`) : null;
}

export function agentTalkUrl(wsUrl) {
  if (!wsUrl) return null;
  const m = /^(wss?):\/\/([^/]+)/i.exec(wsUrl.trim());
  if (!m) return null;
  return `${m[1].toLowerCase() === "wss" ? "https" : "http"}://${m[2]}/api/agent/talk`;
}

// Clips shorter than this are an accidental tap, not speech.
export const MIN_CLIP_MS = 400;

// The clip goes up as the raw m4a body. Expo's fetch (SDK 58) rejects React Native's old
// FormData file parts ({uri, name, type}) with "Unsupported FormDataPart implementation", so the
// caller reads the file's bytes (expo-file-system) and passes readBytes(uri) -> Uint8Array.
export async function postClip(fetchFn, url, uri, { readBytes, timeoutMs = 65000 } = {}) {
  let bytes;
  try {
    bytes = await readBytes(uri);
  } catch (e) {
    return { reply: "Sorry, I couldn't read the recording.", error: `read: ${e?.name ?? "error"}: ${e?.message ?? ""}` };
  }
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const r = await fetchFn(url, { method: "POST", headers: { "Content-Type": "audio/m4a" }, body: bytes, signal: ctrl?.signal });
    if (!r.ok) {
      const reply = r.status === 409 ? "Please wait for the current answer."
        : r.status === 503 ? "The voice assistant is not ready. Check the server."
        : "Sorry, the voice assistant had a problem.";
      return { reply, status: r.status, error: `http ${r.status}` };
    }
    const body = await r.json();
    const tools = (body.tools ?? []).map((x) => x.name);
    if (!body.url) {
      return { reply: "Sorry, the voice assistant had a problem.", heard: body.heard ?? "", tools,
        error: body.error ?? "no audio", detail: body.detail };
    }
    return { url: body.url, reply: body.reply ?? "", heard: body.heard ?? "", tools, server_ms: body.ms, bytes: bytes.length };
  } catch (e) {
    return {
      reply: e?.name === "AbortError" ? "The voice assistant took too long. Please try again."
        : "I can't reach the voice assistant. Check the server connection.",
      error: `${e?.name ?? "error"}: ${e?.message ?? ""}`,
    };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// Serializes preparation, release, upload and playback even when press-out arrives early.
export function createHoldToTalk({ prepare, record, finish, restore, send, announce, state,
  busy, onResult, now = Date.now }) {
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
        if (onResult) onResult(result); else announce(result.reply);
      } catch { await restore().catch(() => {}); announce("The recording could not be sent. Please try again."); }
      finally { await restore().catch(() => {}); phase = "idle"; busy(false); state("idle"); }
    },
  };
}
