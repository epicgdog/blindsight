import { stateClass, pushHistory, sparklinePoints, boxStyle, appendCue, fpsFromTimes, refStrip, clock, rankRows, footprintPaths, mapMarkers, pushTrail, yawText, errorText } from "./logic.js";

const $ = (id) => document.getElementById(id);
let history = [], cueLog = [], times = [], faces = [], placeInfo = {}, mapPano = null, mapData = null, trail = [], lastResult = null;

function render(r) {
  lastResult = r;
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
    $("yaw").textContent = yawText(loc.yaw_deg);
    history = pushHistory(history, loc.score);
    $("spark-line").setAttribute("points", sparklinePoints(history, 200, 40));
    renderRefs(loc.best_ref);
  }
  renderMatch(r);
  renderMinimap(r);
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

const rowEls = new Map();

function renderMatch(r) {
  if (r.jpeg_b64) $("cmp-live").src = "data:image/jpeg;base64," + r.jpeg_b64;
  const rows = rankRows(r.match, r.location?.pano_id, placeInfo);
  const list = $("rank-list");
  const seen = new Set();
  for (const row of rows) {
    seen.add(row.place);
    let li = rowEls.get(row.place);
    if (!li) {
      li = document.createElement("li");
      li.innerHTML = '<span class="rank-n"></span><img class="rank-thumb" alt=""><div><span class="rank-name"></span><div class="bar"><i></i></div></div><span class="rank-pct"></span><span class="rank-in"></span>';
      li.querySelector("img").onerror = (e) => { e.target.style.visibility = "hidden"; };
      li.querySelector("img").src = row.thumb;
      rowEls.set(row.place, li);
      list.append(li);
    }
    li.className = "rank-row" + (row.here ? " here" : "");
    li.style.order = row.rank;
    li.querySelector(".rank-n").textContent = row.rank;
    li.querySelector(".rank-name").textContent = row.scan != null ? `${row.name} · scan ${row.scan}` : row.name;
    li.querySelector(".bar i").style.width = row.percent + "%";
    li.querySelector(".rank-pct").textContent = row.percent + "%";
    li.querySelector(".rank-in").textContent = row.inliers ? row.inliers + " in" : "";
  }
  for (const [place, li] of rowEls) {
    if (!seen.has(place)) { li.remove(); rowEls.delete(place); }
  }
  if (rows.length) {
    $("cmp-top").src = rows[0].thumb;
    $("cmp-top-name").textContent = rows[0].name;
  }
  const pano = r.location?.pano_id;
  if (pano && pano !== mapPano) {
    mapPano = pano;
    $("equirect").src = `/data/pano/${pano}/equirect.jpg`;
  }
  const svg = $("footprint");
  svg.replaceChildren();
  for (const pts of footprintPaths(r.match?.footprint, 2048, 1024)) {
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    poly.setAttribute("points", pts);
    svg.append(poly);
  }
}

function renderMinimap(r) {
  if (!mapData) return;
  const active = r.location?.state === "LOCKED" ? r.location?.pano_id : null;
  if (active) trail = pushTrail(trail, active, 8);
  const { floor, name, markers, walls, viewBox, otherFloor } = mapMarkers(mapData, active, r.match?.ranking, trail, 400, 400);
  const svg = $("minimap");
  const NS = "http://www.w3.org/2000/svg";
  const nodes = [];
  let scale = 1;
  if (viewBox && walls) {
    svg.setAttribute("viewBox", `${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`);
    scale = viewBox.w / 400;
    const path = document.createElementNS(NS, "path");
    path.setAttribute("class", "mm-walls");
    path.setAttribute("d", walls.map(([x1, y1, x2, y2]) => `M${x1} ${y1}L${x2} ${y2}`).join(""));
    nodes.push(path);
  } else {
    svg.setAttribute("viewBox", "0 0 400 400");
  }
  for (const m of markers) {
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("class", `mm-dot mm-${m.kind}`);
    c.setAttribute("cx", m.x); c.setAttribute("cy", m.y);
    c.setAttribute("r", ({ active: 12, estimate: 11, point: 5 }[m.kind] ?? 8) * scale);
    nodes.push(c);
  }
  svg.replaceChildren(...nodes);
  $("mm-floor").textContent = floor == null ? "" : `Floor ${floor + 1}`;
  const tag = otherFloor ? " (another floor)" : active ? "" : name ? " (estimate)" : "";
  $("mm-place").textContent = name ? `${name}${tag}` : "";
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
  let body = null;
  try { body = await r.json(); } catch {}
  return { ok: r.ok, status: r.status, body };
}

const say = (msg) => { $("warmup-text").textContent = msg; };

for (const [id, kind] of [["btn-pos", "pos"], ["btn-neg", "neg"]]) {
  $(id).onclick = async () => {
    const r = await post(`/api/capture/${kind}`);
    say(r.ok ? `Saved ${kind === "pos" ? "positive" : "negative"} frame` : errorText(r.status, r.body));
  };
}
$("btn-warmup").onclick = async () => {
  const r = await post("/api/capture/auto?seconds=20");
  if (!r.ok) { say(errorText(r.status, r.body)); return; }
  const seconds = r.body?.seconds ?? 20;
  $("warmup-progress").max = seconds;
  const poll = setInterval(async () => {
    const s = await (await fetch("/api/capture/auto")).json();
    $("warmup-progress").value = s.elapsed;
    say(`Warm-up ${s.elapsed.toFixed(0)}/${s.seconds} s, kept ${s.kept} frames`);
    if (!s.running) { clearInterval(poll); say(`Warm-up done, kept ${s.kept} frames`); }
  }, 500);
};

setInterval(async () => {
  try {
    const m = await (await fetch("/metrics")).json();
    $("latency").textContent = `p50 ${m.p50_ms.toFixed(0)} / p95 ${m.p95_ms.toFixed(0)} ms`;
  } catch {}
}, 2000);

fetch("/api/pano").then((r) => r.json()).then((p) => { faces = p.faces; placeInfo = p.places ?? {}; mapData = p.map ?? null; if (lastResult) { renderMatch(lastResult); renderMinimap(lastResult); } }).catch(() => {});
connect();
