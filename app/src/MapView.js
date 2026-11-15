// Google Maps style floor map: flat SVG in map metres under one view matrix, tilted with a native
// perspective + rotateX while following. The view glides between frame results (about 2 Hz).
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Animated, StyleSheet, View } from "react-native";
import Svg, { Circle, G, Path, Polyline } from "react-native-svg";
import {
  FOLLOW_PX_PER_M, TILT_DEG, ease, entrancePoint, fitView, floorExtent, followAnchor, followView, hasXY, lerpView,
  mapLayout, project, routePoints, screenAngle, viewMatrix, wallPath,
} from "./map";

const GLIDE_MS = 450; // a little under the frame interval, so the map settles before the next fix
const FRAME_MS = 33; // ~30 fps is smooth enough and keeps the big SVG cheap to redraw
const C = { ground: "#e8eaed", wall: "#9aa0a6", route: "#1a73e8", preview: "#7baaf7", casing: "#ffffff",
  puck: "#1a73e8", cone: "#1a73e8", pin: "#d93025" };

// Eases from wherever the view is now to the latest target; a new target mid-glide starts from there.
function useGlide(target) {
  const [cur, setCur] = useState(target);
  const st = useRef({ cur: target, raf: null });
  const key = target ? JSON.stringify(target) : "";
  useEffect(() => {
    const s = st.current;
    if (s.raf) cancelAnimationFrame(s.raf);
    if (!target || !s.cur) { s.cur = target; setCur(target); return; }
    const from = s.cur, t0 = Date.now();
    let last = 0;
    const tick = () => {
      const now = Date.now(), t = Math.min(1, (now - t0) / GLIDE_MS);
      s.cur = lerpView(from, target, ease(t));
      if (t >= 1 || now - last >= FRAME_MS) { last = now; setCur(s.cur); }
      s.raf = t < 1 ? requestAnimationFrame(tick) : null;
    };
    s.raf = requestAnimationFrame(tick);
    return () => { if (s.raf) cancelAnimationFrame(s.raf); s.raf = null; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return cur;
}

export default function MapView({ map, path, position, headingDeg, follow }) {
  const [size, setSize] = useState(null);
  const you = hasXY(position) ? position : null;
  const facing = Number.isFinite(headingDeg) ? headingDeg : null;
  const following = !!(follow && you && facing != null);
  const L = useMemo(() => (size ? mapLayout(size.w, size.h) : null), [size?.w, size?.h]); // eslint-disable-line react-hooks/exhaustive-deps
  const ext = useMemo(() => floorExtent(map), [map]);

  const target = useMemo(() => {
    if (!L) return null;
    const v = following ? followView(you, facing, followAnchor(L), FOLLOW_PX_PER_M) : fitView(ext, L.region);
    if (!v) return null;
    return { ...v, px: you?.x ?? null, py: you?.y ?? null, ph: facing };
  }, [L, following, you?.x, you?.y, facing, ext]); // eslint-disable-line react-hooks/exhaustive-deps
  const cur = useGlide(target);

  // Tilt runs on the native driver; the SVG only redraws for the view matrix and the puck.
  const tilt = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(tilt, { toValue: following ? TILT_DEG : 0, duration: GLIDE_MS, useNativeDriver: true }).start();
  }, [following, tilt]);
  const rotateX = tilt.interpolate({ inputRange: [0, 90], outputRange: ["0deg", "90deg"] });

  // Everything is projected to screen pixels in JS: no SVG matrix transform, which did not render on device.
  const f = (x, y) => { const [a, b] = project(cur, x, y); return `${a.toFixed(1)} ${b.toFixed(1)}`; };
  const walls = cur && map?.walls?.length ? map.walls.map(([x1, y1, x2, y2]) => `M${f(x1, y1)}L${f(x2, y2)}`).join("") : null;
  const route = cur && path?.pts?.length > 1 ? path.pts.map(([x, y]) => f(x, y).replace(" ", ",")).join(" ") : null;
  const content = (
    <>
      {walls ? <Path d={walls} stroke={C.wall} strokeWidth={5} strokeLinecap="round" strokeLinejoin="round" fill="none" /> : null}
      {route ? <Polyline points={route} stroke={C.casing} strokeWidth={15} strokeLinecap="round" strokeLinejoin="round" fill="none" /> : null}
      {route ? <Polyline points={route} stroke={path?.guiding ? C.route : C.preview} strokeWidth={9} strokeLinecap="round" strokeLinejoin="round" fill="none" /> : null}
    </>
  );

  const goal = entrancePoint(path, map);
  const pin = cur && goal ? project(cur, goal.x, goal.y) : null;
  const puck = cur && Number.isFinite(cur.px) && Number.isFinite(cur.py) ? project(cur, cur.px, cur.py) : null;
  const cone = puck && Number.isFinite(cur.ph) ? screenAngle(cur, cur.ph) : null;
  // The pin stands up: pre-stretched so the tilt does not squash it.
  const upright = following ? 1 / Math.cos((TILT_DEG * Math.PI) / 180) : 1;

  return (
    <View style={s.root} onLayout={(e) => setSize({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}
      accessible={false} importantForAccessibility="no-hide-descendants">
      {L && L.perspective > 0 ? (
        <Animated.View style={{ position: "absolute", left: L.left, top: L.top, width: L.lw, height: L.lh,
          transform: [{ perspective: L.perspective }, { rotateX }] }}>
          <Svg width={L.lw} height={L.lh}>
            {cur ? content : null}
            {pin ? (
              <G transform={`translate(${pin[0].toFixed(1)} ${pin[1].toFixed(1)}) scale(1 ${upright.toFixed(3)})`}>
                <Path d="M0 0 C-4 -10 -14 -16 -14 -27 A14 14 0 1 1 14 -27 C14 -16 4 -10 0 0 Z" fill={C.pin} stroke="#a50e0e" strokeWidth={1.5} />
                <Circle cx={0} cy={-27} r={5} fill="#fff" />
              </G>
            ) : null}
            {puck ? (
              <G transform={`translate(${puck[0].toFixed(1)} ${puck[1].toFixed(1)})`}>
                {cone != null ? (
                  <G transform={`rotate(${cone.toFixed(1)})`}>
                    <Path d="M0 0 L-26 -62 A67 67 0 0 1 26 -62 Z" fill={C.cone} opacity={0.22} />
                  </G>
                ) : null}
                <Circle r={22} fill={C.puck} opacity={0.15} />
                <Circle r={12} fill="#fff" />
                <Circle r={9} fill={C.puck} />
              </G>
            ) : null}
          </Svg>
        </Animated.View>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  root: { ...StyleSheet.absoluteFillObject, backgroundColor: C.ground, overflow: "hidden" },
});
