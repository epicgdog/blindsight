// Cue speech policy: interrupt -> stop then speak; else speak only if idle; never queue.
export function createCueSpeaker({ speak, stop, isSpeaking }, { log = () => {}, maxSeen = 200 } = {}) {
  const seen = new Set();
  const remember = (id) => {
    seen.add(id);
    if (seen.size > maxSeen) seen.delete(seen.values().next().value);
  };
  return {
    handle(cue) {
      if (!cue || seen.has(cue.id)) return;
      if (cue.interrupt) {
        stop();
      } else if (isSpeaking()) {
        return; // stale by the time it is free: drop, do not queue (and do not mark seen)
      }
      remember(cue.id);
      speak(cue.text, cue);
    },
    announce(text) {
      stop();
      speak(text);
    },
    async startupCheck() {
      speak("Blindsight ready", { startup: true });
      log("speech played: Blindsight ready (volume check)");
    },
    seenCount: () => seen.size,
  };
}
