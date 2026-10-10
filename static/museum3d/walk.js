/* The museum walk's renderer: WebGL through three.js.

   app.js still plans the building — which rooms, how big, which walls carry
   doors, where each painting hangs — and still owns the walking: keys, drag,
   glides, collisions, stairs, the map. This module turns that plan into a
   lit building and draws it from wherever app.js says the visitor stands.

   Rooms are made on demand — the one you're in first, its neighbours next,
   the rest of the storey after — by a Web Worker (bakeworker.js), which
   builds each room's architecture and bakes its light off the main thread;
   here the result only becomes buffers, materials and meshes, and the room
   is photographed from its middle into a reflection probe. Paintings'
   pictures are loaded only for rooms near the visitor and let go again
   further off, so a long museum costs a phone no more than a short one.

   The API app.js uses:
     createWalk(host, {touch})       → walk
     walk.build(plan)                 rooms, works, markers, the stair
     walk.ready(fn)                   fn(ok) once the room you stand in is lit (or failed)
     walk.setCamera(x, z, yaw, zoom, lift, pitch)   then walk.render()
     walk.setView(floor, roomIndex)   which storey, which room you're in
     walk.prepare(floor, roomIndex)   where a climb is going (null when it's over)
     walk.pick(clientX, clientY)      what a tap landed on
     walk.upgrade(workId, url)        swap a painting up to its full image
     walk.resize(), walk.dispose() */

import * as THREE from "../vendor/three/three.module.min.js";
import { makeTextures, exitSignTexture, placardTexture, markerTexture, neutralEnvironment, FLOOR_TILE } from "./textures.js";
import { makeProbe, setProbeEnv, roomMaterials, canvasMaterial, placardMaterial } from "./materials.js";
import { geometryFrom } from "./geo.js";

const EYE = 158;
const FOV = 58;              // vertical field of view at zoom 1, degrees
const LBL_W = 22, LBL_H = 14;

export function createWalk(host, opts) {
  const touch = !!(opts && opts.touch);
  const renderer = new THREE.WebGLRenderer({
    antialias: true, powerPreference: "high-performance", alpha: false,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, touch ? 1.5 : 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.domElement.className = "mu-gl";
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d0b08);
  const camera = new THREE.PerspectiveCamera(FOV, 1, 4, 30000);
  camera.rotation.order = "YXZ";

  const tex = makeTextures(renderer, touch);
  const neutral = neutralEnvironment(renderer);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const loader = new THREE.TextureLoader();
  let disposed = false;
  let rafPending = 0;

  const placeholder = new THREE.DataTexture(new Uint8Array([42, 38, 34, 255]), 1, 1);
  placeholder.colorSpace = THREE.SRGBColorSpace;
  placeholder.needsUpdate = true;
  // the photographed gilt frames, gold until they arrive
  const goldPx = new THREE.DataTexture(new Uint8Array([176, 138, 70, 255]), 1, 1);
  goldPx.colorSpace = THREE.SRGBColorSpace;
  goldPx.needsUpdate = true;
  const frameTex = [goldPx, goldPx];
  const frameMats = [];
  const loadShared = (url, fn) => loader.load(url, (t) => {
    if (disposed) { t.dispose(); return; }
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 8;
    fn(t);
    requestRender();
  });
  ["/static/img/frame1.png", "/static/img/frame2.png"].forEach((url, k) => loadShared(url, (t) => {
    frameTex[k] = t;
    frameMats.forEach((m) => { if (m.userData.frame === k) m.map = t; });
  }));
  const frontDoorMat = new THREE.MeshBasicMaterial({ color: 0xb4b0aa });
  loadShared("/static/img/entry-door.jpg", (t) => { frontDoorMat.map = t; frontDoorMat.needsUpdate = true; });
  const pickMat = new THREE.MeshBasicMaterial({ visible: false });
  const exitMat = new THREE.MeshBasicMaterial({ map: exitSignTexture(0), color: new THREE.Color(1.6, 1.6, 1.6) });
  const markerMat = new THREE.MeshBasicMaterial({ map: markerTexture(), transparent: true, depthWrite: false,
                                                  toneMapped: false, opacity: 0.85 });

  let plan = null;
  const rooms = [];
  const markers = [];
  let view = { floor: 0, ri: 0 };
  let pre = null;
  const RANGE = touch ? 1 : 3;           // rooms either side drawn
  const RESIDENT = RANGE + 1;            // rooms either side whose pictures stay loaded
  let readyFns = [];

  /* -------------------------------------------------------------- render */
  function render() {
    if (disposed) return;
    rafPending = 0;
    renderer.render(scene, camera);
  }
  function requestRender() {
    if (!rafPending && !disposed) rafPending = requestAnimationFrame(render);
  }

  function resize() {
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = w + "px";
    renderer.domElement.style.height = h + "px";
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    requestRender();
  }

  function setCamera(x, z, yaw, zoom, lift, pitch) {
    camera.position.set(x, EYE + (lift || 0), z);
    camera.rotation.set(pitch || 0, -yaw, 0);
    const f = 2 * Math.atan(Math.tan((FOV * Math.PI) / 360) / (zoom || 1)) * 180 / Math.PI;
    if (Math.abs(camera.fov - f) > 1e-4) {
      camera.fov = f;
      camera.updateProjectionMatrix();
    }
  }

  /* ---------------------------------------------------------------- plan */
  function build(p) {
    plan = p;
    rooms.length = 0;
    p.rooms.forEach((r, i) => {
      rooms.push({ i, desc: r, built: false, pending: false, group: null, probe: null, arts: [],
                   halls: [], resident: false, needProbe: false, probedWith: 0, lamps: [] });
      r.arts = [];
    });
    // works go to their rooms
    p.arts.forEach((a) => { p.rooms[a.ri].arts.push(a); });
    // the rooms a stair joins are made early, so a climb never arrives at
    // nothing
    const ends = {};
    p.rooms.forEach((r, i) => r.doors.forEach((d) => {
      if (d.pi != null) (ends[d.pi] = ends[d.pi] || []).push(i);
    }));
    rooms.forEach((rm) => {
      rm.partners = [];
      rm.desc.doors.forEach((d) => {
        if (d.pi != null) (ends[d.pi] || []).forEach((j) => { if (j !== rm.i) rm.partners.push(j); });
      });
    });
    p.targets.forEach((t) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), markerMat);
      m.rotation.order = "YXZ";
      m.rotation.set(-Math.PI / 2, Math.atan2(-t.u[0], -t.u[1]), 0);
      m.position.set(t.x, 0.6, t.z);
      m.userData.pick = { kind: "target", to: t.to };
      m.userData.ri = t.ri;
      m.renderOrder = 2;
      m.visible = false;
      scene.add(m);
      markers.push(m);
    });
  }

  /* ------------------------------------------------------------ the baker

     A module worker where the browser offers one; otherwise the same code
     runs here, on the page. One room at a time, the most wanted first. */
  let worker = null;
  let busy = null;
  let jobId = 0;
  const jobs = new Map();
  let local = null;
  try {
    worker = new Worker(new URL("./bakeworker.js", import.meta.url), { type: "module" });
    worker.onmessage = (e) => {
      const rm = jobs.get(e.data.id);
      jobs.delete(e.data.id);
      busy = null;
      if (disposed) return;
      if (e.data.error) {
        console.error("museum: room", rm.i, "failed:", e.data.error);
        rm.failed = true;
        rm.pending = false;
        applyView();
      } else assemble(rm, e.data.piece);
      pump();
    };
    worker.onerror = (err) => {
      // no module workers here after all: bake on the page instead
      console.warn("museum: baking on the main thread", err && err.message);
      if (worker) worker.terminate();
      worker = null;
      if (busy) { busy.pending = false; busy = null; }
      jobs.clear();
      pump();
    };
  } catch (err) {
    worker = null;
  }

  function request(rm) {
    rm.pending = true;
    busy = rm;
    const msg = { room: rm.desc, opts: { floorTile: FLOOR_TILE, texel: touch ? 6 : 5 }, stair: plan.stair };
    if (worker) {
      const id = ++jobId;
      jobs.set(id, rm);
      worker.postMessage(Object.assign({ id }, msg));
      return;
    }
    const run = (m) => {
      if (disposed) return;
      busy = null;
      try { assemble(rm, m.computeRoom(msg.room, msg.opts, msg.stair)); }
      catch (err) {
        console.error("museum: room", rm.i, "failed:", err);
        rm.failed = true;
        rm.pending = false;
        applyView();
      }
      pump();
    };
    if (local) setTimeout(() => run(local), 0);
    else import("./compute.js").then((m) => { local = m; run(m); }, (err) => {
      // no baker at all: every room fails in turn, and ready() says so
      console.error("museum: the baker didn't load:", err);
      busy = null;
      if (disposed) return;
      rm.failed = true;
      rm.pending = false;
      applyView();
      pump();
    });
  }

  /* Pick the next piece of work: the most wanted room still unbuilt, or a
     probe to retake now that a neighbour has arrived. */
  function pump() {
    if (disposed || !plan || busy) return;
    const next = rooms.filter((r) => !r.built && !r.pending && !r.failed)
      .sort((a, b) => score(a) - score(b))[0];
    if (next) { request(next); return; }
    // no probe retakes while a climb is under way: each would be a hitch
    if (pre) return;
    const stale = rooms.find((r) => r.built && (r.needProbe || r.probedWith < neighboursBuilt(r)) &&
      r.desc.floor === view.floor && Math.abs(r.i - view.ri) <= RANGE + 1);
    if (stale) {
      capture(stale);
      requestRender();
      setTimeout(pump, 30);
    }
  }
  function score(r) {
    const cur = rooms[view.ri];
    if (cur && cur.partners && cur.partners.includes(r.i)) return 1.5;
    return (r.desc.floor === view.floor ? 0 : 1000) + Math.abs(r.i - view.ri);
  }

  /* A light map from the baker's half floats, read through uv1. */
  function lightMapTexture(lm) {
    const t = new THREE.DataTexture(lm.half, lm.W, lm.H, THREE.RGBAFormat, THREE.HalfFloatType);
    t.magFilter = THREE.LinearFilter;
    t.minFilter = THREE.LinearFilter;
    t.generateMipmaps = false;
    t.channel = 1;
    t.needsUpdate = true;
    return t;
  }

  /* Meshes for a baked piece: surfaces per material, borrowed-light trim,
     probe-lit fittings, the invisible faces taps land on. */
  function addMeshes(group, piece, M) {
    Object.keys(piece.baked).forEach((k) => {
      if (M[k]) group.add(new THREE.Mesh(geometryFrom(piece.baked[k]), M[k]));
    });
    Object.keys(piece.plain).forEach((k) => {
      if (M[k]) group.add(new THREE.Mesh(geometryFrom(piece.plain[k]), M[k]));
    });
    piece.picks.forEach((pk) => {
      const mesh = new THREE.Mesh(geometryFrom(pk.g), pickMat);
      mesh.userData.pick = pk.pick;
      group.add(mesh);
    });
  }

  function extras(M) {
    M.frontDoor = frontDoorMat;
    M.pick = pickMat;
    M.frame0.userData.frame = 0;
    M.frame1.userData.frame = 1;
    frameMats.push(M.frame0, M.frame1);
    return M;
  }

  /* A room's baked arrays become a lit room in the scene. */
  function assemble(rm, piece) {
    const lm = lightMapTexture(piece.lm);
    const probe = makeProbe(neutral);
    const b = piece.box;
    probe.uMin.value.set(b.x0, 0, b.z0);
    probe.uMax.value.set(b.x1, b.top, b.z1);
    probe.uPos.value.set((b.x0 + b.x1) / 2, EYE, (b.z0 + b.z1) / 2);
    const M = extras(roomMaterials(tex, lm, probe, piece.wallRGB, frameTex));
    const group = new THREE.Group();
    group.name = "room" + rm.i;
    addMeshes(group, piece, M);
    // stair halls behind their doors, each with its own light map; a hall
    // shows only from its own room, or while it's being climbed
    rm.halls = piece.halls.map((hp) => {
      const hlm = lightMapTexture(hp.lm);
      const HM = extras(roomMaterials(tex, hlm, probe, null, frameTex));
      const hg = new THREE.Group();
      hg.name = "hall";
      addMeshes(hg, hp, HM);
      group.add(hg);
      return { group: hg, door: hp.door, lm: hlm, box: hp.box };
    });
    // exit signs
    piece.signs.forEach((s) => {
      const g = new THREE.Group();
      g.add(new THREE.Mesh(new THREE.BoxGeometry(34, 17, 2.2), M.metalWhite));
      const face = new THREE.Mesh(new THREE.PlaneGeometry(31, 15.5), exitMat);
      face.position.z = 1.15;
      g.add(face);
      g.position.set(s.p[0], s.p[1], s.p[2]);
      g.rotation.y = Math.atan2(s.n[0], s.n[2]);
      group.add(g);
    });
    // the paintings: canvas under the room's light, the gilt frame, the placard
    piece.arts.forEach((pa) => {
      const art = pa.art;
      const cmat = canvasMaterial(placeholder, lm, probe);
      const canvas = new THREE.Mesh(geometryFrom(pa.canvas), cmat);
      canvas.userData.pick = { kind: "art", id: art.id };
      group.add(canvas);
      const frame = new THREE.Mesh(geometryFrom(pa.frame), (art.frame || 0) % 2 ? M.frame1 : M.frame0);
      frame.userData.pick = { kind: "art", id: art.id };
      group.add(frame);
      const entry = { art, canvas, cmat, frame, label: null, loaded: false, hi: false, full: false };
      if (art.label) {
        const pl = new THREE.Group();
        pl.add(new THREE.Mesh(new THREE.BoxGeometry(LBL_W, LBL_H, 0.4), M.placardBack));
        pl.position.set(art.lx, art.ly, art.lz);
        pl.rotation.y = Math.atan2(art.nx, art.nz);
        group.add(pl);
        entry.label = { group: pl, mat: null, mesh: null };
      }
      rm.arts.push(entry);
    });
    rm.group = group;
    rm.probe = probe;
    rm.lm = lm;
    rm.lamps = piece.lamps;
    rm.extents = piece.extents || [];
    rm.built = true;
    rm.pending = false;
    rm.needProbe = true;
    group.visible = false;
    scene.add(group);
    applyView();
  }

  /* ---------------------------------------------------- pictures in & out */
  function loadArt(rm, e, url, hi) {
    loader.load(url, (t) => {
      if (!rm.resident || disposed) { t.dispose(); if (!hi) e.loaded = false; return; }
      // a thumbnail that arrives after the full image stays out of its way
      if (!hi && e.full) { t.dispose(); return; }
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = renderer.capabilities.getMaxAnisotropy();
      if (e.cmat.map && e.cmat.map !== placeholder) e.cmat.map.dispose();
      e.cmat.map = t;
      if (hi) e.full = true;
      requestRender();
    }, undefined, () => { if (hi) e.hi = false; else e.loaded = false; });
  }
  function makeResident(rm, on) {
    if (rm.resident === on) return;
    rm.resident = on;
    rm.arts.forEach((e) => {
      if (on) {
        if (!e.loaded) { e.loaded = true; loadArt(rm, e, e.art.src, false); }
        if (e.label && !e.label.mesh) {
          const m = placardMaterial(placardTexture(e.art.label, LBL_W, LBL_H), rm.probe);
          const mesh = new THREE.Mesh(new THREE.PlaneGeometry(LBL_W - 0.1, LBL_H - 0.1), m);
          mesh.position.z = 0.21;
          e.label.group.add(mesh);
          e.label.mat = m;
          e.label.mesh = mesh;
        }
      } else {
        if (e.cmat.map && e.cmat.map !== placeholder) {
          e.cmat.map.dispose();
          e.cmat.map = placeholder;
        }
        e.loaded = false;
        e.hi = false;
        e.full = false;
        if (e.label && e.label.mesh) {
          e.label.group.remove(e.label.mesh);
          e.label.mesh.geometry.dispose();
          e.label.mat.map.dispose();
          e.label.mat.dispose();
          e.label.mesh = null;
        }
      }
    });
  }

  /* -------------------------------------------------------------- probes */
  const cubeRT = new THREE.WebGLCubeRenderTarget(touch ? 128 : 256, {
    type: THREE.HalfFloatType, generateMipmaps: true, minFilter: THREE.LinearMipmapLinearFilter });
  const cubeCam = new THREE.CubeCamera(4, 30000, cubeRT);
  /* Each lamp's lens is a few centimetres across — too small for a probe to
     see — so while a probe is captured every lamp in view stands in as a
     bright ball. Gilt and varnish then carry the lamps' highlights where
     a visitor expects them. */
  const lampGeo = new THREE.SphereGeometry(9, 12, 8);
  const lampMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(26, 24, 21.5) });
  function neighboursBuilt(rm) {
    return rooms.filter((r) => r.built && r.desc.floor === rm.desc.floor && Math.abs(r.i - rm.i) <= 1).length;
  }
  function capture(rm) {
    if (!rm.built) return;
    const balls = new THREE.Group();
    const vis = rooms.map((r) => r.group && r.group.visible);
    rooms.forEach((r) => {
      const near = r.built && r.desc.floor === rm.desc.floor && Math.abs(r.i - rm.i) <= 1;
      if (r.group) r.group.visible = near;
      if (near) r.lamps.forEach((p) => {
        const b = new THREE.Mesh(lampGeo, lampMat);
        b.position.set(p[0], p[1], p[2]);
        balls.add(b);
      });
    });
    scene.add(balls);
    markers.forEach((m) => { m.userData.was = m.visible; m.visible = false; });
    cubeCam.position.copy(rm.probe.uPos.value);
    cubeCam.update(renderer, scene);
    scene.remove(balls);
    const rt = pmrem.fromCubemap(cubeRT.texture);
    if (rm.probeRT) rm.probeRT.dispose();
    rm.probeRT = rt;
    setProbeEnv(rm.probe, rt.texture);
    rooms.forEach((r, k) => { if (r.group) r.group.visible = vis[k]; });
    markers.forEach((m) => { m.visible = m.userData.was; });
    rm.needProbe = false;
    rm.probedWith = neighboursBuilt(rm);
  }

  /* ---------------------------------------------------------------- view */
  function setView(floor, ri) {
    view = { floor, ri };
    applyView();
    pump();
  }
  /* The ground a room stands on: its own box, and once it's built, the
     corridors running on beyond its walls. */
  function footprint(r) {
    const d = r.desc;
    return [{ x0: d.cx - d.W / 2, x1: d.cx + d.W / 2, z0: d.cz - d.D / 2, z1: d.cz + d.D / 2 }]
      .concat(r.extents || []);
  }
  const overlaps = (a, b) =>
    Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0) > 10 && Math.min(a.z1, b.z1) - Math.max(a.z0, b.z0) > 10;

  function applyView() {
    // Nearest first, and a room is drawn only where nothing drawn already
    // stands — the room you're in and its stair halls above all. A plan
    // that couldn't keep two rooms apart then shows each where it belongs
    // instead of both at once, one standing inside the other.
    const taken = [];
    const cur = rooms[view.ri];
    if (cur) cur.halls.forEach((h) => { if (h.box) taken.push(h.box); });
    rooms.slice().sort((a, b) => Math.abs(a.i - view.ri) - Math.abs(b.i - view.ri)).forEach((r) => {
      let near = r.desc.floor === view.floor && Math.abs(r.i - view.ri) <= RANGE;
      if (near) {
        const fp = footprint(r);
        if (r.i !== view.ri && fp.some((b) => taken.some((t) => overlaps(b, t)))) near = false;
        else taken.push(...fp);
      }
      if (r.group) r.group.visible = near;
      // a stair hall could reach into a neighbour's space: it shows only
      // from its own room
      r.halls.forEach((h) => { h.group.visible = r.i === view.ri; });
      if (r.built) {
        const resident = (r.desc.floor === view.floor && Math.abs(r.i - view.ri) <= RESIDENT) ||
          (pre != null && r.desc.floor === pre.floor && Math.abs(r.i - pre.ri) <= 1);
        makeResident(r, resident);
        // a probe is a few megabytes of the GPU's memory: a room out of
        // reach lets its go, and takes a fresh one when it's back
        if (!resident && r.probeRT) {
          setProbeEnv(r.probe, neutral);
          r.probeRT.dispose();
          r.probeRT = null;
          r.needProbe = true;
        }
      }
    });
    markers.forEach((m) => { m.visible = m.userData.ri === view.ri; });
    if (cur && (cur.built || cur.failed)) {
      if (cur.built && cur.needProbe) capture(cur);
      if (readyFns.length) {
        const fns = readyFns;
        readyFns = [];
        if (cur.built) render();
        fns.forEach((fn) => fn(!!cur.built));
      }
    }
    requestRender();
  }
  /* Where a climb is taking you (or null when it's over): that room and its
     neighbours load their pictures, and its probe is taken now, at the foot
     of the stairs, rather than in the middle of the hand-over. */
  function prepare(floor, ri) {
    pre = floor == null ? null : { floor, ri };
    applyView();
    const r = pre && rooms[ri];
    if (r && r.built && r.needProbe) capture(r);
    if (!pre) pump();
  }
  /* fn(ok) once the room you stand in is built and lit — or has failed. */
  function ready(fn) {
    const cur = rooms[view.ri];
    if (cur && (cur.built || cur.failed)) fn(!!cur.built);
    else readyFns.push(fn);
  }

  /* ---------------------------------------------------------------- pick */
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pick(cx, cy) {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const cands = [];
    rooms.forEach((rm) => { if (rm.group && rm.group.visible) cands.push(rm.group); });
    markers.forEach((m) => { if (m.visible) cands.push(m); });
    const hits = ray.intersectObjects(cands, true);
    for (const h of hits) {
      let o = h.object;
      if (!o.visible || (o.parent && !o.parent.visible)) continue;
      if (o.userData.pick) return o.userData.pick;
      // the first solid thing along the ray decides — a wall in front of a
      // painting in the next room keeps it out of reach
      if (o.material && o.material.visible !== false && !o.material.transparent) return null;
    }
    return null;
  }

  function upgrade(id, url) {
    for (const rm of rooms) {
      for (const e of rm.arts) {
        if (e.art.id !== id || e.hi || !rm.resident) continue;
        e.hi = true;
        loadArt(rm, e, url, true);
      }
    }
  }

  function dispose() {
    disposed = true;
    if (worker) worker.terminate();
    if (rafPending) cancelAnimationFrame(rafPending);
    const seen = new Set();
    scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      const ms = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      ms.forEach((m) => {
        if (seen.has(m)) return;
        seen.add(m);
        ["map", "lightMap"].forEach((k) => {
          if (m[k] && m[k].dispose && !m[k].isRenderTargetTexture) m[k].dispose();
        });
        m.dispose();
      });
    });
    rooms.forEach((r) => { if (r.probeRT) r.probeRT.dispose(); });
    Object.values(tex).forEach((rt) => rt.dispose());
    frameTex.forEach((t) => t.dispose());
    exitMat.map.dispose();
    markerMat.map.dispose();
    cubeRT.dispose();
    pmrem.dispose();
    neutral.dispose();
    renderer.dispose();
    if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
  }

  resize();
  const isBuilt = (i) => !!(rooms[i] && rooms[i].built);
  return { build, ready, isBuilt, setCamera, setView, prepare, render, requestRender, pick, upgrade, resize,
           dispose, renderer, scene, camera, rooms };
}
