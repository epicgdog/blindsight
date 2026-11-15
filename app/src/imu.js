// The phone sensor sample sent about 10 times a second between camera frames (JSON text on the same
// socket). The server uses the compass, walking flag and step count to keep heading and guidance
// current. Unknown values are null: the server drops just those fields.

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export function imuMessage({ now, heading, attitude, steps }) {
  const att = attitude ?? {};
  return {
    type: "imu",
    t_ms: isNum(now) ? now : null,
    heading_deg: isNum(heading) && heading >= 0 && heading < 360 ? heading : null,
    pitch_deg: isNum(att.pitch_deg) ? att.pitch_deg : null,
    roll_deg: isNum(att.roll_deg) ? att.roll_deg : null,
    moving: typeof att.moving === "boolean" ? att.moving : null,
    steps: Number.isInteger(steps) ? steps : null,
  };
}
