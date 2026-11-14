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

export async function postClip(fetchFn, url, uri, { FormDataCtor = FormData, timeoutMs = 20000 } = {}) {
  const form = new FormDataCtor();
  form.append("audio", { uri, name: "clip.m4a", type: "audio/m4a" });
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const r = await fetchFn(url, { method: "POST", body: form, signal: ctrl?.signal });
    if (!r.ok) return { reply: "Sorry, the voice assistant had a problem.", status: r.status };
    const body = await r.json();
    return { reply: body.reply || "Sorry, I didn't catch that.", heard: body.heard ?? "" };
  } catch (e) {
    return { reply: "I can't reach the voice assistant.", error: e?.name ?? "error" };
  } finally {
    if (timer) clearTimeout(timer);
  }
}
