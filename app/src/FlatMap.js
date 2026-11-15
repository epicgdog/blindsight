// Plain 2D floor map, the same drawing as the web viewer's minimap: walls, scan points, route,
// entrance and you, north up, fitted to the screen with an SVG viewBox. No tilt.
// The "you" dot and its arrow lerp toward the latest fix at ~30 fps, on their own layer so the
// walls are not redrawn each tick.
import React, { memo, useEffect, useMemo, useRef, useState } from "react";
import { StyleSheet, Text, View, useWindowDimensions } from "react-native";
import Svg, { Circle, Line, Path, Polygon, Polyline } from "react-native-svg";

// Fraction of the remaining gap closed per second (frame-rate independent): 1 - exp(-LERP_RATE * dt).
// 8/s closes ~93 % of a jump in a third of a second, about one frame result.
const LERP_RATE = 8;
const step = (dt) => 1 - Math.exp(-LERP_RATE * dt);

const PX_PER_M = 40; // drawing units per metre (the viewBox scales them to the screen)
const MARGIN_M = 2;
const TICK_MS = 33;
const ENTRANCE_ID = "n0ppizy9u8xcadypd2bawceyc";

function frameOf(map) {
  const pts = (map?.points ?? []).filter((p) => (p.floor ?? 0) === 0);
  if (!pts.length) return null;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const minX = Math.min(...xs) - MARGIN_M, maxX = Math.max(...xs) + MARGIN_M;
  const minY = Math.min(...ys) - MARGIN_M, maxY = Math.max(...ys) + MARGIN_M;
  const px = (x) => +((x - minX) * PX_PER_M).toFixed(1);
  const py = (y) => +((maxY - y) * PX_PER_M).toFixed(1); // north (larger y) at the top
  return { pts, px, py, viewBox: `0 0 ${px(maxX)} ${py(minY)}` };
}

// Walls, route, scan points, entrance: redrawn only when their inputs change.
const StaticLayer = memo(function StaticLayer({ f, map, path, ranking, activeId, debugDots }) {
  const { pts, px, py } = f;
  const ranked = debugDots ? (ranking ?? []).map((r) => r.place) : [];
  const walls = (map.walls ?? []).map(([x1, y1, x2, y2]) => `M${px(x1)} ${py(y1)}L${px(x2)} ${py(y2)}`).join("");
  const route = path?.pts?.length > 1 ? path.pts.map(([x, y]) => `${px(x)},${py(y)}`).join(" ") : null;
  const goal = pts.find((p) => p.id === ENTRANCE_ID) ?? (path?.goal ? { x: path.goal[0], y: path.goal[1] } : null);
  return (
    <Svg style={StyleSheet.absoluteFill} width="100%" height="100%" viewBox={f.viewBox} preserveAspectRatio="xMidYMid meet">
      {walls ? <Path d={walls} stroke="#9aa0a6" strokeWidth={6} strokeLinecap="round" fill="none" /> : null}
      {route ? <Polyline points={route} stroke="#ffffff" strokeWidth={22} strokeLinecap="round" strokeLinejoin="round" fill="none" /> : null}
      {route ? <Polyline points={route} stroke={path?.guiding ? "#1a73e8" : "#7baaf7"} strokeWidth={14} strokeLinecap="round" strokeLinejoin="round" fill="none" /> : null}
      {debugDots && pts.map((p) => {
        // Scan points (grey), matched place (green) and top candidates (yellow): only with "Debug dots" on.
        const kind = debugDots && p.id === activeId ? "active" : ranked.slice(0, 3).includes(p.id) ? "candidate" : "point";
        return <Circle key={p.id} cx={px(p.x)} cy={py(p.y)} r={kind === "point" ? 7 : 12}
          fill={kind === "active" ? "#17c964" : kind === "candidate" ? "#f5a524" : "#bdc1c6"} />;
      })}
      {goal ? <Circle cx={px(goal.x)} cy={py(goal.y)} r={18} fill="#d93025" stroke="#fff" strokeWidth={5} /> : null}
    </Svg>
  );
});

// Lerp toward the latest value every TICK_MS. mix(cur, target, k) does the blend; the first value
// is taken as is, and a missing value clears it.
function useLerp(target, mix) {
  const latest = useRef(target);
  latest.current = target;
  const [cur, setCur] = useState(target);
  const st = useRef({ cur: target, t: 0 });
  const has = target != null;
  useEffect(() => {
    if (!has) { st.current.cur = null; setCur(null); return; }
    st.current.t = Date.now();
    const id = setInterval(() => {
      const now = Date.now(), k = step((now - st.current.t) / 1000), tgt = latest.current;
      st.current.t = now;
      if (tgt == null) return;
      st.current.cur = st.current.cur == null ? tgt : mix(st.current.cur, tgt, k);
      setCur(st.current.cur);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [has]); // eslint-disable-line react-hooks/exhaustive-deps
  return cur;
}

const mixPoint = (a, b, k) => ({ x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k });
// Shortest way round: 350 -> 10 goes through 0, not 180.
const mixAngle = (a, b, k) => { const d = ((b - a + 540) % 360) - 180; return (a + d * k + 360) % 360; };

export default function FlatMap({ map, path, position, headingDeg, ranking, activeId, debugDots = false, insetTop = 150, insetBottom = 110 }) {
  const f = useMemo(() => frameOf(map), [map]);
  const { height: winH } = useWindowDimensions();
  const areaH = Math.max(120, winH - insetTop - insetBottom); // explicit: layout-derived heights came out 0
  const [box, setBox] = useState(null); // measured map area, shown when it is empty (debug aid)
  const fix = Number.isFinite(position?.x) && Number.isFinite(position?.y) ? { x: position.x, y: position.y } : null;
  // No tracked position yet: stand on the decided scan point.
  // Hold the last tracked fix through frames without one (unmatched frames, a scan point switch):
  // falling back to the scan point there made the dot jump metres and back. The scan point is
  // used only before the first fix, or after 15 s without one.
  const last = useRef(null);
  if (fix) last.current = { ...fix, t: Date.now() };
  const held = last.current && Date.now() - last.current.t < 15000 ? last.current : null;
  const anchor = fix ?? held ?? (() => { const a = f?.pts.find((p) => p.id === activeId); return a ? { x: a.x, y: a.y } : null; })();
  const you = useLerp(anchor, mixPoint);
  const heading = useLerp(Number.isFinite(headingDeg) ? headingDeg : null, mixAngle);

  if (!f) {
    return <View style={[s.root, { paddingTop: insetTop, paddingBottom: insetBottom }]}><Text style={s.note}>{map ? "Map has no points" : "Loading map…"}</Text></View>;
  }
  const here = you ? { x: f.px(you.x), y: f.py(you.y) } : null;
  let arrow = null;
  if (here && heading != null) {
    const t = (heading * Math.PI) / 180, dx = Math.sin(t), dy = -Math.cos(t), L = 60;
    const tip = [here.x + dx * L, here.y + dy * L];
    const l = [here.x + dx * L * 0.6 - dy * 20, here.y + dy * L * 0.6 + dx * 20];
    const r = [here.x + dx * L * 0.6 + dy * 20, here.y + dy * L * 0.6 - dx * 20];
    arrow = { tip, head: [tip, l, r].map((q) => q.map((v) => v.toFixed(1)).join(",")).join(" ") };
  }

  return (
    <View style={s.root}>
      <View style={[s.area, { top: insetTop, height: areaH }]} onLayout={(e) => setBox(e.nativeEvent.layout)}>
        {box && (box.width < 10 || box.height < 10) ? <Text style={s.note}>Map area is {Math.round(box.width)} x {Math.round(box.height)}</Text> : null}
        <StaticLayer f={f} map={map} path={path} ranking={ranking} activeId={activeId} debugDots={debugDots} />
        <Svg style={StyleSheet.absoluteFill} width="100%" height="100%" viewBox={f.viewBox} preserveAspectRatio="xMidYMid meet">
          {arrow ? <Line x1={here.x} y1={here.y} x2={arrow.tip[0]} y2={arrow.tip[1]} stroke="#1a73e8" strokeWidth={8} /> : null}
          {arrow ? <Polygon points={arrow.head} fill="#1a73e8" /> : null}
          {here ? <Circle cx={here.x} cy={here.y} r={16} fill="#1a73e8" stroke="#fff" strokeWidth={6} /> : null}
        </Svg>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  root: { ...StyleSheet.absoluteFillObject, backgroundColor: "#e8eaed", alignItems: "center", justifyContent: "center" },
  area: { position: "absolute", left: 0, right: 0 }, // top/bottom from the insets: flex gave it 0 height
  note: { color: "#5f6368", fontSize: 16 },
});
