// 3D scan-point map. Local three.js only (no CDN): the viewer must work with venue Wi-Fi down.
// Matterport frame is x, y on the floor and z up; three.js is y-up, so (x, y, z) -> (x, z, -y).
// North (larger y) therefore points away from the camera at the default azimuth, like the 2D map.
import * as THREE from "/viewer/vendor/three.module.min.js";

const COLORS = { point: 0x8a93a8, trail: 0x3b82f6, candidate: 0xf5a524, estimate: 0xf5a524, active: 0x17c964 };
const RADII = { point: 0.14, trail: 0.18, candidate: 0.2, estimate: 0.28, active: 0.32 };
const toThree = (x, y, z) => new THREE.Vector3(x, z, -y);

export function createMap3d(host) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    return null; // no WebGL: caller keeps the 2D map
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const canvas = renderer.domElement;
  canvas.tabIndex = 0;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "3D view of the scan points. Drag to rotate, scroll to zoom, arrow keys also rotate.");
  host.append(canvas);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 500);
  const target = new THREE.Vector3(), goal = new THREE.Vector3();
  const view = { theta: 0.6, phi: 1.0, radius: 20, goalRadius: 20 }; // phi from straight up
  const dotGeo = new THREE.SphereGeometry(1, 20, 14);
  const dots = new Map();
  const staticGroup = new THREE.Group();
  scene.add(staticGroup);
  let staticKey = "", focusId = null, dirty = true, running = false;

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.5, 0.62, 40).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: COLORS.active, transparent: true, opacity: 0.8, side: THREE.DoubleSide }),
  );
  const beam = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 1, 0)]),
    new THREE.LineBasicMaterial({ color: COLORS.active }),
  );
  ring.visible = beam.visible = false;
  scene.add(ring, beam);

  function place() {
    const s = Math.sin(view.phi);
    camera.position.set(
      target.x + view.radius * s * Math.sin(view.theta),
      target.y + view.radius * Math.cos(view.phi),
      target.z + view.radius * s * Math.cos(view.theta),
    );
    camera.lookAt(target);
  }

  function frame() {
    running = false;
    target.lerp(goal, 0.15);
    view.radius += (view.goalRadius - view.radius) * 0.15;
    place();
    renderer.render(scene, camera);
    const settling = target.distanceTo(goal) > 0.01 || Math.abs(view.goalRadius - view.radius) > 0.01;
    if (settling || dirty) { dirty = false; schedule(); }
  }
  function schedule() {
    if (!running) { running = true; requestAnimationFrame(frame); }
  }
  const redraw = () => { dirty = true; schedule(); };

  function resize() {
    const w = host.clientWidth || 400, h = host.clientHeight || 400;
    renderer.setSize(w, h, false);
    canvas.style.width = "100%"; canvas.style.height = "100%";
    camera.aspect = w / h; camera.updateProjectionMatrix();
    redraw();
  }
  new ResizeObserver(resize).observe(host);

  // ---- orbit by pointer, wheel and keys ----
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointerup", () => { drag = null; });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    view.theta -= (e.clientX - drag.x) * 0.008;
    view.phi = Math.min(1.5, Math.max(0.15, view.phi - (e.clientY - drag.y) * 0.008));
    drag = { x: e.clientX, y: e.clientY };
    redraw();
  });
  const zoom = (f) => { view.goalRadius = Math.min(120, Math.max(3, view.goalRadius * f)); redraw(); };
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoom(e.deltaY > 0 ? 1.1 : 0.9); }, { passive: false });
  canvas.addEventListener("keydown", (e) => {
    const k = { ArrowLeft: () => (view.theta -= 0.1), ArrowRight: () => (view.theta += 0.1),
      ArrowUp: () => (view.phi = Math.max(0.15, view.phi - 0.08)), ArrowDown: () => (view.phi = Math.min(1.5, view.phi + 0.08)),
      "+": () => zoom(0.9), "=": () => zoom(0.9), "-": () => zoom(1.1) }[e.key];
    if (k) { e.preventDefault(); k(); redraw(); }
  });

  // ---- static geometry: walls and floor outlines, rebuilt only when the data changes ----
  function buildStatic(s) {
    for (const o of [...staticGroup.children]) { o.geometry?.dispose(); o.material?.dispose(); staticGroup.remove(o); }
    const lo = s.ground_z, hi = s.ground_z + s.wall_height;
    if (s.walls.length) {
      const pos = [], idx = [], edge = [];
      s.walls.forEach(([ax, ay, bx, by], i) => {
        const a0 = toThree(ax, ay, lo), b0 = toThree(bx, by, lo), b1 = toThree(bx, by, hi), a1 = toThree(ax, ay, hi);
        pos.push(...a0.toArray(), ...b0.toArray(), ...b1.toArray(), ...a1.toArray());
        idx.push(4 * i, 4 * i + 1, 4 * i + 2, 4 * i, 4 * i + 2, 4 * i + 3);
        edge.push(...a0.toArray(), ...b0.toArray(), ...a1.toArray(), ...b1.toArray());
      });
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setIndex(idx);
      staticGroup.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({
        color: 0x6b7694, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false })));
      const eg = new THREE.BufferGeometry();
      eg.setAttribute("position", new THREE.Float32BufferAttribute(edge, 3));
      staticGroup.add(new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: 0xaab4cc, transparent: true, opacity: 0.8 })));
    }
    // one translucent slab per floor, sized to that floor's scan points
    for (const f of s.floors) {
      const pts = s.points.filter((p) => p.floor === f.floor);
      if (!pts.length) continue;
      const m = 2, x0 = Math.min(...pts.map((p) => p.x)) - m, x1 = Math.max(...pts.map((p) => p.x)) + m;
      const y0 = Math.min(...pts.map((p) => p.y)) - m, y1 = Math.max(...pts.map((p) => p.y)) + m;
      const slab = new THREE.Mesh(
        new THREE.PlaneGeometry(x1 - x0, y1 - y0).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: 0x2c3347, transparent: true, opacity: f.floor === 0 ? 0.55 : 0.28, side: THREE.DoubleSide, depthWrite: false }));
      slab.position.copy(toThree((x0 + x1) / 2, (y0 + y1) / 2, f.base_z));
      staticGroup.add(slab);
    }
  }

  // Text labels (e.g. "Entrance") as camera-facing sprites above a point, drawn on a canvas.
  const labels = new Map();
  function setLabel(p) {
    let sp = labels.get(p.id);
    if (!p.label) { if (sp) { scene.remove(sp); sp.material.map.dispose(); sp.material.dispose(); labels.delete(p.id); } return; }
    if (!sp) {
      const c = document.createElement("canvas");
      c.width = 256; c.height = 64;
      const g = c.getContext("2d");
      g.font = "600 34px system-ui, sans-serif";
      g.textAlign = "center"; g.textBaseline = "middle";
      const w = g.measureText(p.label).width + 28;
      g.fillStyle = "rgba(20,24,36,0.85)";
      g.beginPath(); g.roundRect((256 - w) / 2, 8, w, 48, 12); g.fill();
      g.fillStyle = "#ffffff"; g.fillText(p.label, 128, 33);
      sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false, transparent: true }));
      sp.scale.set(2.4, 0.6, 1);
      sp.renderOrder = 10;
      scene.add(sp); labels.set(p.id, sp);
    }
    sp.position.copy(toThree(p.x, p.y, p.z)).add(new THREE.Vector3(0, 0.7, 0));
  }

  function update(s) {
    if (!s) return;
    const key = `${s.points.length}|${s.walls.length}|${s.ground_z}|${s.floors.length}`;
    if (key !== staticKey) {
      staticKey = key;
      buildStatic(s);
      const xs = [...s.points.map((p) => p.x), ...s.walls.flatMap((w) => [w[0], w[2]])];
      const ys = [...s.points.map((p) => p.y), ...s.walls.flatMap((w) => [w[1], w[3]])];
      const extent = Math.max(8, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
      view.goalRadius = view.radius = extent * 1.5;
      goal.copy(toThree(s.center.x, s.center.y, s.center.z)); target.copy(goal);
    }
    const seen = new Set();
    for (const p of s.points) {
      seen.add(p.id);
      let m = dots.get(p.id);
      if (!m) {
        m = new THREE.Mesh(dotGeo, new THREE.MeshBasicMaterial());
        scene.add(m); dots.set(p.id, m);
      }
      m.material.color.setHex(COLORS[p.kind] ?? COLORS.point);
      m.material.opacity = p.kind === "estimate" ? 0.65 : 1;
      m.material.transparent = p.kind === "estimate";
      m.scale.setScalar(RADII[p.kind] ?? RADII.point);
      m.position.copy(toThree(p.x, p.y, p.z));
      setLabel(p);
    }
    for (const [id, m] of dots) if (!seen.has(id)) { scene.remove(m); m.material.dispose(); dots.delete(id); }
    for (const [id, sp] of labels) if (!seen.has(id)) { scene.remove(sp); sp.material.map.dispose(); sp.material.dispose(); labels.delete(id); }

    const hot = s.points.find((p) => p.kind === "active") ?? s.points.find((p) => p.kind === "estimate");
    ring.visible = beam.visible = !!hot;
    if (hot) {
      const top = toThree(hot.x, hot.y, hot.z);
      ring.position.set(top.x, s.floors.find((f) => f.floor === hot.floor)?.base_z ?? s.ground_z, top.z);
      ring.position.y += 0.02;
      beam.position.copy(ring.position);
      beam.scale.set(1, Math.max(0.1, top.y - ring.position.y), 1);
      const c = hot.kind === "active" ? COLORS.active : COLORS.estimate;
      ring.material.color.setHex(c); beam.material.color.setHex(c);
      if (hot.id !== focusId) { focusId = hot.id; goal.copy(top); goal.y = s.ground_z + 1; }
    }
    host.dataset.points = String(s.points.length);
    host.dataset.active = hot?.kind === "active" ? hot.id : "";
    redraw();
  }

  return { update, redraw };
}
