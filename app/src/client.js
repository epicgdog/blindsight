import { backoffMs } from "./reconnect.js";

export function createClient({
  url, WebSocketImpl = globalThis.WebSocket, speaker, onStatus = () => {}, onResult = () => {},
  setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
}) {
  let ws = null, attempt = 0, timer = null, wantRun = false, wasUp = false, announcedDown = false;
  let state = null;
  const status = (extra = {}) => onStatus({ connected: !!ws && ws.readyState === 1, state, ...extra });

  function connect() {
    ws = new WebSocketImpl(url);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      if (announcedDown) speaker.announce("Reconnected");
      announcedDown = false; wasUp = true; attempt = 0;
      status();
    };
    ws.onmessage = (ev) => {
      let r;
      try { r = JSON.parse(ev.data); } catch { return; }
      state = r.location?.state ?? state;
      speaker.handle(r.cue ?? null);
      onResult(r);
      status();
    };
    ws.onclose = () => {
      status({ connected: false });
      if (!wantRun) return;
      if (wasUp && !announcedDown) { speaker.announce("Connection lost"); announcedDown = true; }
      timer = setTimeoutImpl(connect, backoffMs(attempt++, { jitter: false }));
    };
    ws.onerror = () => {};
  }

  return {
    start() { wantRun = true; connect(); },
    stop() { wantRun = false; clearTimeoutImpl(timer); try { ws?.close(); } catch {} },
    isOpen: () => !!ws && ws.readyState === 1,
    async sendFrame(bytes) {
      if (!ws || ws.readyState !== 1) return false;
      ws.send(bytes);
      return true;
    },
  };
}
