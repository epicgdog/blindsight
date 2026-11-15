// The route drawn on the floor of the camera image (see arProject.js). Redraws ~15 times a
// second from getPose(), which reads the latest server fix and the live phone sensors; the
// position and heading are eased so the path does not jump at each server frame.
import React, { useEffect, useRef, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import Svg, { Circle, Line, Polygon } from "react-native-svg";
import { arScene, wrap180 } from "./arProject";

const TICK_MS = 66;
const POS_RATE = 4, HEAD_RATE = 8; // fraction of the gap closed per second: 1 - exp(-rate * dt)
const JUMP_M = 5; // a bigger jump is taken at once

const pts = (list) => list.map(([u, v]) => `${u.toFixed(1)},${v.toFixed(1)}`).join(" ");

export default function ArOverlay({ width, height, getPose, cam }) {
  const [, setTick] = useState(0);
  const st = useRef({ pos: null, head: null, t: 0 });
  useEffect(() => {
    const id = setInterval(() => setTick((n) => (n + 1) % 1e6), TICK_MS);
    return () => clearInterval(id);
  }, []);

  const pose = getPose();
  const now = Date.now(), dt = Math.min(0.5, (now - (st.current.t || now)) / 1000);
  st.current.t = now;
  if (!pose) { st.current.pos = null; st.current.head = null; return null; }
  const s = st.current;
  if (!s.pos || Math.hypot(pose.pos[0] - s.pos[0], pose.pos[1] - s.pos[1]) > JUMP_M) s.pos = pose.pos;
  else { const k = 1 - Math.exp(-POS_RATE * dt); s.pos = [s.pos[0] + (pose.pos[0] - s.pos[0]) * k, s.pos[1] + (pose.pos[1] - s.pos[1]) * k]; }
  if (s.head == null) s.head = pose.headingDeg;
  else s.head = (s.head + wrap180(pose.headingDeg - s.head) * (1 - Math.exp(-HEAD_RATE * dt)) + 360) % 360;

  const sc = arScene({ route: pose.pts, pos: s.pos, headingDeg: s.head, endIsDest: pose.endIsDest,
    cam: { ...cam, width, height, pitchDeg: pose.pitchDeg, rollDeg: pose.rollDeg } });
  const hint = { left: "◀  Turn left", right: "Turn right  ▶", behind: "Turn around" }[sc.hint];
  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="none">
      <Svg width={width} height={height} style={StyleSheet.absoluteFill}>
        {sc.quads.map((q, i) => <Polygon key={`q${i}`} points={pts(q.pts)} fill="#1a73e8" fillOpacity={q.alpha} />)}
        {sc.chevrons.map((c, i) => <Polygon key={`c${i}`} points={pts(c.pts)} fill="#ffffff" fillOpacity={0.9} />)}
        {sc.pin ? <Line x1={sc.pin.base[0]} y1={sc.pin.base[1]} x2={sc.pin.top[0]} y2={sc.pin.top[1]} stroke="#d93025" strokeWidth={4} /> : null}
        {sc.pin ? <Circle cx={sc.pin.top[0]} cy={sc.pin.top[1]} r={16} fill="#d93025" stroke="#fff" strokeWidth={3} /> : null}
      </Svg>
      {hint ? (
        <View style={[s2.hint, sc.hint === "left" ? s2.left : sc.hint === "right" ? s2.right : s2.mid]}>
          <Text style={s2.hintText}>{hint}</Text>
        </View>
      ) : null}
    </View>
  );
}

const s2 = StyleSheet.create({
  hint: { position: "absolute", top: "48%", backgroundColor: "rgba(26,115,232,0.92)", borderRadius: 22,
    paddingHorizontal: 16, paddingVertical: 10 },
  left: { left: 12 },
  right: { right: 12 },
  mid: { alignSelf: "center", left: 0, right: 0, marginHorizontal: 100, alignItems: "center" },
  hintText: { color: "#fff", fontSize: 18, fontWeight: "800" },
});
