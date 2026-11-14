// Wire format 5.1: u32 BE N | N bytes UTF-8 JSON header | JPEG bytes.
const REQUIRED = ["seq", "ts_ms", "heading_deg", "w", "h"];

export function encodeFrame(header, jpeg) {
  for (const k of REQUIRED) {
    if (!(k in header)) throw new Error(`invalid header: missing ${k}`);
  }
  const h = new TextEncoder().encode(JSON.stringify(header));
  const out = new Uint8Array(4 + h.length + jpeg.length);
  new DataView(out.buffer).setUint32(0, h.length, false);
  out.set(h, 4);
  out.set(jpeg, 4 + h.length);
  return out;
}

export function decodeFrame(buf) {
  if (buf.length < 4) throw new Error("truncated frame");
  const n = new DataView(buf.buffer, buf.byteOffset, buf.byteLength).getUint32(0, false);
  if (buf.length < 4 + n) throw new Error("truncated frame");
  const header = JSON.parse(new TextDecoder().decode(buf.subarray(4, 4 + n)));
  return { header, jpeg: buf.subarray(4 + n) };
}
