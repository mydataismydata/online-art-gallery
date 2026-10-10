/* The stair hall: a real piece of architecture behind every open stair
   door, modelled on the staircase of the reference galleries.

   A U-shaped stair of oak in a two-storey hall. From the lower door the
   first flight rises away along the left wall to a half landing across the
   back; the second flight turns and climbs back along the right wall,
   overhead, to the upper landing across the front, where the upper door
   opens. The flights stand on white plaster — the first on a solid cut
   string whose top steps under every tread, the second on a sloping soffit
   with the same stepped edge along the well — and round oak handrails with
   a mid rail run up both flights on square oak posts and along the upper
   landing's edge. The underside of the upper landing is painted dark, as
   in the reference; a frosted lunette high in the back wall's vault lights
   the whole well.

   The same hall stands behind both of a stair's doors, lifted or dropped by
   one storey, so the visitor sees the flights going up from below and
   coming up from above — and app.js can walk them up the one and hand them
   over to the other on the landing without a seam.

   Hall coordinates: x across (the wall's own axis), z away from the room
   into the hall, y up from the hall's lower floor. The hall's front wall —
   the one with the doors — is z = 0. */

import { Geo, add, mul, norm, comb, UP, DOWN, obox, cylinder, disc } from "./geo.js";
import { surface, planeFns, planeMesh, vaultSurface } from "./arch.js";
import { PLASTER_RGB, FLOOR_RGB, SOFFIT_RGB } from "./materials.js";

const OAK_RHO = [0.42, 0.28, 0.15];
const DAY = [0.88, 0.94, 1.0];
const WASH = [1.0, 0.97, 0.93];    // the downlights' white, as the rooms' wash

/* The stair's dimensions, from app.js's plan (MU_STAIR) with these as the
   fallback. */
export function stairDims(spec) {
  const s = Object.assign({ FF: 460, N: 13, T: 28, F0: 115, LD: 130, FW: 125, G: 16 }, spec || {});
  s.r = s.FF / (2 * s.N);
  s.RUN = (s.N - 1) * s.T;
  s.HW = 2 * s.FW + s.G;
  s.HD = s.F0 + s.RUN + s.LD;
  s.SPR = s.FF + 330;           // the hall's vault springs here
  s.R = s.HW / 2;
  s.YL = s.N * s.r;             // the half landing's top
  s.TT = 3.5;                   // tread thickness
  s.NOSE = 2.5;
  s.SLAB = 30;                  // the upper landing's depth of floor
  s.LAND = 20;                  // the half landing's
  return s;
}

function pointInPoly(u, v, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [ui, vi] = pts[i], [uj, vj] = pts[j];
    if ((vi > v) !== (vj > v) && u < ((uj - ui) * (v - vi)) / (vj - vi || 1e-9) + ui) inside = !inside;
  }
  return inside;
}

/* Build one end's hall. st: { w (the room wall's frame), d (the door) };
   spec: the stair dimensions; floorTile: the floor texture's tile size.
   Returns the pieces in arch.js's vocabulary (S, derived, plain, out) for
   the baker and the renderer. */
export function buildStairHall(st, spec, floorTile) {
  const { w, d } = st;
  const s = stairDims(spec);
  const { FF, N, T, F0, RUN, HW, HD, SPR, R, r, YL, TT, NOSE, SLAB, LAND, FW, G } = s;
  const X = w.a, Zd = mul(w.n, -1);
  const H0 = comb(w.O, w.a, d.uc, w.n, -((w.inset || 0) + 2));
  const yOff = d.kind === "stairdown" ? -FF : 0;
  const L = (x, y, z) => [H0[0] + X[0] * x + Zd[0] * z, y + yOff, H0[2] + X[2] * x + Zd[2] * z];
  const D = (v) => norm([X[0] * v[0] + Zd[0] * v[2], v[1], X[2] * v[0] + Zd[2] * v[2]]);
  const S = [], plain = [];
  const out = { lights: [], emitters: [], openings: [], signs: [], arts: [] };

  /* ---- what stands in the light's way: the flights' blocks and the slabs ---- */
  const OCC = [];
  const lbox = (x0, y0, z0, x1, y1, z1) => {
    const a = L(x0, y0, z0), b = L(x1, y1, z1);
    OCC.push({ min: [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.min(a[2], b[2])],
               max: [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.max(a[2], b[2])] });
  };
  const xA0 = -HW / 2, xA1 = -G / 2, xB0 = G / 2, xB1 = HW / 2;
  const slope = r / T;
  const WAIST = 21;
  const soffitY = (z) => FF - WAIST - (z - F0) * slope;     // under the second flight
  // Each block starts at its riser's face, so the riser isn't inside it, and
  // the second flight's stop just above its sloping soffit: a soffit inside
  // every other block bakes in stripes.
  for (let k = 0; k < N; k++) {
    if (k < N - 1) lbox(xA0, 0, F0 + k * T, xA1, (k + 1) * r, F0 + (k + 1) * T);
    const zb0 = F0 + RUN - (k + 1) * T, zb1 = F0 + RUN - k * T;
    lbox(xB0, soffitY(zb0) + 1, zb0, xB1, YL + (k + 1) * r, zb1);
  }
  lbox(-HW / 2, YL - LAND, F0 + RUN, HW / 2, YL, HD);
  lbox(-HW / 2, FF - SLAB, 0, HW / 2, FF, F0);

  // The wood bounces its light paler than it looks: in a hall this narrow,
  // five bounces off true oak turn every wall orange, and the reference's
  // stair hall stays white.
  const tame = (c) => {
    const y = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    return c.map((v) => y + (v - y) * 0.45);
  };
  const OAK_B = tame(OAK_RHO), FLOOR_B = tame(FLOOR_RGB);
  const rhoOf = (m) => (m === "oak" ? OAK_B : m === "floor" ? FLOOR_B : m === "soffit" ? SOFFIT_RGB : PLASTER_RGB);

  /* A planar piece: local origin o, local axes ua / va, extents U×V, local
     normal nl; optionally a contour (and holes) in its (u, v). */
  const plane = (mat, o, ua, va, U, V, nl, opt) => {
    opt = opt || {};
    const O = L(o[0], o[1], o[2]), A = D(ua), B = D(va), Nn = D(nl);
    const g = new Geo();
    const contour = opt.contour || [[0, 0], [U, 0], [U, V], [0, V]];
    planeMesh(g, contour, opt.holes || null, O, A, B, Nn, 120);
    if (opt.uv) {
      for (let k = 0; k < g.count; k++) {
        const t = opt.uv(g.uvL[2 * k], g.uvL[2 * k + 1], [g.p[3 * k], g.p[3 * k + 1], g.p[3 * k + 2]]);
        g.uv[2 * k] = t[0]; g.uv[2 * k + 1] = t[1];
      }
    }
    S.push(surface(Object.assign({
      mat, U, V, t: opt.t || 7, geo: g, kind: opt.kind || "hall", rho: rhoOf(mat),
      occ: OCC, occAll: true,
      inside: opt.contour ? ((u, v) => pointInPoly(u, v, opt.contour)) : null,
    }, planeFns(O, A, B, Nn))));
  };
  /* A stack of narrow strips (every tread of a flight, every riser) as one
     surface: each strip its own rows of the light map, one above the next. */
  const stack = (mat, rows, U, nl, edgeAO) => {
    const g = new Geo();
    const Nn = D(nl);
    let v0 = 0;
    const map = [];
    rows.forEach((rw) => {
      const O = L(rw.o[0], rw.o[1], rw.o[2]), A = D(rw.ua), B = D(rw.va);
      const q = [[0, 0], [U, 0], [U, rw.V], [0, rw.V]].map(([u, v]) =>
        g.v(comb(O, A, u, B, v), Nn, [v / 60, u / 240], [u, v0 + v]));
      g.quad(q[0], q[1], q[2], q[3]);
      map.push({ v0, V: rw.V, O, A, B });
      v0 += rw.V;
    });
    const find = (v) => {
      for (const m of map) if (v < m.v0 + m.V) return m;
      return map[map.length - 1];
    };
    S.push(surface({
      mat, U, V: v0, t: 3, geo: g, kind: "hall", rho: rhoOf(mat), occ: OCC, occAll: true,
      P: (u, v) => { const m = find(v); return comb(m.O, m.A, u, m.B, Math.min(m.V, v - m.v0)); },
      N: () => Nn,
      // where a tread runs into the riser above, and under a nosing, the
      // light can't reach: a dark line at every step, as on a real stair
      aoFn: edgeAO ? (u, v) => { const m = find(v); return edgeAO(v - m.v0, m.V); } : null,
    }));
  };

  /* ---- the hall's shell ---- */
  plane("floor", [-HW / 2, 0.02, 0], [1, 0, 0], [0, 0, 1], HW, HD, [0, 1, 0],
    { uv: (u, v, p) => [p[0] / floorTile, p[2] / floorTile], t: 7 });
  plane("plaster", [-HW / 2, 0, 0], [0, 0, 1], [0, 1, 0], HD, SPR + 3, [1, 0, 0], { kind: "wall", t: 8 });
  plane("plaster", [HW / 2, 0, 0], [0, 0, 1], [0, 1, 0], HD, SPR + 3, [-1, 0, 0], { kind: "wall", t: 8 });
  const archTop = (u) => SPR + R * Math.sqrt(Math.max(0, 1 - ((u - HW / 2) / (HW / 2)) ** 2)) + 3;
  const archPts = [];
  for (let k = 32; k >= 0; k--) { const u = (k / 32) * HW; archPts.push([u, archTop(u)]); }
  plane("plaster", [-HW / 2, 0, HD], [1, 0, 0], [0, 1, 0], HW, SPR + R + 3, [0, 0, -1],
    { kind: "wall", t: 8, contour: [[0, 0], [HW, 0]].concat(archPts) });
  const dl = HW / 2 - d.ow / 2, dr = HW / 2 + d.ow / 2;
  plane("plaster", [-HW / 2, 0, 0], [1, 0, 0], [0, 1, 0], HW, SPR + R + 3, [0, 0, 1], {
    kind: "wall", t: 8,
    contour: [[0, 0], [dl, 0], [dl, d.oh], [dr, d.oh], [dr, 0], [HW, 0]].concat(archPts),
    holes: [[[dl, FF], [dr, FF], [dr, FF + d.oh], [dl, FF + d.oh]]],
  });
  S.push(vaultSurface(L(0, SPR, 0), D([0, 0, 1]), D([1, 0, 0]), 0, HD, HW / 2, R, () => true,
    Math.max(4, Math.ceil(HD / 60)), 32, (p, q) => [p * HD, q * Math.PI]));

  /* ---- the first flight: solid, on its cut string ---- */
  const rowsT = [], rowsR = [], rowsN = [];
  for (let k = 0; k < N; k++) {
    if (k < N - 1) rowsT.push({ o: [xA0, (k + 1) * r, F0 + k * T - NOSE], ua: [1, 0, 0], va: [0, 0, 1], V: T + NOSE });
    rowsR.push({ o: [xA0, k * r, F0 + k * T], ua: [1, 0, 0], va: [0, 1, 0], V: r - TT });
    rowsN.push({ o: [xA0, (k + 1) * r - TT, F0 + k * T - NOSE], ua: [1, 0, 0], va: [0, 1, 0], V: TT });
  }
  const backShade = (d) => 0.5 + 0.5 * Math.min(1, d / 7);
  const nosingLift = (d) => (d < 1.6 ? 1.12 : 1);
  stack("oak", rowsT, FW, [0, 1, 0], (v, V) => backShade(V - v) * nosingLift(v));
  // a riser darkens up under the nosing above it, a little at its foot
  const riserShade = (v, V) => (0.55 + 0.45 * Math.min(1, (V - v) / 4)) * (0.85 + 0.15 * Math.min(1, v / 3));
  stack("oak", rowsR, FW, [0, 0, -1], riserShade);
  stack("oak", rowsN, FW, [0, 0, -1]);
  const cutA = [[F0, 0]];
  for (let k = 0; k < N; k++) {
    cutA.push([F0 + k * T, (k + 1) * r - TT]);
    if (k < N - 1) cutA.push([F0 + (k + 1) * T, (k + 1) * r - TT]);
  }
  cutA.push([F0 + RUN, 0]);
  plane("plaster", [xA1, 0, 0], [0, 0, 1], [0, 1, 0], HD, YL, [1, 0, 0], { contour: cutA, t: 5 });

  /* ---- the half landing ---- */
  const worldUV = { uv: (u, v, p) => [p[0] / floorTile, p[2] / floorTile] };
  plane("floor", [-HW / 2, YL, F0 + RUN], [1, 0, 0], [0, 0, 1], HW, HD - F0 - RUN, [0, 1, 0], worldUV);
  plane("plaster", [xA1, YL - LAND, F0 + RUN], [1, 0, 0], [0, 1, 0], HW / 2 - xA1, LAND, [0, 0, -1]);
  plane("plaster", [-HW / 2, YL - LAND, F0 + RUN], [1, 0, 0], [0, 0, 1], HW, HD - F0 - RUN, [0, -1, 0]);

  /* ---- the second flight: up the right, back toward the doors ---- */
  const rowsT2 = [], rowsR2 = [], rowsN2 = [];
  for (let k = 0; k < N; k++) {
    const zk = F0 + RUN - k * T;
    if (k < N - 1) rowsT2.push({ o: [xB0, YL + (k + 1) * r, zk - T], ua: [1, 0, 0], va: [0, 0, 1], V: T + NOSE });
    rowsR2.push({ o: [xB0, YL + k * r, zk], ua: [1, 0, 0], va: [0, 1, 0], V: r - TT });
    // each tread's nosing stands NOSE proud of the riser it caps
    rowsN2.push({ o: [xB0, YL + (k + 1) * r - TT, zk + NOSE], ua: [1, 0, 0], va: [0, 1, 0], V: TT });
  }
  stack("oak", rowsT2, FW, [0, 1, 0], (v, V) => backShade(v) * nosingLift(V - v));
  stack("oak", rowsR2, FW, [0, 0, 1], riserShade);
  stack("oak", rowsN2, FW, [0, 0, 1]);
  // its soffit, sloping from the landing up to the upper landing's floor
  {
    const z0 = F0 + RUN, zTop = F0 + (FF - WAIST - (FF - SLAB)) / slope;
    const y0 = soffitY(z0), y1 = FF - SLAB;
    const len = Math.hypot(z0 - zTop, y1 - y0);
    const along = [0, (y1 - y0) / len, (zTop - z0) / len];
    const nrm = [0, -T, -r];
    const o = [xB0, y0, z0];
    const O = L(o[0], o[1], o[2]), A = D([1, 0, 0]), B = D(along), Nn = D(nrm);
    const g = new Geo();
    planeMesh(g, [[0, 0], [FW, 0], [FW, len], [0, len]], null, O, A, B, Nn, 120);
    S.push(surface(Object.assign({ mat: "plaster", U: FW, V: len, t: 6, geo: g, kind: "hall",
      rho: PLASTER_RGB, occ: OCC, occAll: true }, planeFns(O, A, B, Nn))));
    // the stepped band along the well
    const band = [[z0, YL - LAND]];
    for (let k = 0; k < N; k++) {
      const zk = F0 + RUN - k * T;
      band.push([zk, YL + (k + 1) * r - TT]);
      if (k < N - 1) band.push([zk - T, YL + (k + 1) * r - TT]);
    }
    band.push([F0, FF - SLAB], [zTop, FF - SLAB], [z0, y0]);
    plane("plaster", [xB0, 0, 0], [0, 0, 1], [0, 1, 0], HD, FF, [-1, 0, 0], { contour: band, t: 5 });
    // the upper landing: oak floor, a dark-painted underside, its plaster edge
    plane("floor", [-HW / 2, FF, 0], [1, 0, 0], [0, 0, 1], HW, F0, [0, 1, 0], worldUV);
    plane("soffit", [-HW / 2, FF - SLAB, 0], [1, 0, 0], [0, 0, 1], HW, F0, [0, -1, 0]);
    plane("soffit", [xB0, FF - SLAB, F0], [1, 0, 0], [0, 0, 1], FW, zTop - F0, [0, -1, 0]);
    plane("plaster", [-HW / 2, FF - SLAB, F0], [1, 0, 0], [0, 1, 0], xB0 + HW / 2, SLAB, [0, 0, 1], { t: 5 });
  }

  /* ---- balustrades: square oak posts, a round handrail, a mid rail ---- */
  const rail = new Geo();
  const P = (x, y, z) => L(x, y, z);
  const post = (x, z, y0, y1) => {
    const c = P(x, (y0 + y1) / 2, z);
    obox(rail, c, D([1, 0, 0]), UP, D([0, 0, 1]), 2.3, (y1 - y0) / 2, 2.3);
  };
  const bar = (p0, p1, rad) => {
    const a = P(...p0), b = P(...p1);
    const v = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const l = Math.hypot(...v);
    cylinder(rail, a, mul(v, 1 / l), l, rad, 12);
  };
  const HR = 92, MR = 46;
  // first flight, along the well
  const xa = xA1 - 4;
  const pitchA = (z) => r + (z - F0) * slope;
  const zA0 = F0 + 8, zA1 = F0 + RUN - 8;
  for (let k = 0; k <= 3; k++) {
    const z = zA0 + (k / 3) * (zA1 - zA0);
    const step = Math.min(N - 1, Math.floor((z - F0) / T));
    post(xa, z, (step + 1) * r, pitchA(z) + HR);
  }
  bar([xa, pitchA(zA0) + HR, zA0], [xa, pitchA(zA1) + HR, zA1], 2.7);
  bar([xa, pitchA(zA0) + MR, zA0], [xa, pitchA(zA1) + MR, zA1], 1.9);
  // across the well at the landing, and up the second flight
  const xb = xB0 + 4;
  post(xb, zA1, YL, YL + HR + r);
  bar([xa, YL + HR + r, zA1], [xb, YL + HR + r, zA1], 2.7);
  bar([xa, YL + MR + r, zA1], [xb, YL + MR + r, zA1], 1.9);
  const pitchB = (z) => YL + r + (F0 + RUN - z) * slope;
  const zB0 = F0 + RUN - 8, zB1 = F0 + 8;
  for (let k = 1; k <= 3; k++) {
    const z = zB0 + (k / 3) * (zB1 - zB0);
    const step = Math.min(N - 1, Math.floor((F0 + RUN - z) / T));
    post(xb, z, YL + (step + 1) * r, pitchB(z) + HR);
  }
  bar([xb, pitchB(zB0) + HR, zB0], [xb, pitchB(zB1) + HR, zB1], 2.7);
  bar([xb, pitchB(zB0) + MR, zB0], [xb, pitchB(zB1) + MR, zB1], 1.9);
  // the upper landing's edge, over the first flight
  const zU = F0 - 4;
  const xs = [-HW / 2 + 6, -HW / 4, -6, xb];
  xs.forEach((x) => post(x, zU, FF, FF + 100));
  bar([-HW / 2 + 6, FF + 100, zU], [xb, FF + 100, zU], 2.7);
  bar([-HW / 2 + 6, FF + 50, zU], [xb, FF + 50, zU], 1.9);
  bar([xb, FF + 100, zU], [xb, pitchB(zB1) + HR, zB1], 2.7);
  plain.push({ mat: "oakLit", geo: rail });

  /* ---- light: a lunette high in the back wall, downlights, the doors ---- */
  {
    const a2 = HW / 2 - 34, R2 = R - 30, y0 = SPR + 4;
    const glass = new Geo();
    const nW = D([0, 0, -1]);
    const ctr = glass.v(P(0, y0, HD - 1.2), nW, [0.5, 0]);
    const rim = [], pts = [];
    const n = 28;
    for (let k = 0; k <= n; k++) {
      const th = (k / n) * Math.PI;
      const x = -a2 * Math.cos(th), y = y0 + R2 * Math.sin(th);
      rim.push(glass.v(P(x, y, HD - 1.2), nW, [0.5 - 0.5 * Math.cos(th), Math.sin(th)]));
      pts.push(P(x, y, HD - 1.0));
    }
    for (let k = 0; k < n; k++) glass.tri(ctr, rim[k], rim[k + 1]);
    plain.push({ mat: "daylight", geo: glass });
    const samples = [P(0, y0 + R2 * 0.45, HD - 1), P(-a2 * 0.6, y0 + R2 * 0.25, HD - 1),
                     P(a2 * 0.6, y0 + R2 * 0.25, HD - 1), P(0, y0 + R2 * 0.9, HD - 1)];
    out.emitters.push({ pts, n: nW, L: mul(DAY, 3.2), samples });
  }
  [HD * 0.3, HD * 0.72].forEach((z) => {
    const y = SPR + R - 1;
    const g = new Geo();
    disc(g, P(0, y - 0.2, z), DOWN, 9, 20);
    plain.push({ mat: "metalWhite", geo: g });
    const gl = new Geo();
    disc(gl, P(0, y - 0.4, z), DOWN, 5, 20);
    plain.push({ mat: "lampFace", geo: gl });
    out.lights.push({ type: "down", p: P(0, y - 1, z), d: DOWN, m: 2, I: mul(WASH, Math.PI * 0.5 * y * y) });
  });
  const doorGlow = (y0) => {
    const pts = [P(-d.ow / 2, y0, 0.5), P(d.ow / 2, y0, 0.5), P(d.ow / 2, y0 + d.oh, 0.5), P(-d.ow / 2, y0 + d.oh, 0.5)];
    const samples = [P(0, y0 + d.oh / 2, 0.5), P(-d.ow / 3, y0 + d.oh * 0.25, 0.5), P(d.ow / 3, y0 + d.oh * 0.75, 0.5)];
    out.openings.push({ pts, n: D([0, 0, 1]), L: [0.6, 0.58, 0.55], samples, w: true });
  };
  doorGlow(0);
  doorGlow(FF);

  /* ---- beyond the far door: the lit room you'd arrive in ---- */
  const far = new Geo();
  const yF = d.kind === "stairdown" ? 0 : FF;
  const fq = [[-d.ow / 2 - 30, yF - 5], [d.ow / 2 + 30, yF - 5], [d.ow / 2 + 30, yF + d.oh + 30], [-d.ow / 2 - 30, yF + d.oh + 30]]
    .map(([x, y]) => far.v(P(x, y, -150), D([0, 0, 1]), [0, 0]));
  far.quad(fq[0], fq[1], fq[2], fq[3]);
  const ff = [[-d.ow / 2 - 30, -150], [d.ow / 2 + 30, -150], [d.ow / 2 + 30, 0], [-d.ow / 2 - 30, 0]]
    .map(([x, z]) => far.v(P(x, yF + 0.05, z), UP, [0, 0]));
  far.quad(ff[0], ff[1], ff[2], ff[3]);
  plain.push({ mat: "farRoom", geo: far });
  // and the reveal through the hall's front wall for the far door — only as
  // deep as the wall, or its edges stand out of the room's own wall face
  // (up in the vault, over the lower door)
  const RV = (w.inset || 0) + 1.5;
  const rv = new Geo();
  [[-d.ow / 2, 1], [d.ow / 2, -1]].forEach(([x, sg]) => {
    const q = [[0, 0], [-RV, 0], [-RV, d.oh], [0, d.oh]].map(([z, y]) =>
      rv.v(P(x, yF + y, z), D([sg, 0, 0]), [z / 100, y / 100]));
    rv.quad(q[0], q[1], q[2], q[3]);
  });
  const sq = [[-d.ow / 2, 0], [d.ow / 2, 0], [d.ow / 2, -RV], [-d.ow / 2, -RV]].map(([x, z]) =>
    rv.v(P(x, yF + d.oh, z), DOWN, [0, 0]));
  rv.quad(sq[0], sq[1], sq[2], sq[3]);
  plain.push({ mat: "plasterLit", geo: rv });

  S.forEach((sf) => { if (!sf.rho) sf.rho = PLASTER_RGB; });
  return { S, derived: [], plain, out, dims: s, frame: { H0, X, Z: Zd, yOff } };
}
