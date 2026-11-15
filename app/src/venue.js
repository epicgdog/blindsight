// HackPrinceton venue on the phone: URLs, which level to show, coordinates between the plan
// images and the map frame, and banner text from a room route. Pure, tested in node.
import { serverBase } from "./voice.js";

export const venueInfoUrl = (wsUrl) => { const b = serverBase(wsUrl); return b ? `${b}/api/venue` : null; };
export const venueSearchUrl = (wsUrl, q) => {
  const b = serverBase(wsUrl);
  return b && q ? `${b}/api/venue/search?q=${encodeURIComponent(q)}` : null;
};
export const venueRouteUrl = (wsUrl, to) => {
  const b = serverBase(wsUrl);
  return b && to ? `${b}/api/venue/route?to=${encodeURIComponent(to)}` : null;
};
export const venueDestUrl = (wsUrl) => { const b = serverBase(wsUrl); return b ? `${b}/api/agent/venue` : null; };
export const imageUrl = (wsUrl, path) => { const b = serverBase(wsUrl); return b && path ? `${b}${path}` : null; };

// Level on screen: the one picked by hand, else where the person is, else where the route
// starts, else the ground floor.
export function shownLevel({ override, you, route }) {
  if (override) return override;
  if (you?.level) return you.level;
  if (route?.ok && route.legs?.length) return route.legs[0].level;
  return "1";
}

// Plan image pixels <-> map metres for a level's align {scale, rot, tx, ty} (image y grows down).
export function mapToPx(align, x, y) {
  const r = ((align.rot ?? 0) * Math.PI) / 180, c = Math.cos(r), s = Math.sin(r);
  return [align.scale * (c * x - s * y) + align.tx, -align.scale * (s * x + c * y) + align.ty];
}
export function pxToMap(align, u, v) {
  const r = ((align.rot ?? 0) * Math.PI) / 180, c = Math.cos(r), s = Math.sin(r);
  const a = (u - align.tx) / align.scale, b = -(v - align.ty) / align.scale;
  return [c * a + s * b, -s * a + c * b];
}

// An image w x h drawn "contain" (SVG meet) in a box: scale and offset, and a touch back to pixels.
export function fitBox(box, w, h) {
  const scale = Math.min(box.width / w, box.height / h);
  return { scale, offX: (box.width - w * scale) / 2, offY: (box.height - h * scale) / 2 };
}
export function touchToPx(box, w, h, lx, ly) {
  const f = fitBox(box, w, h);
  if (!(f.scale > 0)) return null;
  const u = (lx - f.offX) / f.scale, v = (ly - f.offY) / f.scale;
  return u < 0 || v < 0 || u > w || v > h ? null : [u, v];
}

// Banner arrow for a spoken step.
export function stepArrow(step) {
  const t = String(step || "").toLowerCase();
  if (t.startsWith("turn around")) return "uturn";
  const side = t.includes("turn right") || t.includes("slightly right") ? "right" : t.includes("turn left") || t.includes("slightly left") ? "left" : null;
  if (side) return t.includes("slightly") ? `slight-${side}` : side;
  if (t.includes(" down to ") || t.includes(" up to ")) return "straight";
  if (t.includes("door on your") || t.startsWith("you are at") || t.endsWith("is straight ahead.")) return "arrive";
  return "straight";
}

// Banner while guiding to a room: the next step, and where the room is.
export function venueBanner(route) {
  if (!route?.ok) return null;
  const step = route.steps?.[0] ?? "";
  return { title: step, meters: `${route.to.id} · ${route.to.level_name}`, arrow: stepArrow(step), tone: "go" };
}

// Where a route changes level on this level's plan: the last point of a leg (going) or the
// first point of the next leg (arriving), with the direction for the marker.
export function transferMarks(route, level) {
  if (!route?.ok) return [];
  const out = [];
  route.legs.forEach((leg, i) => {
    const t = route.transfers?.[i];
    if (t && leg.level === level) out.push({ px: leg.px[leg.px.length - 1], kind: t.kind, to: t.to_level, going: true });
    const prev = route.transfers?.[i - 1];
    if (prev && leg.level === level) out.push({ px: leg.px[0], kind: prev.kind, to: prev.from_level, going: false });
  });
  return out;
}

export const distanceText = (m) => (Number.isFinite(m) ? `${Math.max(1, Math.round(m))} m` : "");
