// The HackPrinceton venue plan of one level as the map: the plan image, the room route on this
// level, the destination room, stair/elevator marks, and you (when you are on this level).
// Everything is drawn in the plan's own pixels under one viewBox, so it lines up exactly.
import React, { memo, useEffect, useMemo, useRef, useState } from "react";
import { Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import Svg, { Circle, G, Image as SvgImage, Line, Path, Polygon, Polyline, Text as SvgText } from "react-native-svg";
import { mapToPx, pxToMap, touchToPx, transferMarks } from "./venue";

const TICK_MS = 33;
const LERP_RATE = 8; // fraction of the gap closed per second: 1 - exp(-rate * dt)

// Lerp toward the latest value every TICK_MS; the first value is taken as is, null clears.
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
      const now = Date.now(), k = 1 - Math.exp((-LERP_RATE * (now - st.current.t)) / 1000), tgt = latest.current;
      st.current.t = now;
      if (tgt == null) return;
      st.current.cur = st.current.cur == null ? tgt : mix(st.current.cur, tgt, k);
      setCur(st.current.cur);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [has]); // eslint-disable-line react-hooks/exhaustive-deps
  return cur;
}
const mixPoint = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
const mixAngle = (a, b, k) => { const d = ((b - a + 540) % 360) - 180; return (a + d * k + 360) % 360; };

const pts = (list) => list.map(([u, v]) => `${u},${v}`).join(" ");

// Plan image and everything that only changes with the route: redrawn only when those change.
const Plan = memo(function Plan({ lv, uri, route, dest, extra, viewBox }) {
  const leg = route?.ok ? route.legs.filter((l) => l.level === lv.id) : [];
  const marks = transferMarks(route, lv.id);
  const here = dest && dest.level === lv.id ? dest : null;
  return (
    <Svg pointerEvents="none" style={StyleSheet.absoluteFill} width="100%" height="100%"
      viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
      {uri ? <SvgImage href={{ uri }} x={0} y={0} width={lv.width} height={lv.height} preserveAspectRatio="xMidYMid meet" /> : null}
      {extra}
      {here ? (
        <Circle cx={here.label_px[0]} cy={here.label_px[1]} r={26} fill="rgba(217,48,37,0.18)" stroke="#d93025" strokeWidth={4} />
      ) : null}
      {leg.map((l, i) => (
        <G key={i}>
          <Polyline points={pts(l.px)} stroke="#ffffff" strokeWidth={11} strokeLinecap="round" strokeLinejoin="round" fill="none" />
          <Polyline points={pts(l.px)} stroke="#1a73e8" strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </G>
      ))}
      {marks.map((m, i) => (
        <G key={`m${i}`}>
          <Circle cx={m.px[0]} cy={m.px[1]} r={13} fill="#1a73e8" stroke="#fff" strokeWidth={3} />
          <SvgText x={m.px[0]} y={m.px[1] + 5} fontSize={14} fontWeight="bold" fill="#fff" textAnchor="middle">
            {m.kind === "elevator" ? "E" : "S"}
          </SvgText>
          <SvgText x={m.px[0] + 18} y={m.px[1] - 12} fontSize={13} fontWeight="bold" fill="#1a73e8">
            {m.going ? `to ${m.to}` : `from ${m.to}`}
          </SvgText>
        </G>
      ))}
      {here ? <PinMark x={here.door_px[0]} y={here.door_px[1]} /> : null}
    </Svg>
  );
});

function PinMark({ x, y, color = "#d93025" }) {
  const d = `M${x} ${y} C${x - 4} ${y - 9} ${x - 11} ${y - 13} ${x - 11} ${y - 21} A11 11 0 1 1 ${x + 11} ${y - 21} C${x + 11} ${y - 13} ${x + 4} ${y - 9} ${x} ${y} Z`;
  return (<>
    <Path d={d} fill={color} stroke="#fff" strokeWidth={2} />
    <Circle cx={x} cy={y - 21} r={4} fill="#fff" />
  </>);
}

// compact: { width, height, spanPx } draws a small map (the minimap on the camera screen),
// zoomed to spanPx plan pixels across, centred on you (else the route start, else the room).
export default function VenueMap({ uri, lv, route, dest, you, headingDeg, insetTop = 0, insetBottom = 0, onTapMap,
  entrancePath, pin, pinDraft, compact }) {
  const { height: winH } = useWindowDimensions();
  const [box, setBox] = useState(null);
  const areaH = compact ? compact.height : Math.max(120, winH - insetTop - insetBottom); // explicit height: layout-derived ones came out 0
  const align = lv?.align;
  const youPx = you && lv && you.level === lv.id ? you.px : null;
  const dot = useLerp(youPx, mixPoint);
  const facing = useLerp(youPx && Number.isFinite(headingDeg) ? headingDeg - (align?.rot ?? 0) : null, mixAngle);

  // The entrance/pin route and pins are in map metres, on the ground floor scan only. Memoized:
  // the dot redraws at ~30 fps and must not redraw the plan image with it.
  const ground = lv?.id === "1" && align;
  const extra = useMemo(() => {
    if (!ground) return null;
    const conv = (x, y) => mapToPx(align, x, y);
    return (
      <G>
      {entrancePath?.guiding && entrancePath.pts?.length > 1 ? (
        <Polyline points={pts(entrancePath.pts.map(([x, y]) => conv(x, y)))} stroke="#1a73e8" strokeWidth={6}
          strokeLinecap="round" strokeLinejoin="round" fill="none" />
      ) : null}
      {pinDraft?.pts?.length > 1 ? (
        <Polyline points={pts(pinDraft.pts.map(([x, y]) => conv(x, y)))} stroke="#1a73e8" strokeWidth={5}
          strokeDasharray="14 10" strokeLinecap="round" fill="none" />
      ) : null}
      {pin ? <PinMark {...(([x, y]) => ({ x, y }))(conv(pin[0], pin[1]))} /> : null}
      {pinDraft && Number.isFinite(pinDraft.x) ? <PinMark color="#f29900" {...(([x, y]) => ({ x, y }))(conv(pinDraft.x, pinDraft.y))} /> : null}
    </G>
    );
  }, [ground, align, entrancePath, pin, pinDraft]);
  if (!lv) return <View style={compact ? [s.compact, { width: compact.width, height: compact.height }] : s.root}><Text style={s.note}>Loading map…</Text></View>;
  const viewBox = compact ? compactView(lv, compact, dot ?? (route?.ok && route.legs[0].level === lv.id ? route.legs[0].px[0] : null)
    ?? (dest?.level === lv.id ? dest.door_px : null)) : `0 0 ${lv.width} ${lv.height}`;

  let arrow = null;
  if (dot && facing != null) {
    const t = (facing * Math.PI) / 180, dx = Math.sin(t), dy = -Math.cos(t), L = 26;
    const tip = [dot[0] + dx * L, dot[1] + dy * L];
    const l = [dot[0] + dx * L * 0.55 - dy * 9, dot[1] + dy * L * 0.55 + dx * 9];
    const r = [dot[0] + dx * L * 0.55 + dy * 9, dot[1] + dy * L * 0.55 - dx * 9];
    arrow = { tip, head: [tip, l, r].map((q) => q.map((v) => v.toFixed(1)).join(",")).join(" ") };
  }

  return (
    <View style={compact ? [s.compact, { width: compact.width, height: compact.height }] : s.root}>
      <Pressable style={[s.area, { top: compact ? 0 : insetTop, height: areaH }]} onLayout={(e) => setBox(e.nativeEvent.layout)}
        disabled={!onTapMap} accessible={false} onPress={(e) => {
          if (!onTapMap || !box || !align) return;
          const px = touchToPx(box, lv.width, lv.height, e.nativeEvent.locationX, e.nativeEvent.locationY);
          if (px) { const [x, y] = pxToMap(align, px[0], px[1]); onTapMap({ x, y, level: lv.id }); }
        }}>
        <Plan lv={lv} uri={uri} route={route} dest={dest} extra={extra} viewBox={viewBox} />
        <Svg pointerEvents="none" style={StyleSheet.absoluteFill} width="100%" height="100%"
          viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
          {arrow ? <Line x1={dot[0]} y1={dot[1]} x2={arrow.tip[0]} y2={arrow.tip[1]} stroke="#1a73e8" strokeWidth={4} /> : null}
          {arrow ? <Polygon points={arrow.head} fill="#1a73e8" /> : null}
          {dot ? <Circle cx={dot[0]} cy={dot[1]} r={8} fill="#1a73e8" stroke="#fff" strokeWidth={3} /> : null}
        </Svg>
      </Pressable>
    </View>
  );
}

// viewBox of a compact map: spanPx wide around the centre, snapped to a 12 px grid so the plan
// image is not redrawn on every small move of the dot.
export function compactView(lv, compact, centre) {
  const w = compact.spanPx, h = (compact.spanPx * compact.height) / compact.width;
  const q = (v) => Math.round(v / 12) * 12;
  const [cx, cy] = centre ? [q(centre[0]), q(centre[1])] : [lv.width / 2, lv.height / 2];
  return `${(cx - w / 2).toFixed(0)} ${(cy - h / 2).toFixed(0)} ${w.toFixed(0)} ${h.toFixed(0)}`;
}

const s = StyleSheet.create({
  compact: { backgroundColor: "#ffffff", overflow: "hidden", alignItems: "center", justifyContent: "center" },
  root: { ...StyleSheet.absoluteFillObject, backgroundColor: "#ffffff", alignItems: "center", justifyContent: "center" },
  area: { position: "absolute", left: 0, right: 0 },
  note: { color: "#5f6368", fontSize: 16 },
});
