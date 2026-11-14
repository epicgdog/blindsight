import { stateClass, pushHistory, sparklinePoints, boxStyle, appendCue, fpsFromTimes, refStrip, clock } from "./logic.js";

const $ = (id) => document.getElementById(id);
let history = [], cueLog = [], times = [], faces = [];

function render(r) {
  if (r.jpeg_b64) $("live-frame").src = "data:image/jpeg;base64," + r.jpeg_b64;
  const boxes = $("boxes"); boxes.replaceChildren();
  for (const d of r.detections ?? []) {
    const el = document.createElement("div"); el.className = "box";
    Object.assign(el.style, boxStyle(d.bbox));
    const label = document.createElement("span"); label.textContent = `${d.cls} ${Math.round(d.conf * 100)}%`;
    el.append(label); boxes.append(el);
  }
  const loc = r.location;
  if (loc) {
    const badge = $("state-badge"); badge.textContent = loc.state; badge.className = stateClass(loc.state);
    $("score").textContent = loc.score.toFixed(2);
    $("inliers").textContent = loc.inliers;
    $("yaw").textContent = loc.yaw_deg == null ? "unknown" : `${Math.round(loc.yaw_deg)}°`;
    const diagnostic = r.localization_debug;
    let reason = $("localization-reason");
    if (!reason) { reason = document.createElement("p"); reason.id = "localization-reason"; $("state-badge").parentElement.append(reason); }
    reason.textContent = diagnostic?.reason ? `Matching: ${diagnostic.reason.replaceAll("_", " ")} · inliers ${diagnostic.best_inliers ?? 0} · competing scan ${diagnostic.competitor_inliers ?? 0}${diagnostic.distinctive_evidence?.required ? ` · distinctive ${diagnostic.distinctive_evidence.count} across ${diagnostic.distinctive_evidence.grid} cells` : ""}` : "";
    history = pushHistory(history, loc.score);
    $("spark-line").setAttribute("points", sparklinePoints(history, 200, 40));
    renderRefs(loc.best_ref);
  }
  const now = Date.now();
  cueLog = appendCue(cueLog, r.cue, now);
  $("cue-log").replaceChildren(...cueLog.map((c) => {
    const li = document.createElement("li"), t = document.createElement("time");
    t.textContent = clock(c.ts); li.append(t, c.text); return li;
  }));
  $("cue-log").scrollTop = $("cue-log").scrollHeight;
  times = [...times, now].slice(-30);
  $("fps").textContent = `${fpsFromTimes(times).toFixed(1)} fps`;
  if (r.server_ms) $("latency").textContent = `server ${r.server_ms.total.toFixed(0)} ms`;
}

function renderRefs(best) {
  const strip = $("ref-strip"); strip.replaceChildren();
  for (const r of refStrip(faces, best)) {
    const img = document.createElement("img"); img.src = r.url; img.alt = `reference ${r.index}`;
    if (r.highlight) img.className = "best";
    img.onerror = () => { img.style.visibility = "hidden"; };
    strip.append(img);
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/viewer${location.search}`);
  ws.onopen = () => { $("conn").textContent = "connected"; };
  ws.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  ws.onclose = () => { $("conn").textContent = "reconnecting…"; setTimeout(connect, 1000); };
}

async function post(path) {
  const r = await fetch(path, { method: "POST" });
  return r.json();
}

$("btn-pos").onclick = () => post("/api/capture/pos");
$("btn-neg").onclick = () => post("/api/capture/neg");
$("btn-warmup").onclick = async () => {
  const { seconds } = await post("/api/capture/auto?seconds=20");
  $("warmup-progress").max = seconds;
  const poll = setInterval(async () => {
    const s = await (await fetch("/api/capture/auto")).json();
    $("warmup-progress").value = s.elapsed;
    $("warmup-text").textContent = `Warm-up ${s.elapsed.toFixed(0)}/${s.seconds} s, kept ${s.kept} frames`;
    if (!s.running) { clearInterval(poll); $("warmup-text").textContent += " — done"; }
  }, 500);
};

setInterval(async () => {
  try {
    const m = await (await fetch("/metrics")).json();
    $("latency").textContent = `p50 ${m.p50_ms.toFixed(0)} / p95 ${m.p95_ms.toFixed(0)} ms`;
  } catch {}
}, 2000);

fetch("/api/pano").then((r) => r.json()).then((p) => { faces = p.faces; }).catch(() => {});
connect();
