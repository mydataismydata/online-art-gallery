/* The architecture of one room, as geometry and as light.

   A room arrives from the walk's planner as a box — centre, width, depth,
   wall height — with its doorways and its paintings. This module dresses it
   the way the galleries of the Centro Cultural de Cascais are dressed:

   - thick walls of warm white plaster, every passage between rooms a round
     arch cut deep through the wall, every other door (the front door, the
     stairs, a closed door) a rectangular opening in a limestone surround;
   - a vault over the room: a run of groin vaults (two barrels crossing, so
     each bay draws an arch on the side walls), a single barrel with a
     frosted half-round window in each free end wall, or — for the largest
     rooms — a flat ceiling opened in the middle into a tall skylight well
     edged in LED;
   - strip-oak floors and a slim oak skirting;
   - a white track hung on rods along each hung wall, one spotlight per
     painting aimed at it, small recessed downlights in the vault, and a
     green exit sign over the doors that lead back toward the entrance.

   Its output is what the light baker and the renderer need: SURFACES (the
   big pieces that get a baked light map, each with a parametrisation and a
   mesh), DERIVED pieces (trim that borrows a parent surface's light),
   PLAIN meshes (fittings lit by the reflection probe alone), and the LIGHTS,
   EMITTERS and OPENINGS that light the room. */

import * as THREE from "../vendor/three/three.module.min.js";
import { Geo, add, sub, mul, dot, norm, comb, UP, DOWN, box, obox, cylinder, disc } from "./geo.js";
import { PLASTER_RGB, FLOOR_RGB, STONE_RGB, OAK_RGB, hexToLinear } from "./materials.js";

export const REVEAL = 40;          // the walls' thickness at a doorway, cm (keep = MU_REVEAL)
const SKIRT_H = 7, SKIRT_T = 1.6;  // oak skirting
const STONE_W = 24, STONE_P = 4;   // limestone door surround: band width, how proud of the wall
const FRAME_W = 9;                 // picture frame moulding width (keep = MU_FRAME)
const E_ART = Math.PI * 0.3;       // irradiance a spotlight lays on its painting's centre: a lift, not a blaze
const WARM = [1.0, 0.955, 0.895];  // the spots' 3800 K
const WASH = [1.0, 0.97, 0.93];    // the vault's hidden uplighting
const DAY = [0.88, 0.94, 1.0];

/* ------------------------------------------------------------- the frame */

/* Wall faces of the room's interior box, in walk order (as app.js muWalls):
   each with its left end O at the floor, its axis a along the wall, inward
   normal n, and length. */
function roomFrame(room) {
  const half = REVEAL / 2;
  const doored = { n: 0, s: 0, e: 0, w: 0 };
  room.doors.forEach((d) => { doored[d.wall] = 1; });
  const x0 = room.cx - room.W / 2 + doored.w * half, x1 = room.cx + room.W / 2 - doored.e * half;
  const z0 = room.cz - room.D / 2 + doored.n * half, z1 = room.cz + room.D / 2 - doored.s * half;
  const walls = {
    w: { id: "w", O: [x0, 0, z1], a: [0, 0, -1], n: [1, 0, 0], len: z1 - z0 },
    n: { id: "n", O: [x0, 0, z0], a: [1, 0, 0], n: [0, 0, 1], len: x1 - x0 },
    e: { id: "e", O: [x1, 0, z0], a: [0, 0, 1], n: [-1, 0, 0], len: z1 - z0 },
    s: { id: "s", O: [x1, 0, z1], a: [-1, 0, 0], n: [0, 0, -1], len: x1 - x0 },
  };
  // the room-box wall centres app.js measures door offsets from
  const wc = { w: [room.cx - room.W / 2, room.cz], n: [room.cx, room.cz - room.D / 2],
               e: [room.cx + room.W / 2, room.cz], s: [room.cx, room.cz + room.D / 2] };
  Object.values(walls).forEach((w) => {
    w.doors = [];
    w.inset = doored[w.id] * half;
  });
  room.doors.forEach((d) => {
    const w = walls[d.wall];
    const c = wc[d.wall];
    const px = c[0] + w.a[0] * (d.at || 0), pz = c[1] + w.a[2] * (d.at || 0);
    const uc = (px - w.O[0]) * w.a[0] + (pz - w.O[2]) * w.a[2];
    const arch = d.kind === "entry" || d.kind === "exit" || (d.kind === "pass" && !d.closed);
    w.doors.push(Object.assign({}, d, { uc: uc, arch: arch, stone: !arch }));
  });
  return { x0, x1, z0, z1, walls, xc: (x0 + x1) / 2, zc: (z0 + z1) / 2 };
}

/* ---------------------------------------------------------- the ceiling

   One function answers "how high is the ceiling here" for any point in the
   room, whatever the vault; the walls' tops, the track rods and the lights
   all ask it. */
function chooseCeiling(room, fr) {
  const W = fr.x1 - fr.x0, D = fr.z1 - fr.z0;
  const alongX = W >= D;
  const along = alongX ? W : D, across = alongX ? D : W;
  const Hs = room.H;
  const area = W * D / 1e4;
  let type;
  if (room.layout === "breezeway") type = "barrel";
  else if (area > 70 && along / across < 1.45) type = "skylight";
  else type = room.i % 3 === 1 ? "barrel" : "groin";
  const c = { type, alongX, along, across, Hs, a: across / 2, Lh: along / 2,
              A: alongX ? [1, 0, 0] : [0, 0, 1], C: alongX ? [0, 0, 1] : [1, 0, 0],
              ctr: [fr.xc, 0, fr.zc] };
  if (type === "groin") {
    c.bays = Math.max(1, Math.round(along / across));
    c.Lb = along / c.bays;
    c.R = Math.max(120, Math.min(240, 0.36 * Math.min(across, c.Lb)));
  } else if (type === "barrel") {
    c.R = Math.max(140, Math.min(280, 0.42 * across));
  } else {
    // a skylit hall stands tall for its width, as the reference's does
    c.Hc = Math.max(Hs + 40, Math.min(640, 0.5 * Math.min(W, D)));
    c.mx = Math.max(140, 0.3 * W);
    c.mz = Math.max(140, 0.3 * D);
    c.well = 150;
  }
  return c;
}
const ell = (t) => (t >= 1 ? 0 : Math.sqrt(1 - t * t));

function ceilingY(c, x, z) {
  if (c.type === "skylight") return c.Hc;
  const s = (x - c.ctr[0]) * c.A[0] + (z - c.ctr[2]) * c.A[2];
  const q = (x - c.ctr[0]) * c.C[0] + (z - c.ctr[2]) * c.C[2];
  const hL = c.R * ell(Math.abs(q) / c.a);
  if (c.type === "barrel") return c.Hs + hL;
  const k = Math.max(0, Math.min(c.bays - 1, Math.floor((s + c.Lh) / c.Lb)));
  const ds = s - (-c.Lh + (k + 0.5) * c.Lb);
  const hT = c.R * ell(Math.abs(ds) / (c.Lb / 2));
  return c.Hs + Math.max(hL, hT);
}

/* -------------------------------------------------------------- surfaces

   A Surface is a baked piece: U×V centimetres of parameter space, a map from
   (u, v) to world position and normal, which (u, v) are really on it, its
   reflectance, and its mesh, whose vertices carry their (u, v) so the baker
   can place them in the room's light-map atlas. */
let SID = 0;
export function surface(o) {
  return Object.assign({ id: SID++, t: 5, patch: true, occ: null, special: null }, o);
}

/* A planar surface on a frame: P = O + a*u + b*v (+ n*off), normal n. */
export function planeFns(O, a, b, n) {
  return {
    P: (u, v) => [O[0] + a[0] * u + b[0] * v, O[1] + a[1] * u + b[1] * v, O[2] + a[2] * u + b[2] * v],
    N: () => n,
  };
}

/* Triangulate a (u, v) contour (CCW, closed implicitly) with optional holes
   onto a plane frame. */
export function planeMesh(geo, contour, holes, O, a, b, n, uvScale, tint) {
  // triangulateShape drops a closing point equal to the first; drop it here
  // too, so its indices and ours stay the same list
  const trim = (c) => (c.length > 1 && Math.hypot(c[0][0] - c[c.length - 1][0], c[0][1] - c[c.length - 1][1]) < 1e-6
    ? c.slice(0, -1) : c);
  contour = trim(contour);
  holes = (holes || []).map(trim);
  const pts = contour.map(([u, v]) => new THREE.Vector2(u, v));
  const hs = holes.map((h) => h.map(([u, v]) => new THREE.Vector2(u, v)));
  const tris = THREE.ShapeUtils.triangulateShape(pts, hs);
  const all = contour.concat(...holes);
  const ids = all.map(([u, v]) =>
    geo.v(comb(O, a, u, b, v), n, [u / uvScale, v / uvScale], [u, v], tint));
  tris.forEach((t) => geo.tri(ids[t[0]], ids[t[1]], ids[t[2]]));
}

/* ------------------------------------------------------------------ walls */

function wallContour(w, top) {
  const pts = [[0, 0]];
  const doors = w.doors.slice().sort((p, q) => p.uc - q.uc);
  doors.forEach((d) => {
    const l = d.uc - d.ow / 2, r = d.uc + d.ow / 2;
    pts.push([l, 0]);
    if (d.arch) {
      const rr = d.ow / 2, hj = d.oh - rr;
      pts.push([l, hj]);
      const n = 28;
      for (let k = 1; k < n; k++) {
        const th = Math.PI - (k / n) * Math.PI;
        pts.push([d.uc + rr * Math.cos(th), hj + rr * Math.sin(th)]);
      }
      pts.push([r, hj]);
    } else {
      pts.push([l, d.oh]);
      pts.push([r, d.oh]);
    }
    pts.push([r, 0]);
  });
  pts.push([w.len, 0]);
  const step = 6;
  const n = Math.max(1, Math.ceil(w.len / step));
  for (let k = n; k >= 0; k--) {
    const u = (k / n) * w.len;
    pts.push([u, top(u)]);
  }
  // drop consecutive duplicates (earcut dislikes them)
  return pts.filter((p, i) => i === 0 || Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) > 0.01);
}

/* Clip a polygon to the half plane v <= h (keep = "below") or v >= h. */
function clipV(pts, h, keep) {
  const inside = (p) => (keep === "below" ? p[1] <= h + 1e-6 : p[1] >= h - 1e-6);
  const out = [];
  for (let k = 0; k < pts.length; k++) {
    const a = pts[k], b = pts[(k + 1) % pts.length];
    const ia = inside(a), ib = inside(b);
    if (ia) out.push(a);
    if (ia !== ib) {
      const t = (h - a[1]) / (b[1] - a[1]);
      out.push([a[0] + (b[0] - a[0]) * t, h]);
    }
  }
  return out.filter((p, i) => i === 0 || Math.hypot(p[0] - out[i - 1][0], p[1] - out[i - 1][1]) > 0.01);
}

function inDoor(w, u, v) {
  for (const d of w.doors) {
    if (Math.abs(u - d.uc) >= d.ow / 2) continue;
    if (d.arch) {
      const rr = d.ow / 2, hj = d.oh - rr;
      if (v < hj || (u - d.uc) ** 2 + (v - hj) ** 2 < rr * rr) return true;
    } else if (v < d.oh) return true;
  }
  return false;
}

/* --------------------------------------------------------------- vaults */

/* One web of a groin bay or a whole barrel: part of an elliptic cylinder.
   Frame: origin o (on the axis, at springing height), axis unit A (u runs
   along it from `u0` to `u1`), profile unit C across, half-span `a`, rise R.
   The profile point at angle th: across = -a cos th, up = R sin th. `inside`
   decides which (u, th) belong to the web. */
export function vaultSurface(o, A, C, u0, u1, a, R, inside, nP, nQ, domain) {
  const rAvg = (a + R) / 2;
  const V = Math.PI * rAvg;
  const P = (u, v) => {
    const th = v / rAvg;
    return [o[0] + A[0] * u - C[0] * a * Math.cos(th),
            o[1] + R * Math.sin(th),
            o[2] + A[2] * u - C[2] * a * Math.cos(th)];
  };
  const N = (u, v) => {
    const th = v / rAvg;
    // inward: (across, up) ∝ (R cos th, -a sin th)
    const nc = R * Math.cos(th), ny = -a * Math.sin(th);
    const l = Math.hypot(nc, ny) || 1;
    return [C[0] * nc / l, ny / l, C[2] * nc / l];
  };
  const geo = new Geo();
  // mesh over the web's own domain: `domain(p, q)` → (u, th)
  const ids = [];
  for (let i = 0; i <= nP; i++) {
    const row = [];
    for (let j = 0; j <= nQ; j++) {
      const [u, th] = domain(i / nP, j / nQ);
      const v = th * rAvg;
      row.push(geo.v(P(u, v), N(u, v), [u / 200, v / 200], [u - u0, v]));
    }
    ids.push(row);
  }
  for (let i = 0; i < nP; i++) for (let j = 0; j < nQ; j++)
    geo.quad(ids[i][j], ids[i + 1][j], ids[i + 1][j + 1], ids[i][j + 1]);
  return surface({
    mat: "ceiling", U: u1 - u0, V: V, t: 6, rho: PLASTER_RGB,
    P: (u, v) => P(u + u0, v), N: (u, v) => N(u + u0, v),
    inside: (u, v) => inside(u + u0, v / rAvg),
    geo: geo, kind: "vault",
  });
}

function buildCeiling(c, fr, S, plain, M, out) {
  const Hs = c.Hs;
  if (c.type === "barrel") {
    const o = [c.ctr[0], Hs, c.ctr[2]];
    S.push(vaultSurface(o, c.A, c.C, -c.Lh, c.Lh, c.a, c.R, () => true,
      Math.max(4, Math.ceil(c.along / 60)), 40,
      (p, q) => [-c.Lh + p * c.along, q * Math.PI]));
    return;
  }
  if (c.type === "groin") {
    const b = c.Lb / 2;
    for (let k = 0; k < c.bays; k++) {
      const sc = -c.Lh + (k + 0.5) * c.Lb;
      const bayC = comb(c.ctr, c.A, sc, UP, Hs);
      // the two longitudinal webs (toward the bay ends): axis A, profile across C
      [1, -1].forEach((sgn) => {
        const o = bayC;
        S.push(vaultSurface(o, c.A, c.C, sgn > 0 ? 0 : -b, sgn > 0 ? b : 0, c.a, c.R,
          (u, th) => Math.abs(Math.cos(th)) <= Math.abs(u) / b + 1e-6,
          18, 28,
          (p, q) => {
            const u = sgn * b * p;
            const across = c.a * (q * 2 - 1) * p;          // -a p .. a p
            return [u, Math.acos(Math.max(-1, Math.min(1, -across / c.a)))];
          }));
      });
      // the two transverse webs (toward the side walls): axis C, profile along A
      [1, -1].forEach((sgn) => {
        S.push(vaultSurface(bayC, c.C, c.A, sgn > 0 ? 0 : -c.a, sgn > 0 ? c.a : 0, b, c.R,
          (u, th) => Math.abs(Math.cos(th)) <= Math.abs(u) / c.a + 1e-6,
          18, 28,
          (p, q) => {
            const u = sgn * c.a * p;
            const al = b * (q * 2 - 1) * p;
            return [u, Math.acos(Math.max(-1, Math.min(1, -al / b)))];
          }));
      });
    }
    return;
  }
  // skylight: a flat ceiling with a well in its middle
  const W = fr.x1 - fr.x0, D = fr.z1 - fr.z0;
  const wx0 = c.mx, wx1 = W - c.mx, wz0 = c.mz, wz1 = D - c.mz;
  const O = [fr.x0, c.Hc, fr.z0];
  const geo = new Geo();
  // facing down: u along +x, v along +z, normal -y
  planeMesh(geo, [[0, 0], [W, 0], [W, D], [0, D]],
    [[[wx0, wz0], [wx0, wz1], [wx1, wz1], [wx1, wz0]]], O, [1, 0, 0], [0, 0, 1], DOWN, 200);
  S.push(surface(Object.assign({ mat: "ceiling", U: W, V: D, t: 7, geo, kind: "ceiling", rho: PLASTER_RGB,
    inside: (u, v) => !(u > wx0 && u < wx1 && v > wz0 && v < wz1) },
    planeFns(O, [1, 0, 0], [0, 0, 1], DOWN))));
  // the well's four walls, rising to the glass
  const H2 = c.well;
  const wells = [
    { O: [fr.x0 + wx0, c.Hc, fr.z0 + wz0], a: [1, 0, 0], n: [0, 0, 1], len: wx1 - wx0 },
    { O: [fr.x0 + wx1, c.Hc, fr.z0 + wz0], a: [0, 0, 1], n: [-1, 0, 0], len: wz1 - wz0 },
    { O: [fr.x0 + wx1, c.Hc, fr.z0 + wz1], a: [-1, 0, 0], n: [0, 0, -1], len: wx1 - wx0 },
    { O: [fr.x0 + wx0, c.Hc, fr.z0 + wz1], a: [0, 0, -1], n: [1, 0, 0], len: wz1 - wz0 },
  ];
  wells.forEach((w) => {
    const g = new Geo();
    planeMesh(g, [[0, 0], [w.len, 0], [w.len, H2], [0, H2]], null, w.O, w.a, UP, w.n, 200);
    S.push(surface(Object.assign({ mat: "ceiling", U: w.len, V: H2, t: 7, geo: g, kind: "well", rho: PLASTER_RGB },
      planeFns(w.O, w.a, UP, w.n))));
    // the LED line at the well's mouth
    const led = new Geo();
    const lo = comb(w.O, w.n, 0.4, UP, 0);
    const q = [[0, 0.5], [w.len, 0.5], [w.len, 3.5], [0, 3.5]].map(([u, v]) =>
      led.v(comb(lo, w.a, u, UP, v), w.n, [0, 0]));
    led.quad(q[0], q[1], q[2], q[3]);
    plain.push({ mat: "led", geo: led });
  });
  // the glass, and its steel glazing bars
  const glass = new Geo();
  const gy = c.Hc + H2;
  const gq = [[wx0, wz0], [wx1, wz0], [wx1, wz1], [wx0, wz1]].map(([u, v]) =>
    glass.v([fr.x0 + u, gy, fr.z0 + v], DOWN, [0, 0]));
  glass.quad(gq[0], gq[1], gq[2], gq[3]);
  plain.push({ mat: "daylight", geo: glass });
  const bars = new Geo();
  const nbx = Math.max(1, Math.round((wx1 - wx0) / 125)), nbz = Math.max(1, Math.round((wz1 - wz0) / 125));
  for (let k = 1; k < nbx; k++) {
    const x = fr.x0 + wx0 + (k / nbx) * (wx1 - wx0);
    box(bars, x, gy - 1.5, fr.z0 + (wz0 + wz1) / 2, 2, 3, wz1 - wz0);
  }
  for (let k = 1; k < nbz; k++) {
    const z = fr.z0 + wz0 + (k / nbz) * (wz1 - wz0);
    box(bars, fr.x0 + (wx0 + wx1) / 2, gy - 1.5, z, wx1 - wx0, 3, 2);
  }
  plain.push({ mat: "metalWhite", geo: bars });
  // the glass is the room's daylight
  out.emitters.push({
    pts: [[fr.x0 + wx0, gy - 0.5, fr.z0 + wz0], [fr.x0 + wx1, gy - 0.5, fr.z0 + wz0],
          [fr.x0 + wx1, gy - 0.5, fr.z0 + wz1], [fr.x0 + wx0, gy - 0.5, fr.z0 + wz1]],
    n: DOWN, L: mul(DAY, 3.4),
  });
}

/* -------------------------------------------------------------- doorways

   The reveal of an opening through the wall's thickness: two jambs, the
   arch's intrados (or a flat soffit), and the floor across it. Built by the
   room the door belongs to (for a passage, the room it leads out of); depth
   runs from this room's face (0) back through the wall (T). */
function buildReveal(w, d, T, S, out) {
  const back = mul(w.n, -1);
  const l = d.uc - d.ow / 2, r = d.uc + d.ow / 2;
  const mat = d.stone ? "stone" : "plaster";
  const rr = d.ow / 2, hj = d.arch ? d.oh - rr : d.oh;
  const jamb = (u, nrm) => {
    const O = comb(w.O, w.a, u, [0, 0, 0], 0);
    const g = new Geo();
    // param: u' = depth (0..T), v = height
    planeMesh(g, [[0, 0], [T, 0], [T, hj], [0, hj]], null, O, back, UP, nrm, 120);
    S.push(surface(Object.assign({ mat, U: T, V: hj, t: 4, geo: g, kind: "reveal", patch: false,
      special: { reveal: "jamb", w, u: u, T } }, planeFns(O, back, UP, nrm))));
  };
  jamb(l, w.a);
  jamb(r, mul(w.a, -1));
  if (d.arch) {
    // intrados: param u' = depth, v = arc length from the right springing
    const n = 32;
    const g = new Geo();
    const ids = [];
    for (let k = 0; k <= n; k++) {
      const th = (k / n) * Math.PI;
      const pu = d.uc + rr * Math.cos(th), pv = hj + rr * Math.sin(th);
      const nrm = norm(add(mul(w.a, -Math.cos(th)), mul(UP, -Math.sin(th))));
      const base = comb(w.O, w.a, pu, UP, pv);
      ids.push([g.v(base, nrm, [0, th * rr / 120], [0, th * rr]),
                g.v(add(base, mul(back, T)), nrm, [T / 120, th * rr / 120], [T, th * rr])]);
    }
    for (let k = 0; k < n; k++) g.quad(ids[k][0], ids[k][1], ids[k + 1][1], ids[k + 1][0]);
    S.push(surface({
      mat, U: T, V: Math.PI * rr, t: 4, geo: g, kind: "reveal", patch: false,
      special: { reveal: "intrados", w, u: d.uc, T },
      P: (u, v) => { const th = v / rr; return add(comb(w.O, w.a, d.uc + rr * Math.cos(th), UP, hj + rr * Math.sin(th)), mul(back, u)); },
      N: (u, v) => { const th = v / rr; return norm(add(mul(w.a, -Math.cos(th)), mul(UP, -Math.sin(th)))); },
    }));
  } else {
    const O = comb(w.O, w.a, l, UP, d.oh);
    const g = new Geo();
    planeMesh(g, [[0, 0], [d.ow, 0], [d.ow, T], [0, T]], null, O, w.a, back, DOWN, 120);
    S.push(surface(Object.assign({ mat, U: d.ow, V: T, t: 4, geo: g, kind: "reveal", patch: false,
      special: { reveal: "soffit", w, u: d.uc, T } }, planeFns(O, w.a, back, DOWN))));
  }
  // the floor through the opening
  const O = comb(w.O, w.a, l, UP, 0.05);
  const g = new Geo();
  const quad = [[0, 0], [d.ow, 0], [d.ow, T], [0, T]];
  const ids = quad.map(([u, v]) => {
    const p = add(comb(O, w.a, u, [0, 0, 0], 0), mul(back, v));
    return g.v(p, UP, [p[0] / FLOOR_TILE_REF.v, p[2] / FLOOR_TILE_REF.v], [u, v]);
  });
  g.quad(ids[0], ids[1], ids[2], ids[3]);
  S.push(surface(Object.assign({ mat: "floor", U: d.ow, V: T, t: 5, geo: g, kind: "reveal", patch: false,
    special: { reveal: "floor", w, u: d.uc, T } }, planeFns(O, w.a, back, UP))));
}
export const FLOOR_TILE_REF = { v: 336 };

/* The limestone surround of a rectangular door: a flat band proud of the
   wall with a small step at its inner edge, and its returns. It borrows the
   wall's light (DERIVED), tinted by which way each face turns. */
function buildSurround(w, d, wallSurf, derived) {
  const g = new Geo();
  g.tinted = true;
  const l = d.uc - d.ow / 2, r = d.uc + d.ow / 2, top = d.oh;
  const P = (u, v, depth) => comb(w.O, w.a, u, UP, v, w.n, depth);
  const face = (u0, v0, u1, v1, depth, tint) => {
    const q = [[u0, v0], [u1, v0], [u1, v1], [u0, v1]].map(([u, v]) =>
      g.v(P(u, v, depth), w.n, [u / 120, v / 120], [u, v], tint));
    g.quad(q[0], q[1], q[2], q[3]);
  };
  const ret = (pa, pb, nrm, d0, d1, tint) => {
    // a strip from depth d0 to d1 along the edge pa→pb (each [u, v])
    const q = [[pa, d0], [pb, d0], [pb, d1], [pa, d1]].map(([p, dd]) =>
      g.v(P(p[0], p[1], dd), nrm, [(p[0] + p[1]) / 120, dd / 120], [p[0], p[1]], tint));
    g.quad(q[0], q[1], q[2], q[3]);
  };
  const W = STONE_W, SP = STONE_P, ST = 2.2;  // ST: the inner step's width
  const ol = l - W, or = r + W, ot = top + W;
  const T1 = [1, 1, 1];
  // outer band (full proud) and the inner fillet (a step back)
  face(ol, 0, l - ST, ot, SP, T1);
  face(r + ST, 0, or, ot, SP, T1);
  face(l - ST, top + ST, r + ST, ot, SP, T1);
  face(l - ST, 0, l, top + ST, SP - 1.4, [0.93, 0.93, 0.93]);
  face(r, 0, r + ST, top + ST, SP - 1.4, [0.93, 0.93, 0.93]);
  face(l, top, r, top + ST, SP - 1.4, [0.93, 0.93, 0.93]);
  // returns: outer edges back to the wall, and the step's riser
  ret([ol, 0], [ol, ot], mul(w.a, -1), 0, SP, [0.82, 0.82, 0.82]);
  ret([or, 0], [or, ot], w.a, 0, SP, [0.82, 0.82, 0.82]);
  ret([ol, ot], [or, ot], UP, 0, SP, [1.08, 1.08, 1.08]);
  ret([l - ST, 0], [l - ST, top + ST], w.a, SP - 1.4, SP, [0.8, 0.8, 0.8]);
  ret([r + ST, 0], [r + ST, top + ST], mul(w.a, -1), SP - 1.4, SP, [0.8, 0.8, 0.8]);
  ret([l - ST, top + ST], [r + ST, top + ST], DOWN, SP - 1.4, SP, [0.62, 0.62, 0.62]);
  // the step's inner riser runs down into the reveal's face
  ret([l, 0], [l, top], w.a, 0, SP - 1.4, [0.78, 0.78, 0.78]);
  ret([r, 0], [r, top], mul(w.a, -1), 0, SP - 1.4, [0.78, 0.78, 0.78]);
  ret([l, top], [r, top], DOWN, 0, SP - 1.4, [0.6, 0.6, 0.6]);
  derived.push({ mat: "stone", geo: g, ref: wallSurf });
}

/* The oak skirting along a wall, broken at its doors; it borrows the wall's
   light at its own height. */
function buildSkirting(w, wallSurf, derived) {
  const g = new Geo();
  g.tinted = true;
  const gaps = w.doors.map((d) => {
    const pad = d.stone ? STONE_W : 0;
    return [d.uc - d.ow / 2 - pad, d.uc + d.ow / 2 + pad];
  }).sort((p, q) => p[0] - q[0]);
  const runs = [];
  let u = 0;
  gaps.forEach(([g0, g1]) => { if (g0 > u) runs.push([u, g0]); u = Math.max(u, g1); });
  if (u < w.len) runs.push([u, w.len]);
  const P = (uu, v, d) => comb(w.O, w.a, uu, UP, v, w.n, d);
  runs.forEach(([u0, u1]) => {
    if (u1 - u0 < 1) return;
    const front = [[u0, 0], [u1, 0], [u1, SKIRT_H], [u0, SKIRT_H]].map(([uu, v]) =>
      g.v(P(uu, v, SKIRT_T), w.n, [uu / 240, v / 60], [uu, Math.max(v, 3)], [0.9, 0.9, 0.9]));
    g.quad(front[0], front[1], front[2], front[3]);
    const top = [[u0, 0], [u1, 0], [u1, SKIRT_T], [u0, SKIRT_T]].map(([uu, dd]) =>
      g.v(P(uu, SKIRT_H, dd), UP, [uu / 240, 0.2], [uu, SKIRT_H + 1], [1.12, 1.12, 1.12]));
    g.quad(top[0], top[1], top[2], top[3]);
    // ends, where a run stops at a door
    [[u0, -1], [u1, 1]].forEach(([uu, s]) => {
      if (uu <= 0.5 || uu >= w.len - 0.5) return;
      const nrm = mul(w.a, s);
      const q = [[0, 0], [SKIRT_T, 0], [SKIRT_T, SKIRT_H], [0, SKIRT_H]].map(([dd, v]) =>
        g.v(P(uu, v, dd), nrm, [dd / 60, v / 60], [uu, Math.max(v, 3)], [0.8, 0.8, 0.8]));
      g.quad(q[0], q[1], q[2], q[3]);
    });
  });
  if (!g.empty) derived.push({ mat: "oak", geo: g, ref: wallSurf });
}

/* ------------------------------------------------------------ the frames

   A gilt moulding run round the canvas and mitred at the corners: a profile
   of (out from the sight edge, proud of the wall) points extruded along each
   side, every side lengthening with its distance out so the corners meet on
   the diagonal. Two profiles, as the walk always had two frames. */
const PROFILES = [
  // classic: sight lip, bead, a sunk scotia, a rounded top, the outer side
  [[0, 1.4], [0.5, 2.9], [1.0, 3.5], [1.6, 3.8], [2.2, 3.5], [2.6, 3.1], [3.6, 2.7],
   [4.8, 2.9], [5.8, 3.8], [6.6, 5.2], [7.3, 6.3], [8.1, 6.8], [8.7, 6.4], [9.0, 5.6], [9.0, 0]],
  // antique: a broad cove sweeping up to a flat outer band
  [[0, 1.2], [0.6, 2.4], [1.2, 2.6], [2.4, 3.0], [3.8, 3.8], [5.0, 4.9], [6.0, 6.0],
   [6.6, 6.5], [8.4, 6.5], [9.0, 6.0], [9.0, 0]],
];

/* The frame's face is the photograph of a real gilt frame (static/img/
   frame1.png, frame2.png — the walk's two frames since the beginning),
   mapped the way a CSS border-image slices it: each side takes its edge strip
   of the picture, the corners keep their carving at its own scale, the long
   runs stretch. The moulding underneath gives it depth and silhouette, and
   its light comes from a small baked plane of its own (the spotlight's pool
   where the frame stands), tinted per facet by which way the facet turns. */
const FRAME_PNG = [
  { W: 1332, H: 459, t: 62, r: 64, b: 62, l: 66 },
  { W: 647, H: 807, t: 114, r: 111, b: 110, l: 113 },
];

function buildFrame(C, a, n, cw, ch, style) {
  const geo = new Geo();
  geo.tinted = true;
  const prof = PROFILES[style % PROFILES.length];
  const png = FRAME_PNG[style % FRAME_PNG.length];
  const F = FRAME_W;
  const segN = [];
  for (let k = 0; k < prof.length - 1; k++) {
    const dx = prof[k + 1][0] - prof[k][0], dy = prof[k + 1][1] - prof[k][1];
    const l = Math.hypot(dx, dy) || 1;
    segN.push([-dy / l, dx / l]);   // (outward, proud) parts of the facet's normal
  }
  // frame-plane coordinates: from the outer bottom-left corner, along a and up
  const ox = -cw / 2 - F, oy = -ch / 2 - F;
  // Per side: its run direction, outward direction, half length (sight),
  // the half extent across, and how its picture strip is read.
  const sides = [
    { key: "b", dir: a, out: mul(UP, -1), half: cw / 2, perp: ch / 2 },
    { key: "r", dir: UP, out: a, half: ch / 2, perp: cw / 2 },
    { key: "t", dir: mul(a, -1), out: UP, half: cw / 2, perp: ch / 2 },
    { key: "l", dir: mul(UP, -1), out: mul(a, -1), half: ch / 2, perp: cw / 2 },
  ];
  // the picture's coordinates (px) for a point `along` the side (from its
  // start, -half-c … half+c, in the side's run direction) at offset c out
  // from the sight edge
  const pngXY = (side, along, c) => {
    const f = c / F;                                   // 0 at the sight edge, 1 outside
    const run = (lo, hi, span, t0, t1) => {
      // corner zone below -half, stretch zone between, corner zone above
      if (along < -side.half) return lo - (-side.half - along) * t0 / F;
      if (along > side.half) return hi + (along - side.half) * t1 / F;
      return lo + (along + side.half) / (2 * side.half) * (hi - lo);
    };
    switch (side.key) {
      case "b": return [run(png.l, png.W - png.r, 0, png.l, png.r), png.H - png.b + f * png.b];
      case "t": return [png.W - run(png.r, png.W - png.l, 0, png.r, png.l), png.t - f * png.t];
      case "r": return [png.W - png.r + f * png.r, png.H - run(png.b, png.H - png.t, 0, png.b, png.t)];
      default: return [png.l - f * png.l, run(png.t, png.H - png.b, 0, png.t, png.b)];
    }
  };
  sides.forEach((s) => {
    for (let k = 0; k < prof.length - 1; k++) {
      const [o0, d0] = prof[k], [o1, d1] = prof[k + 1];
      const nn = segN[k];
      const nw = norm(add(mul(s.out, nn[0]), mul(n, nn[1])));
      // facets turned up to the lamps read brighter, turned down darker
      const tv = Math.max(0.5, Math.min(1.22, 0.92 + 0.3 * nw[1] + 0.08 * dot(nw, n)));
      const tint = [tv, tv, tv];
      const c0 = add(C, mul(s.out, s.perp + o0)), c1 = add(C, mul(s.out, s.perp + o1));
      // three spans along the side, so the corners map as corners
      const cuts0 = [-(s.half + o0), -s.half, s.half, s.half + o0];
      const cuts1 = [-(s.half + o1), -s.half, s.half, s.half + o1];
      // the moulding's outer side has no strip of its own in the picture:
      // it takes the gilt band just inside the outer edge, read by depth
      const side = Math.abs(o1 - o0) < 1e-6;
      const row = (cc, depth, cuts, oc) => cuts.map((al) => {
        const p = add(add(cc, mul(s.dir, al)), mul(n, depth));
        const [px, py] = pngXY(s, al, side ? F * (0.97 - 0.2 * depth / 7) : oc);
        const lu = (p[0] - C[0]) * a[0] + (p[2] - C[2]) * a[2] - ox;
        const lv = p[1] - C[1] - oy;
        return geo.v(p, nw, [px / png.W, 1 - py / png.H], [lu, lv], tint);
      });
      const r0 = row(c0, d0, cuts0, o0), r1 = row(c1, d1, cuts1, o1);
      for (let j = 0; j < 3; j++) {
        if (j !== 1 && o0 === 0 && o1 === 0) continue;
        geo.quad(r0[j], r0[j + 1], r1[j + 1], r1[j]);
      }
    }
  });
  // its light: a plane the size of the frame, a few centimetres proud
  const O = comb(C, a, ox, UP, oy, n, 4);
  const surf = surface(Object.assign({ mat: "frameLight", U: cw + 2 * F, V: ch + 2 * F, t: 3,
    geo: null, kind: "frame", rho: [0.4, 0.3, 0.15], patch: false },
    planeFns(O, a, UP, n)));
  return { geo, surf };
}

/* ------------------------------------------------------------- the room */

export function buildRoom(room, opts) {
  FLOOR_TILE_REF.v = opts.floorTile;
  const fr = roomFrame(room);
  const c = chooseCeiling(room, fr);
  const wallRGB = room.color ? hexToLinear(room.color) : PLASTER_RGB;
  const S = [], derived = [], plain = [];
  const out = { lights: [], emitters: [], openings: [], picks: [], signs: [], arts: [], extents: [] };

  /* ---- walls ---- */
  const wallSurf = {};
  Object.values(fr.walls).forEach((w) => {
    const top = (u) => {
      const p = comb(w.O, w.a, u, w.n, 1);
      return ceilingY(c, p[0], p[2]);
    };
    w.top = top;
    const contour = wallContour(w, (u) => top(u) + 3);
    const g = new Geo();
    // A painted room is painted up to the springing line; the lunettes
    // above it stay white with the vault, as a palace's would.
    const painted = !!room.color && c.type !== "skylight";
    const upper = [];
    if (painted) {
      planeMesh(g, clipV(contour, c.Hs, "below"), null, w.O, w.a, UP, w.n, 200);
      // each arch of the wall's top above the springing line, as its own piece
      let run = null;
      const n = Math.max(2, Math.ceil(w.len / 4));
      for (let k = 0; k <= n; k++) {
        const u = (k / n) * w.len, hgt = top(u) + 3;
        if (hgt > c.Hs + 0.5) { if (!run) run = []; run.push([u, hgt]); }
        if ((hgt <= c.Hs + 0.5 || k === n) && run) {
          if (run.length > 1) upper.push([[run[0][0], c.Hs]].concat(run, [[run[run.length - 1][0], c.Hs]]));
          run = null;
        }
      }
    } else {
      planeMesh(g, contour, null, w.O, w.a, UP, w.n, 200);
    }
    let vmax = 0;
    for (let k = 0; k <= 40; k++) vmax = Math.max(vmax, top((k / 40) * w.len));
    const s = surface(Object.assign({
      mat: "plaster", U: w.len, V: vmax, t: opts.texel, geo: g, kind: "wall", wall: w,
      rho: wallRGB,
      inside: (u, v) => v <= top(u) && !inDoor(w, u, v),
      occ: [],
      rhoHigh: painted ? { v: c.Hs, rho: PLASTER_RGB } : null,
    }, planeFns(w.O, w.a, UP, w.n)));
    S.push(s);
    wallSurf[w.id] = s;
    if (upper.length) {
      const ug = new Geo();
      ug.tinted = true;
      upper.forEach((poly) => planeMesh(ug, poly, null, w.O, w.a, UP, w.n, 200, [1, 1, 1]));
      derived.push({ mat: "ceiling", geo: ug, ref: s });
    }
    buildSkirting(w, s, derived);
  });

  /* ---- floor ---- */
  {
    const W = fr.x1 - fr.x0, D = fr.z1 - fr.z0;
    const O = [fr.x0, 0, fr.z0];
    const g = new Geo();
    const ids = [[0, 0], [W, 0], [W, D], [0, D]].map(([u, v]) =>
      g.v([fr.x0 + u, 0, fr.z0 + v], UP, [(fr.x0 + u) / opts.floorTile, (fr.z0 + v) / opts.floorTile], [u, v]));
    g.quad(ids[0], ids[1], ids[2], ids[3]);
    S.push(surface(Object.assign({ mat: "floor", U: W, V: D, t: opts.texel + 1, geo: g, kind: "floor",
      rho: FLOOR_RGB }, planeFns(O, [1, 0, 0], [0, 0, 1], UP))));
  }

  /* ---- ceiling ---- */
  buildCeiling(c, fr, S, plain, null, out);
  S.forEach((s) => { if (!s.rho) s.rho = s.mat === "floor" ? FLOOR_RGB : wallRGB; });

  /* ---- doorways ---- */
  Object.values(fr.walls).forEach((w) => {
    w.doors.forEach((d) => {
      // a passage's reveal belongs to the room it leads out of
      if (d.kind !== "entry") buildReveal(w, d, d.kind === "exit" ? REVEAL : REVEAL / 2 + 2, S, out);
      if (d.stone) buildSurround(w, d, wallSurf[w.id], derived);
      // what lies beyond the opening, for the light: the next room glowing
      // through a passage, a dark stair or corridor, a closed leaf
      const glow = d.kind === "entry" || d.kind === "exit" ? 0.62 : (d.kind === "front" ? 0.06 : 0.14);
      const l = d.uc - d.ow / 2, r = d.uc + d.ow / 2;
      out.openings.push({
        pts: [comb(w.O, w.a, l, UP, 0), comb(w.O, w.a, r, UP, 0),
              comb(w.O, w.a, r, UP, d.oh), comb(w.O, w.a, l, UP, d.oh)],
        n: w.n, L: [glow, glow * 0.97, glow * 0.92], w: w, d: d,
      });
      buildDoorFill(w, d, room, plain, out);
      // exit signs over the ways back toward the entrance (and the front door)
      if (d.kind === "entry" || d.kind === "front" || d.kind === "stairup" || d.kind === "stairdown") {
        const sy = d.oh + (d.stone ? STONE_W : 0) + 26;
        if (sy + 12 < w.top(d.uc)) {
          out.signs.push({ p: comb(w.O, w.a, d.uc, UP, sy, w.n, 2.6), a: w.a, n: w.n });
        }
      }
      // a fire-alarm call point beside the doors that lead out
      if (d.kind === "entry" || d.kind === "front") {
        const u = d.uc + d.ow / 2 + (d.stone ? STONE_W : 0) + 34;
        if (u < w.len - 30) {
          const g = new Geo();
          const p = comb(w.O, w.a, u, UP, 138, w.n, 2.2);
          obox(g, p, w.a, UP, w.n, 4.5, 4.5, 2.2);
          plain.push({ mat: "red", geo: g });
          // its break-glass window: a white square in the red frame
          const win = new Geo();
          obox(win, comb(p, w.n, 2.25, UP, 0.3), w.a, UP, w.n, 2.9, 2.9, 0.15);
          plain.push({ mat: "metalWhite", geo: win });
        }
      }
    });
  });

  /* ---- lunettes: a frosted half-round window in each free end wall of a barrel ---- */
  if (c.type === "barrel") {
    Object.values(fr.walls).forEach((w) => {
      const endWall = c.alongX ? (w.id === "e" || w.id === "w") : (w.id === "n" || w.id === "s");
      if (!endWall || w.doors.length) return;
      buildLunette(w, c, plain, out);
    });
  }

  /* ---- paintings, their lights and fittings ---- */
  const tracks = {};
  (room.arts || []).forEach((art) => {
    const w = fr.walls[art.wall];
    if (!w) return;
    const n = w.n, a = w.a;
    // the wall face behind the piece; app.js placed it 2.4 cm proud
    const face = [art.x - n[0] * 2.4, 0, art.z - n[2] * 2.4];
    const u = (face[0] - w.O[0]) * a[0] + (face[2] - w.O[2]) * a[2];
    const cy = art.cy;
    const C = [face[0], cy, face[2]];
    const cw = art.cw, ch = art.ch;
    // canvas, sunk a little into the frame
    const depth = 1.6;
    const O = comb(C, a, -cw / 2, UP, -ch / 2, n, depth);
    const g = new Geo();
    const ids = [[0, 0], [cw, 0], [cw, ch], [0, ch]].map(([uu, vv]) =>
      g.v(comb(O, a, uu, UP, vv), n, [uu / cw, vv / ch], [uu, vv]));
    g.quad(ids[0], ids[1], ids[2], ids[3]);
    const rails = [
      // frame rails as world boxes (min, max), for the canvas's own shadows
      railBox(C, a, n, -cw / 2 - FRAME_W, ch / 2, cw / 2 + FRAME_W, ch / 2 + FRAME_W, 7),
      railBox(C, a, n, -cw / 2 - FRAME_W, -ch / 2 - FRAME_W, cw / 2 + FRAME_W, -ch / 2, 7),
      railBox(C, a, n, -cw / 2 - FRAME_W, -ch / 2, -cw / 2, ch / 2, 7),
      railBox(C, a, n, cw / 2, -ch / 2, cw / 2 + FRAME_W, ch / 2, 7),
    ];
    const cs = surface(Object.assign({ mat: "canvas", U: cw, V: ch, t: Math.max(2, Math.min(5, Math.max(cw, ch) / 40)),
      geo: g, kind: "canvas", art: art, rho: [0.22, 0.2, 0.17], patch: false, occ: rails },
      planeFns(O, a, UP, n)));
    S.push(cs);
    // the frame, and the little baked plane that lights it
    const fr2 = buildFrame(C, a, n, cw, ch, art.frame || 0);
    S.push(fr2.surf);
    out.arts.push({ art, canvas: cs, frame: fr2.geo, frameLight: fr2.surf });
    // the whole framed piece shadows the wall behind it
    wallSurf[w.id].occ.push(railBox(C, a, n, -cw / 2 - FRAME_W, -ch / 2 - FRAME_W,
      cw / 2 + FRAME_W, ch / 2 + FRAME_W, 6.8));
    wallSurf[w.id].hung = wallSurf[w.id].hung || [];
    wallSurf[w.id].hung.push([u - cw / 2 - FRAME_W, u + cw / 2 + FRAME_W, cy - ch / 2 - FRAME_W, cy + ch / 2 + FRAME_W]);
    // its spotlight: hung from the vault (or a flat ceiling's track) about
    // 30° off the vertical from the picture, the museum rule of thumb
    let dT = 120, hS = 0;
    for (let k = 0; k < 3; k++) {
      const m = comb(w.O, a, u, n, dT);
      hS = ceilingY(c, m[0], m[2]);
      dT = Math.max(80, Math.min(260, 0.55 * (hS - cy)));
    }
    const mount = comb(w.O, a, u, UP, hS, n, dT);
    const yoke = add(mount, [0, -(c.type === "skylight" ? 13 : 10), 0]);
    const target = comb(C, n, depth, [0, 0, 0], 0);
    const dir = norm(sub(target, yoke));
    const lens = add(yoke, mul(dir, 9));
    const dist = Math.hypot(...sub(target, lens));
    const outer = Math.max(0.2, Math.min(0.6, Math.atan((Math.max(cw, ch) / 2 + 30) / dist)));
    const cosInc = Math.max(0.2, -dot(dir, n));
    out.lights.push({ type: "spot", p: lens, d: dir, cosOut: Math.cos(outer), cosIn: Math.cos(outer * 0.3),
                      I: mul(WARM, E_ART * dist * dist / cosInc) });
    const tr = tracks[w.id] = tracks[w.id] || { w, dT, hS, u0: Infinity, u1: -Infinity, spots: [] };
    tr.u0 = Math.min(tr.u0, u - cw / 2 - 40);
    tr.u1 = Math.max(tr.u1, u + cw / 2 + 40);
    tr.spots.push({ u, mount, yoke, dir });
  });
  Object.values(tracks).forEach((tr) => buildTrack(tr, c, plain));

  /* ---- the ceiling's wash: a hidden cove of light round the room ----
     An upward-facing strip along each wall, just under the springing line
     (or, under a flat ceiling, a little lower and further out), lights the
     vault in one smooth gradient. bake.js balances it against the room's
     other light. */
  const flat = c.type === "skylight";
  Object.values(fr.walls).forEach((w) => {
    const out0 = flat ? 70 : 45, wd = 14, y = flat ? c.Hs - 30 : c.Hs - 6;
    const e = 25;
    out.emitters.push({
      pts: [comb(w.O, w.a, e, UP, y, w.n, out0), comb(w.O, w.a, w.len - e, UP, y, w.n, out0),
            comb(w.O, w.a, w.len - e, UP, y, w.n, out0 + wd), comb(w.O, w.a, e, UP, y, w.n, out0 + wd)],
      n: UP, L: mul(WASH, 4),
    });
  });

  /* ---- downlights in the vault ---- */
  const downs = [];
  if (c.type === "groin") {
    for (let k = 0; k < c.bays; k++) downs.push(comb(c.ctr, c.A, -c.Lh + (k + 0.5) * c.Lb, [0, 0, 0], 0));
  } else if (c.type === "barrel") {
    const n = Math.max(1, Math.round(c.along / 260));
    for (let k = 0; k < n; k++) downs.push(comb(c.ctr, c.A, -c.Lh + (k + 0.5) * (c.along / n), [0, 0, 0], 0));
  }
  downs.forEach((p) => {
    const y = ceilingY(c, p[0], p[2]) - 0.6;
    const g = new Geo();
    disc(g, [p[0], y - 0.2, p[2]], DOWN, 9, 20);
    plain.push({ mat: "metalWhite", geo: g });
    const gl = new Geo();
    disc(gl, [p[0], y - 0.4, p[2]], DOWN, 5, 20);
    plain.push({ mat: "lampFace", geo: gl });
    out.lights.push({ type: "down", p: [p[0], y - 1, p[2]], d: DOWN, m: 2,
                      I: mul(WARM, Math.PI * 0.6 * y * y) });
  });

  return { room, fr, c, S, derived, plain, out, wallSurf };
}

function railBox(C, a, n, u0, v0, u1, v1, depth) {
  const p0 = comb(C, a, u0, UP, v0), p1 = comb(C, a, u1, UP, v1, n, depth);
  return { min: [Math.min(p0[0], p1[0]), Math.min(p0[1], p1[1]), Math.min(p0[2], p1[2])],
           max: [Math.max(p0[0], p1[0]), Math.max(p0[1], p1[1]), Math.max(p0[2], p1[2])] };
}

/* What fills an opening that isn't a passage: the front door's photographed
   leaves, a closed door's panelled leaf, the dark beyond a stair or corridor
   (a stairway's real flight comes from stairs.js). A clickable plane rides
   in the opening of every portal. */
function buildDoorFill(w, d, room, plain, out) {
  if (d.kind === "entry" || d.kind === "exit") return;
  const T = REVEAL / 2 + 2;
  const back = mul(w.n, -1);
  const C = comb(w.O, w.a, d.uc, UP, d.oh / 2, back, T);
  if (d.kind === "front") {
    const g = new Geo();
    const q = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([s, t]) =>
      g.v(comb(C, w.a, s * d.ow / 2, UP, t * d.oh / 2), w.n, [(s + 1) / 2, (t + 1) / 2]));
    g.quad(q[0], q[1], q[2], q[3]);
    plain.push({ mat: "frontDoor", geo: g });
    return;
  }
  if (d.closed || d.kind === "pass") {
    if (d.closed) {
      // a tall pair of panelled oak leaves, meeting in the middle
      const lg = new Geo();
      const half = d.ow / 2;
      [-1, 1].forEach((sd) => {
        const lc = comb(C, w.a, sd * half / 2, UP, 0);
        obox(lg, lc, w.a, UP, w.n, half / 2 - 0.4, d.oh / 2, 2.5);
        // two raised fields per leaf, bevelled all round: the bevels face
        // up, down and aside, so the room's light shades each differently
        // and the fields read even seen square-on
        [[0.27, 0.36], [0.73, 0.4]].forEach(([fy, fh]) => {
          const pc = comb(lc, UP, (fy - 0.5) * d.oh, w.n, 2.5);
          const fw = half / 2 - 13, fv = fh * d.oh / 2, bev = 4.5, rise = 2;
          const top = (s, t) => comb(pc, w.a, s * (fw - bev), UP, t * (fv - bev), w.n, rise);
          const base = (s, t) => comb(pc, w.a, s * fw, UP, t * fv);
          const face = (pts, n) => {
            const ids = pts.map((pt) => lg.v(pt, n, [0, 0]));
            lg.quad(ids[0], ids[1], ids[2], ids[3]);
          };
          const tilt = (dir) => norm(add(mul(w.n, bev), mul(dir, rise)));
          face([top(-1, -1), top(1, -1), top(1, 1), top(-1, 1)], w.n);
          face([base(-1, 1), base(1, 1), top(1, 1), top(-1, 1)], tilt(UP));
          face([base(-1, -1), base(1, -1), top(1, -1), top(-1, -1)], tilt(DOWN));
          face([base(1, -1), base(1, 1), top(1, 1), top(1, -1)], tilt(w.a));
          face([base(-1, -1), base(-1, 1), top(-1, 1), top(-1, -1)], tilt(mul(w.a, -1)));
        });
      });
      // the grain at the oak's own scale (60 cm across, 240 along), straight
      // up each leaf
      for (let k = 0; k < lg.count; k++) {
        lg.uv[2 * k] = (lg.p[3 * k] * w.a[0] + lg.p[3 * k + 2] * w.a[2]) / 60;
        lg.uv[2 * k + 1] = lg.p[3 * k + 1] / 240;
      }
      plain.push({ mat: "leafOak", geo: lg });
      const h = new Geo();
      [-1, 1].forEach((sd) => {
        const hp = comb(C, w.a, sd * 7, UP, -14, w.n, 2.5);
        cylinder(h, hp, w.n, 5, 1.1, 10);
        cylinder(h, add(hp, mul(w.n, 5)), mul(w.a, sd), 12, 0.9, 10);
      });
      plain.push({ mat: "steel", geo: h });
      return;
    }
    // an open passage: the arch runs on as a short corridor and the light
    // dies away down it — where it goes, you find out by walking in
    const g = new Geo();
    g.tinted = true;
    const gf = new Geo();
    gf.tinted = true;
    const D2 = 300, rr = d.ow / 2, hj = d.oh - rr;
    const fade = (dd) => { const k = 0.78 * Math.pow(Math.max(0, 1 - dd / D2), 1.7); return [k, k, k * 0.97]; };
    const at = (u, v, dd) => comb(w.O, w.a, d.uc + u, UP, v, back, T + dd);
    const steps = 10;
    for (let k = 0; k < steps; k++) {
      const d0 = (k / steps) * D2, d1 = ((k + 1) / steps) * D2;
      // floor
      const fq = [[-rr, d0], [rr, d0], [rr, d1], [-rr, d1]].map(([u, dd]) => {
        const p = at(u, 0.05, dd);
        return gf.v(p, UP, [p[0] / FLOOR_TILE_REF.v, p[2] / FLOOR_TILE_REF.v], null, fade(dd));
      });
      gf.quad(fq[0], fq[1], fq[2], fq[3]);
      // jambs
      [[-rr, w.a], [rr, mul(w.a, -1)]].forEach(([u, nrm]) => {
        const q = [[0, d0], [hj, d0], [hj, d1], [0, d1]].map(([v, dd]) => g.v(at(u, v, dd), nrm, [0, 0], null, fade(dd)));
        g.quad(q[0], q[1], q[2], q[3]);
      });
      // the arch's vault
      const n = 16;
      for (let j = 0; j < n; j++) {
        const t0 = (j / n) * Math.PI, t1 = ((j + 1) / n) * Math.PI;
        const pt = (th, dd) => at(rr * Math.cos(th), hj + rr * Math.sin(th), dd);
        const nr = (th) => norm(add(mul(w.a, -Math.cos(th)), mul(UP, -Math.sin(th))));
        const q = [g.v(pt(t0, d0), nr(t0), [0, 0], null, fade(d0)), g.v(pt(t1, d0), nr(t1), [0, 0], null, fade(d0)),
                   g.v(pt(t1, d1), nr(t1), [0, 0], null, fade(d1)), g.v(pt(t0, d1), nr(t0), [0, 0], null, fade(d1))];
        g.quad(q[0], q[1], q[2], q[3]);
      }
    }
    plain.push({ mat: "passWall", geo: g });
    plain.push({ mat: "passFloor", geo: gf });
    const end = new Geo();
    obox(end, at(0, d.oh / 2, D2), w.a, UP, w.n, rr + 2, d.oh / 2 + 2, 1);
    plain.push({ mat: "shadowVoid", geo: end });
    // the ground the corridor takes up beyond the wall, for walk.js
    const cs = [at(-rr, 0, 0), at(rr, 0, 0), at(-rr, 0, D2), at(rr, 0, D2)];
    out.extents.push({ x0: Math.min(...cs.map((p) => p[0])), x1: Math.max(...cs.map((p) => p[0])),
                       z0: Math.min(...cs.map((p) => p[2])), z1: Math.max(...cs.map((p) => p[2])) });
  } else {
    // a stairway: stairs.js builds the hall behind
    out.stairs = out.stairs || [];
    out.stairs.push({ w, d, room });
  }
  if (d.pi != null) {
    const g = new Geo();
    const q = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([s, t]) =>
      g.v(comb(C, w.a, s * d.ow / 2, UP, t * d.oh / 2, w.n, T - 1), w.n, [0, 0]));
    g.quad(q[0], q[1], q[2], q[3]);
    plain.push({ mat: "pick", geo: g, pick: { kind: "portal", pi: d.pi, end: d.end } });
  }
}

/* A frosted half-round window in the lunette of an end wall, with a slim
   steel frame and sill. It lights the room as an area of daylight. */
function buildLunette(w, c, plain, out) {
  const inset = Math.max(40, w.len * 0.12), n = 36;
  const a2 = w.len / 2 - inset;
  const uc = w.len / 2;
  const R2 = Math.max(40, c.R * (a2 / (w.len / 2)) - 14);
  const y0 = c.Hs + 6;
  const glass = new Geo();
  const ctr = glass.v(comb(w.O, w.a, uc, UP, y0, w.n, 1.2), w.n, [0.5, 0]);
  const rim = [];
  const pts = [];
  for (let k = 0; k <= n; k++) {
    const th = (k / n) * Math.PI;
    const u = uc - a2 * Math.cos(th), v = y0 + R2 * Math.sin(th);
    rim.push(glass.v(comb(w.O, w.a, u, UP, v, w.n, 1.2), w.n, [0.5 - 0.5 * Math.cos(th), Math.sin(th)]));
    pts.push(comb(w.O, w.a, u, UP, v, w.n, 1.0));
  }
  for (let k = 0; k < n; k++) glass.tri(ctr, rim[k], rim[k + 1]);
  plain.push({ mat: "daylight", geo: glass });
  // frame: a band along the arc and the sill
  const fg = new Geo();
  for (let k = 0; k < n; k++) {
    const t0 = (k / n) * Math.PI, t1 = ((k + 1) / n) * Math.PI;
    const ring = (th, r) => comb(w.O, w.a, uc - (a2 + r) * Math.cos(th), UP, y0 + (R2 + r) * Math.sin(th), w.n, 2.6);
    const q = [fg.v(ring(t0, 0), w.n), fg.v(ring(t1, 0), w.n), fg.v(ring(t1, 3), w.n), fg.v(ring(t0, 3), w.n)];
    fg.quad(q[0], q[1], q[2], q[3]);
  }
  obox(fg, comb(w.O, w.a, uc, UP, y0 - 1.5, w.n, 2.6), w.a, UP, w.n, a2 + 3, 2, 2.4);
  plain.push({ mat: "metalWhite", geo: fg });
  // light: the glass as a polygon (a fan of the half ellipse)
  out.emitters.push({ pts: pts, n: w.n, L: mul(DAY, 3.0) });
}

/* The spotlights' fittings. Under a vault each can hangs on a short stem
   from a small canopy on the plaster; under a flat ceiling the cans ride a
   slim surface track. Each can is turned to its picture, lens glowing. */
function buildTrack(tr, c, plain) {
  const w = tr.w;
  const g = new Geo();
  const lamp = new Geo();
  if (c.type === "skylight") {
    const u0 = Math.max(30, tr.u0), u1 = Math.min(w.len - 30, tr.u1);
    if (u1 > u0) {
      const mid = comb(w.O, w.a, (u0 + u1) / 2, UP, tr.hS - 1.8, w.n, tr.dT);
      obox(g, mid, w.a, UP, w.n, (u1 - u0) / 2, 1.8, 1.7);
    }
  }
  tr.spots.forEach((s) => {
    if (c.type !== "skylight") disc(g, add(s.mount, [0, -0.5, 0]), DOWN, 5.5, 18);
    cylinder(g, add(s.mount, [0, -0.5, 0]), DOWN, s.mount[1] - s.yoke[1], 0.8, 8);
    const back = add(s.yoke, mul(s.dir, -5));
    cylinder(g, back, s.dir, 14, 4.4, 18, false);
    disc(lamp, add(back, mul(s.dir, 14.05)), s.dir, 3.6, 18);
  });
  plain.push({ mat: "metalWhite", geo: g });
  plain.push({ mat: "lampFace", geo: lamp });
}
