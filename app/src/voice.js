import { shortUri } from "./log.js";

// All speech comes from the server as WAV: the Live agent's clips (/api/agent/clip/<id>) and a few
// fixed Gemini TTS phrases (/api/voice). The phone only plays audio.

export function serverBase(wsUrl) {
  if (!wsUrl) return null;
  const m = /^(wss?):\/\/([^/]+)/i.exec(wsUrl.trim());
  if (!m) return null;
  return `${m[1].toLowerCase() === "wss" ? "https" : "http"}://${m[2]}`;
}

export function voiceUrl(wsUrl, text) {
  if (!wsUrl || !text) return null;
  const m = /^(wss?):\/\/([^/]+)/i.exec(wsUrl.trim());
  if (!m) return null;
  const scheme = m[1].toLowerCase() === "wss" ? "https" : "http";
  return `${scheme}://${m[2]}/api/voice?text=${encodeURIComponent(text.slice(0, 300))}`;
}

// Two players, double buffered: while one plays, the next queued clip is already loading in the
// other, so back-to-back agent segments play without the fetch/decode gap that made the voice choppy.
// speak() replaces whatever is playing; onStart fires when audio is actually audible, onSettled
// however it ends (finished, stopped, failed, replaced).
// Every step is logged: queued, load, preload, loaded, playing, finished, cut, stalled, failed.
export const STALL_MS = 6000; // a clip that has not started by then is skipped

// Correlate HTTP answers with audio actually received over the socket, not a UI flag.
export function createVoiceDelivery(playPath, announce) {
  const streamed = new Set(), replayed = new Set();
  const remember = (set, id) => { if (!id) return; set.add(id); if (set.size > 100) set.delete(set.values().next().value); };
  return {
    stream(message) {
      if (!message.url || replayed.has(message.turn)) return;
      remember(streamed, message.turn);
      playPath(message.url, { interrupt: message.interrupt });
    },
    reply(result) {
      if (!result.url) { announce(result.reply); return; }
      if (result.turn && (streamed.has(result.turn) || replayed.has(result.turn))) return;
      remember(replayed, result.turn);
      playPath(result.url, { interrupt: true });
    },
  };
}

export function createVoicePlayer({ createPlayer, getWsUrl, log = () => {}, now = () => Date.now(),
  setTimer = setTimeout, clearTimer = clearTimeout }) {
  const players = [createPlayer(), createPlayer()];
  let current = null; // { uri, slot, onStart, onSettled, started, loaded, t0, timer }
  let pre = null; // { slot, entry, loaded } the next clip, loading in the idle player
  let speaking = false;
  const queue = []; // agent clips waiting for the current one to finish: { uri, opts }
  let playEntry = null;

  const idleSlot = () => (current ? 1 - current.slot : 0);
  const finish = (how) => {
    const c = current;
    if (!c) return;
    clearTimer(c.timer);
    current = null;
    speaking = false;
    log(how, { clip: shortUri(c.uri), ms: now() - c.t0, queue: queue.length });
    c.onSettled?.();
  };
  const next = () => {
    const n = queue.shift();
    if (n) playEntry(n);
  };
  const dropPreload = () => {
    if (!pre) return;
    try { players[pre.slot].pause(); } catch { /* idle */ }
    pre = null;
  };
  const preloadNext = () => {
    if (pre || !current || !queue.length) return;
    const slot = idleSlot();
    const entry = queue[0];
    pre = { slot, entry, loaded: false, t0: now() };
    try {
      players[slot].pause();
      players[slot].replace({ uri: entry.uri });
      log("preload", { clip: shortUri(entry.uri) });
    } catch (e) {
      log("preload failed", { clip: shortUri(entry.uri), error: e?.message });
      pre = null;
    }
  };

  players.forEach((player, slot) => player.addListener?.("playbackStatusUpdate", (s) => {
    if (pre && pre.slot === slot && !(current && current.slot === slot)) {
      if (s.isLoaded && !pre.loaded) { pre.loaded = true; log("preloaded", { clip: shortUri(pre.entry.uri), ms: now() - pre.t0 }); }
      return;
    }
    if (!current || current.slot !== slot) return;
    if (s.isLoaded && !current.loaded) {
      current.loaded = true;
      log("loaded", { clip: shortUri(current.uri), ms: now() - current.t0, duration_s: s.duration ?? null });
    }
    if (s.playing && !current.started) {
      current.started = true;
      log("playing", { clip: shortUri(current.uri), startup_ms: now() - current.t0 });
      current.onStart?.();
    }
    if (s.error) { log("player error", { clip: shortUri(current.uri), error: String(s.error) }); finish("failed"); next(); }
    if (s.didJustFinish) { finish("finished"); next(); }
  }));

  playEntry = (entry) => {
    const { uri, opts: { onStart, onSettled } = {} } = entry;
    if (current) {
      const old = current;
      finish("cut");
      try { players[old.slot].pause(); } catch { /* done */ }
    }
    let slot;
    let warm = false;
    if (pre && pre.entry === entry) {
      slot = pre.slot; warm = true; pre = null; // already loading/loaded in the idle player
    } else {
      dropPreload();
      slot = 0;
    }
    const c = { uri, slot, onStart, onSettled, started: false, loaded: warm, t0: now(), timer: null };
    current = c;
    speaking = true;
    c.timer = setTimer(() => {
      if (current === c && !c.started) { log("stalled, skipping", { clip: shortUri(uri) }); try { players[c.slot].pause(); } catch {} finish("stalled"); next(); }
    }, STALL_MS);
    log(warm ? "play preloaded" : "load", { clip: shortUri(uri), queue: queue.length });
    try {
      if (!warm) players[slot].replace({ uri });
      players[slot].play();
    } catch (e) {
      log("play failed", { clip: shortUri(uri), error: e?.message });
      finish("failed");
      next();
      return;
    }
    preloadNext();
  };

  const clearQueue = (why) => {
    if (queue.length) log(why, { dropped: queue.length });
    queue.length = 0;
    dropPreload();
  };

  return {
    speak(text, opts = {}) {
      const uri = voiceUrl(getWsUrl(), text);
      if (!uri) { log("no server url, not spoken", { text }); opts.onSettled?.(); return; }
      clearQueue("speak clears queue");
      playEntry({ uri, opts });
    },
    // An agent clip by server path. Urgent clips cut in; others wait their turn (max 40 queued).
    playPath(path, { interrupt = false, ...opts } = {}) {
      const base = serverBase(getWsUrl());
      if (!base || !path) { log("no server url, clip dropped", { path }); return; }
      const entry = { uri: base + path, opts };
      if (interrupt || !speaking) {
        if (interrupt) clearQueue("interrupt clears queue");
        playEntry(entry);
        return;
      }
      queue.push(entry);
      while (queue.length > 40) { if (pre && pre.entry === queue[0]) dropPreload(); queue.shift(); }
      log("queued", { clip: shortUri(entry.uri), queue: queue.length });
      preloadNext();
    },
    stop() {
      clearQueue("stop clears queue");
      if (current) { try { players[current.slot].pause(); } catch { /* nothing playing */ } }
      finish("stopped");
    },
    isSpeaking: () => speaking,
  };
}
