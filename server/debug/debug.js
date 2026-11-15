// Insight debug page: every localization stage for each frame (server/debug_server.py).
import { mapMarkers, pathLines, headingArrow, scene3d } from "/viewer/logic.js";

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
let mapData = null, map3d = null, paused = false, last = null, times = [];

fetch("/api/pano").then((r) => r.json()).then((p) => { mapData = p.map ?? null; if (last) render(last); }).catch(() => {});
import("/viewer/map3d.js").then((m) => { map3d = m.createMap3d($("map3d")); if (last) render(last); }).catch(() => {});

// ---------- frame <-> reference with matched points ----------
const imgCache = new Map();
function loadImg(src) {
  if (!src) return Promise.resolve(null);
  if (imgCache.has(src)) return imgCache.get(src);
  const p = new Promise((ok) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => ok(null); i.src = src; });
  if (!src.startsWith("data:")) { imgCache.set(src, p); if (imgCache.size > 300) imgCache.delete(imgCache.keys().next().value); }
  return p;
}

async function drawPair(r, pts) {
  const c = $("pair"), g = c.getContext("2d");
  const W = c.width / 2, H = c.height;
  const refSrc = pts ? `/api/ref/${pts.ref}.jpg?place=${encodeURIComponent(pts.place)}&name=${encodeURIComponent(pts.ref_name)}` : null;
  const [live, ref] = await Promise.all([loadImg(r.jpeg_b64 ? "data:image/jpeg;base64," + r.jpeg_b64 : null), loadImg(refSrc)]);
  if (r !== last) return; // a newer frame arrived meanwhile
  g.fillStyle = "#000"; g.fillRect(0, 0, c.width, c.height);
  if (live) g.drawImage(live, 0, 0, W, H);
  if (ref) g.drawImage(ref, W, 0, W, H);
  g.fillStyle = "#fff"; g.font = "16px ui-monospace, monospace";
  g.fillText("live", 8, 20); g.fillText(pts ? `${pts.ref_name} @ ${pts.place.slice(0, 8)}` : "no candidate", W + 8, 20);
  if (!pts) return;
  // putative matches (before RANSAC): faint red; rotation-consistent inliers: green lines
  g.strokeStyle = "rgba(243,18,96,0.35)"; g.lineWidth = 1;
  pts.live_matches.forEach(([x, y], i) => {
    const q = pts.ref_matches[i]; if (!q) return;
    g.beginPath(); g.moveTo(x * W, y * H); g.lineTo(W + q[0] * W, q[1] * H); g.stroke();
  });
  g.strokeStyle = "rgba(23,201,100,0.9)"; g.lineWidth = 1.5;
  pts.live_inliers.forEach(([x, y], i) => {
    const q = pts.ref_inliers[i]; if (!q) return;
    g.beginPath(); g.moveTo(x * W, y * H); g.lineTo(W + q[0] * W, q[1] * H); g.stroke();
  });
  $("pair-caption").textContent = `${pts.live_inliers.length} inliers (green) of ${pts.live_matches.length} matches (red, first 200 drawn)`;
}

// ---------- 2D / 3D maps ----------
function drawMaps(r, steps) {
  if (!mapData) return;
  const loc = r.location ?? {};
  const match = r.match ?? r.localization_debug?.match ?? null;
  const pos = match?.position ?? null;
  const heading = r.agent?.heading ?? null;
  const path = r.agent?.path ?? null;
  const active = loc.state === "LOCKED" ? loc.pano_id : null;
  const { markers, walls, viewBox, project } = mapMarkers(mapData, active, match?.ranking, [], 400, 400, pos);
  const svg = $("map2d"), nodes = [];
  let scale = 1;
  if (viewBox && walls) {
    svg.setAttribute("viewBox", `${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`);
    scale = viewBox.w / 400;
    const p = document.createElementNS(NS, "path"); p.setAttribute("class", "mm-walls");
    p.setAttribute("d", walls.map(([a, b, c, d]) => `M${a} ${b}L${c} ${d}`).join("")); nodes.push(p);
  }
  const lines = pathLines(path, project);
  if (lines) {
    for (const [cls, pts] of [["mm-straight", lines.straight], ["mm-route", lines.route]]) {
      if (!pts) continue;
      const l = document.createElementNS(NS, "polyline"); l.setAttribute("class", cls); l.setAttribute("points", pts); nodes.push(l);
    }
  }
  for (const m of markers) {
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("class", `mm-dot mm-${m.kind}`); c.setAttribute("cx", m.x); c.setAttribute("cy", m.y);
    c.setAttribute("r", ({ active: 10, estimate: 9, you: 8, point: 4 }[m.kind] ?? 6) * scale); nodes.push(c);
  }
  // the matched pano (ring) and the raw PnP fix (red), both from this frame's stages
  const best = steps?.["5_decide"]?.best;
  const pano = best && mapData.points.find((p) => p.id === best.place);
  if (pano && project) {
    const [x, y] = project(pano.x, pano.y);
    const c = document.createElementNS(NS, "circle"); c.setAttribute("class", "mm-pano");
    c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", 16 * scale); nodes.push(c);
  }
  const raw = steps?.["6_pnp"]?.world;
  if (raw && project) {
    const [x, y] = project(raw[0], raw[1]);
    const c = document.createElementNS(NS, "circle"); c.setAttribute("class", "mm-raw");
    c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", 5 * scale); nodes.push(c);
  }
  const at = markers.find((m) => m.kind === "you") ?? markers.find((m) => m.kind === "active");
  const arrow = at && headingArrow(at.x, at.y, heading?.heading_deg, 34 * scale);
  if (arrow) {
    const l = document.createElementNS(NS, "line"); const [x1, y1, x2, y2] = arrow.line;
    Object.entries({ x1, y1, x2, y2, class: "mm-arrow" }).forEach(([k, v]) => l.setAttribute(k, v));
    const h = document.createElementNS(NS, "polygon"); h.setAttribute("points", arrow.head); h.setAttribute("class", "mm-arrow-head");
    nodes.push(l, h);
  }
  svg.replaceChildren(...nodes);
  if (map3d) map3d.update(scene3d(mapData, active, match?.ranking, [], pos, heading?.heading_deg, path));
  const f = (v) => (v == null ? "–" : v);
  $("where").textContent = pos
    ? `tracked x ${f(pos.x)} y ${f(pos.y)} z ${f(pos.z)} ±${f(pos.sigma_m)} m (${f(pos.source)}) · raw ${raw ? raw.join(", ") : "–"} · heading ${f(heading?.heading_deg)}° ±${f(heading?.sigma_deg)} (${f(heading?.source)}) · compass ${heading?.compass_deg != null ? Math.round(heading.compass_deg) : "–"}° · route ${f(path?.length_m)} m`
    : "no tracked position yet";
}

// ---------- stage tables ----------
const SUMMARY = {
  "1_input": (s) => `${s.frame_wh?.join("x")} → ${s.prepared_wh?.join("x")}, hfov ${s.hfov_deg}, heading ${s.heading_deg ?? "–"}, pitch ${s.pitch_deg ?? "–"}, moving ${s.moving}`,
  "2_retrieval": (s) => `${s.embedded_now ? "fresh" : "REUSED"} embedding, ${s.ms} ms, top ${s.picks?.[0]?.place?.slice(0, 8) ?? "–"} sim ${s.picks?.[0]?.sim ?? "–"}`,
  "3_features": (s) => `${s.live_keypoints} keypoints, ${s.ms} ms`,
  "4_verify": (s) => `${s.cands?.length ?? 0} candidates, best ${s.cands?.[0]?.rotation_inliers ?? 0} inliers (need ${s.n_min}, grid ${s.grid_min}), ${s.ms} ms`,
  "5_decide": (s) => s.matched ? `MATCHED ${s.best?.place?.slice(0, 8)} ${s.best?.name} (${s.best?.inliers})` : `no match${s.imu_rejected ? " · pitch gate" : ""}${s.jump_rejected ? " · tracker jump" : ""}`,
  "6_pnp": (s) => `${s.result ?? "–"}${s.pnp_inliers != null ? ` · ${s.pnp_inliers} inliers of ${s.mesh_hits} mesh hits / ${s.pairs} pairs · ${s.reproj_px} px · ${s.offset_m} m from pano` : ""}`,
  "7_tracker": (s) => `${s.accepted ? "accepted" : s.rejected ? "REJECTED" : "no measurement"}${s.reinit ? " (reinit)" : ""} · d2 ${s.d2 ?? "–"} · ±${s.sigma_m ?? "–"} m · ${s.used_pnp ? "pnp" : "scan point"}`,
};

function cell(v) {
  if (v == null) return "–";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function table(rows) {
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const t = document.createElement("table");
  t.innerHTML = `<tr>${keys.map((k) => `<th>${k}</th>`).join("")}</tr>`;
  for (const r of rows) {
    const tr = document.createElement("tr");
    if ("verdict" in r) tr.className = r.verdict === "kept" ? "kept" : "out";
    for (const k of keys) { const td = document.createElement("td"); td.textContent = cell(r[k]); tr.append(td); }
    t.append(tr);
  }
  const w = document.createElement("div"); w.className = "wrap"; w.append(t); return w;
}

function kv(obj) {
  const t = document.createElement("table");
  for (const [k, v] of Object.entries(obj)) {
    if (Array.isArray(v) && v.length && typeof v[0] === "object") continue; // shown as its own table
    const tr = document.createElement("tr"), a = document.createElement("th"), b = document.createElement("td");
    a.textContent = k; b.textContent = cell(v); tr.append(a, b); t.append(tr);
  }
  return t;
}

const openState = {};
function drawSteps(steps) {
  const host = $("step-list");
  if (!steps) { host.textContent = "No stage data: is the main server running the real localizer (LOCALIZER=real, LOCALIZATION_SCOPE=single)?"; return; }
  if (steps.error) { host.textContent = "stage data error: " + steps.error; return; }
  const out = [];
  for (const [name, s] of Object.entries(steps)) {
    if (name === "points" || s == null) continue;
    const d = document.createElement("details");
    d.open = openState[name] ?? true;
    d.ontoggle = () => { openState[name] = d.open; };
    const sum = document.createElement("summary");
    sum.textContent = name;
    const sp = document.createElement("span"); sp.className = "s"; sp.textContent = SUMMARY[name]?.(s) ?? ""; sum.append(sp);
    d.append(sum, kv(s));
    for (const [k, v] of Object.entries(s)) if (Array.isArray(v) && v.length && typeof v[0] === "object") d.append(table(v));
    out.push(d);
  }
  host.replaceChildren(...out);
}

// ---------- frame ----------
function render(r) {
  last = r;
  const loc = r.location ?? {}, dbg = r.localization_debug ?? {}, steps = dbg.steps;
  $("state").textContent = loc.state ?? "–"; $("state").className = "pill " + (loc.state ?? "");
  $("place").textContent = `${loc.label ?? "–"} · ${(loc.pano_id ?? "").slice(0, 8)} · agent ${r.agent?.decision?.state ?? "–"}`;
  $("reason").textContent = dbg.reason ?? "–";
  const now = performance.now(); times = [...times, now].slice(-20);
  const fps = times.length > 1 ? ((times.length - 1) * 1000) / (now - times[0]) : 0;
  $("timing").textContent = `#${r.seq} · ${fps.toFixed(1)} fps · localizer ${dbg.total ?? "–"} ms · server ${Math.round(r.server_ms?.total ?? 0)} ms`;
  drawPair(r, steps?.points);
  drawMaps(r, steps);
  drawSteps(steps);
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { $("conn").textContent = "connected"; };
  ws.onmessage = (e) => { if (paused) return; try { render(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  ws.onclose = () => { $("conn").textContent = "reconnecting…"; setTimeout(connect, 1000); };
}

$("pause").onclick = () => { paused = !paused; $("pause").textContent = paused ? "Resume" : "Pause"; };
$("copy").onclick = () => {
  if (!last) return;
  const { jpeg_b64, ...rest } = last;
  navigator.clipboard?.writeText(JSON.stringify(rest, null, 1));
  $("copy").textContent = "Copied"; setTimeout(() => { $("copy").textContent = "Copy frame JSON"; }, 1200);
};
connect();
