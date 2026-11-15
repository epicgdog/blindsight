// Small SVG icons for the navigation screen (no icon font is bundled with this app).
import React from "react";
import Svg, { Circle, G, Path, Rect } from "react-native-svg";

// Right-hand arrows on a 48 box; the left ones are mirrored. Each is [shaft, head].
const RIGHT = {
  straight: ["M24 42 V9", "M13 19 L24 8 L35 19"],
  right: ["M17 42 V25 Q17 16 26 16 H39", "M31 8 L39 16 L31 24"],
  "slight-right": ["M19 42 V29 L35 13", "M25 12 H36 V23"],
  "sharp-right": ["M16 42 V14 L34 32", "M34 21 V33 H22"],
  uturn: ["M33 42 V20 Q33 8 23 8 Q13 8 13 20 V33", "M6 26 L13 34 L20 26"],
  arrive: ["M15 43 V7", "M15 8 L35 14 L15 21 Z"],
};

export function TurnIcon({ arrow, size = 48, color = "#fff" }) {
  if (!arrow) return null;
  const left = arrow.endsWith("left");
  const key = left ? arrow.replace("left", "right") : arrow;
  const [shaft, head] = RIGHT[key] ?? RIGHT.straight;
  return (
    <Svg width={size} height={size} viewBox="0 0 48 48">
      <G transform={left ? "translate(48 0) scale(-1 1)" : undefined} stroke={color} strokeWidth={5}
        strokeLinecap="round" strokeLinejoin="round" fill="none">
        <Path d={shaft} />
        <Path d={head} fill={arrow === "arrive" ? color : "none"} />
      </G>
    </Svg>
  );
}

export const MicIcon = ({ size = 22, color = "#fff" }) => (
  <Svg width={size} height={size} viewBox="0 0 24 24">
    <Rect x={8} y={2} width={8} height={13} rx={4} fill={color} />
    <Path d="M5 11 a7 7 0 0 0 14 0 M12 18 V22 M8 22 H16" stroke={color} strokeWidth={2} strokeLinecap="round" fill="none" />
  </Svg>
);

// Follow mode shows the navigation arrow; north-up shows a compass needle.
export const CompassIcon = ({ follow, size = 24 }) => (
  <Svg width={size} height={size} viewBox="0 0 24 24">
    {follow ? <Path d="M12 2 L20 21 L12 16 L4 21 Z" fill="#1a73e8" />
      : <G><Path d="M12 2 L16 12 H8 Z" fill="#d93025" /><Path d="M12 22 L8 12 H16 Z" fill="#9aa0a6" /></G>}
  </Svg>
);

export const SpeakerIcon = ({ muted, size = 24, color = "#3c4043" }) => (
  <Svg width={size} height={size} viewBox="0 0 24 24">
    <Path d="M3 9 H7 L12 5 V19 L7 15 H3 Z" fill={color} />
    {muted ? <Path d="M16 9 L22 15 M22 9 L16 15" stroke="#d93025" strokeWidth={2} strokeLinecap="round" />
      : <Path d="M15.5 8.5 a5 5 0 0 1 0 7 M18 6 a8.5 8.5 0 0 1 0 12" stroke={color} strokeWidth={2} strokeLinecap="round" fill="none" />}
  </Svg>
);

export const BugIcon = ({ size = 24, color = "#3c4043" }) => (
  <Svg width={size} height={size} viewBox="0 0 24 24">
    <Path d="M4 9 L8 11 M20 9 L16 11 M3 14 H8 M21 14 H16 M4 20 L8 17 M20 20 L16 17 M9 5 L10 7 M15 5 L14 7"
      stroke={color} strokeWidth={1.8} strokeLinecap="round" />
    <Circle cx={12} cy={9} r={3} fill={color} />
    <Rect x={8} y={10} width={8} height={11} rx={4} fill={color} />
  </Svg>
);
