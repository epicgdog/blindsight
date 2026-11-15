// The HackPrinceton venue plan of one level as the map: the plan image, the room route on this
// level, the destination room, stair/elevator marks, and you (when you are on this level).
// Everything is drawn in the plan's own pixels under one viewBox, so it lines up exactly.
import React, { memo, useEffect, useMemo, useRef, useState } from "react";
import { Animated, PanResponder, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
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
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const MAX_ZOOM = 6;

// Pinch to zoom and drag to pan, with React Native's own touch system (no native gesture
// library). The map content gets transform [translate, scale] about the area's centre:
// screen = C + T + s * (p - C). The pinch midpoint stays over the same spot of the plan.
function useZoom(boxRef, insetTop) {
  const z = useRef({ s: 1, x: 0, y: 0, g: null });
  const aS = useRef(new Animated.Value(1)).current, aX = useRef(new Animated.Value(0)).current;
  const aY = useRef(new Animated.Value(0)).current;
  const apply = (sc, x, y) => {
    const b = boxRef.current;
    const mx = b ? (b.width * (sc - 1)) / 2 : 0, my = b ? (b.height * (sc - 1)) / 2 : 0;
    Object.assign(z.current, { s: sc, x: clamp(x, -mx, mx), y: clamp(y, -my, my) });
    aS.setValue(z.current.s); aX.setValue(z.current.x); aY.setValue(z.current.y);
  };
  const touch = (e) => {
    const t = e.nativeEvent.touches;
    if (t.length >= 2) {
      return { n: 2, d: Math.hypot(t[0].pageX - t[1].pageX, t[0].pageY - t[1].pageY),
        mx: (t[0].pageX + t[1].pageX) / 2, my: (t[0].pageY + t[1].pageY) / 2 - insetTop };
    }
    return { n: 1, d: 0, mx: t[0]?.pageX ?? 0, my: (t[0]?.pageY ?? 0) - insetTop };
  };
  const grant = (e) => {
    const b = boxRef.current, t = touch(e), c = z.current;
    const C = [b ? b.width / 2 : 0, b ? b.height / 2 : 0];
    // plan point under the fingers, in unzoomed area coordinates
    c.g = { ...t, s0: c.s, C, p: [C[0] + (t.mx - C[0] - c.x) / c.s, C[1] + (t.my - C[1] - c.y) / c.s] };
  };
  const pan = useRef(PanResponder.create({
    onStartShouldSetPanResponder: (e) => e.nativeEvent.touches.length >= 2,
    onMoveShouldSetPanResponder: (e, g) => e.nativeEvent.touches.length >= 2
      || (z.current.s > 1.01 && Math.abs(g.dx) + Math.abs(g.dy) > 8),
    onPanResponderGrant: grant,
    onPanResponderMove: (e) => {
      const g = z.current.g, t = touch(e);
      if (!g || t.n !== g.n) { grant(e); return; } // a finger added or lifted: start again from here
      const sc = t.n === 2 && g.d > 10 ? clamp((g.s0 * t.d) / g.d, 1, MAX_ZOOM) : g.s0;
      apply(sc, t.mx - g.C[0] - sc * (g.p[0] - g.C[0]), t.my - g.C[1] - sc * (g.p[1] - g.C[1]));
    },
    onPanResponderRelease: () => { z.current.g = null; },
    onPanResponderTerminate: () => { z.current.g = null; },
  })).current;
  // a touch in the area -> the same point of the unzoomed map
  const unzoom = (lx, ly) => {
    const b = boxRef.current, c = z.current;
    if (!b) return [lx, ly];
    const C = [b.width / 2, b.height / 2];
    return [C[0] + (lx - C[0] - c.x) / c.s, C[1] + (ly - C[1] - c.y) / c.s];
  };
  const style = { transform: [{ translateX: aX }, { translateY: aY }, { scale: aS }] };
  return { pan, style, unzoom, reset: () => apply(1, 0, 0) };
}

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
  const boxRef = useRef(null);
  boxRef.current = box;
  const zoom = useZoom(boxRef, insetTop);
  useEffect(() => { zoom.reset(); }, [lv?.id]); // eslint-disable-line react-hooks/exhaustive-deps
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

  const marks = (
    <Svg pointerEvents="none" style={StyleSheet.absoluteFill} width="100%" height="100%"
      viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
      {arrow ? <Line x1={dot[0]} y1={dot[1]} x2={arrow.tip[0]} y2={arrow.tip[1]} stroke="#1a73e8" strokeWidth={4} /> : null}
      {arrow ? <Polygon points={arrow.head} fill="#1a73e8" /> : null}
      {dot ? <Circle cx={dot[0]} cy={dot[1]} r={8} fill="#1a73e8" stroke="#fff" strokeWidth={3} /> : null}
    </Svg>
  );
  return (
    compact ? (
      <View style={[s.compact, { width: compact.width, height: compact.height }]}>
        <View style={[s.area, { top: 0, height: areaH }]} onLayout={(e) => setBox(e.nativeEvent.layout)}>
          <Plan lv={lv} uri={uri} route={route} dest={dest} extra={extra} viewBox={viewBox} />
          {marks}
        </View>
      </View>
    ) : (
      <View style={s.root}>
        <View style={[s.area, s.clip, { top: insetTop, height: areaH }]} onLayout={(e) => setBox(e.nativeEvent.layout)}
          {...zoom.pan.panHandlers}>
          <Pressable style={StyleSheet.absoluteFill} disabled={!onTapMap} accessible={false} onPress={(e) => {
            if (!onTapMap || !box || !align) return;
            const [lx, ly] = zoom.unzoom(e.nativeEvent.locationX, e.nativeEvent.locationY);
            const px = touchToPx(box, lv.width, lv.height, lx, ly);
            if (px) { const [x, y] = pxToMap(align, px[0], px[1]); onTapMap({ x, y, level: lv.id }); }
          }}>
            <Animated.View style={[StyleSheet.absoluteFill, zoom.style]}>
              <Plan lv={lv} uri={uri} route={route} dest={dest} extra={extra} viewBox={viewBox} />
              {marks}
            </Animated.View>
          </Pressable>
        </View>
      </View>
    )
  );
}

const s = StyleSheet.create({
  compact: { backgroundColor: "#ffffff", overflow: "hidden", alignItems: "center", justifyContent: "center" },
  clip: { overflow: "hidden" },
  root: { ...StyleSheet.absoluteFillObject, backgroundColor: "#ffffff", alignItems: "center", justifyContent: "center" },
  area: { position: "absolute", left: 0, right: 0 },
  note: { color: "#5f6368", fontSize: 16 },
});
