/* Geometry plumbing shared by the builders: a triangle accumulator that
   carries every channel the museum uses (position, normal, texture uv, the
   light map's surface coordinates, a per-vertex tint), and the small vector
   helpers the builders are written in. Coordinates are three.js's: x east, y
   up, z south; one unit is one centimetre. */

import * as THREE from "../vendor/three/three.module.min.js";

export class Geo {
  constructor() {
    this.p = []; this.n = []; this.uv = []; this.uvL = []; this.c = []; this.i = [];
    this.count = 0;
  }
  /* p, n: [x,y,z]; uv, uvL: [u,v]; c: tint [r,g,b] (default white). */
  v(p, n, uv, uvL, c) {
    this.p.push(p[0], p[1], p[2]);
    this.n.push(n[0], n[1], n[2]);
    this.uv.push(uv ? uv[0] : 0, uv ? uv[1] : 0);
    this.uvL.push(uvL ? uvL[0] : 0, uvL ? uvL[1] : 0);
    const k = c || WHITE;
    this.c.push(k[0], k[1], k[2]);
    return this.count++;
  }
  /* A triangle, wound so its face points along the vertices' normals. */
  tri(a, b, c) {
    const P = this.p, N = this.n;
    const ax = P[3 * a], ay = P[3 * a + 1], az = P[3 * a + 2];
    const e1x = P[3 * b] - ax, e1y = P[3 * b + 1] - ay, e1z = P[3 * b + 2] - az;
    const e2x = P[3 * c] - ax, e2y = P[3 * c + 1] - ay, e2z = P[3 * c + 2] - az;
    const fx = e1y * e2z - e1z * e2y, fy = e1z * e2x - e1x * e2z, fz = e1x * e2y - e1y * e2x;
    const nx = N[3 * a] + N[3 * b] + N[3 * c];
    const ny = N[3 * a + 1] + N[3 * b + 1] + N[3 * c + 1];
    const nz = N[3 * a + 2] + N[3 * b + 2] + N[3 * c + 2];
    if (fx * nx + fy * ny + fz * nz < 0) this.i.push(a, c, b);
    else this.i.push(a, b, c);
  }
  quad(a, b, c, d) { this.tri(a, b, c); this.tri(a, c, d); }
  get empty() { return this.i.length === 0; }

  /* Hand over as plain typed arrays (what a worker can post back):
     position, normal and uv; uv1 — each vertex's surface coordinates turned
     into light-map uvs by uvMap — when one is given; color only for tinted
     pieces. */
  arrays(uvMap) {
    const o = {
      position: new Float32Array(this.p), normal: new Float32Array(this.n),
      uv: new Float32Array(this.uv),
      index: this.count > 65535 ? new Uint32Array(this.i) : new Uint16Array(this.i),
    };
    if (uvMap) {
      const uv1 = new Float32Array(this.count * 2);
      for (let k = 0; k < this.count; k++) {
        const r = uvMap(this.uvL[2 * k], this.uvL[2 * k + 1], k);
        uv1[2 * k] = r[0]; uv1[2 * k + 1] = r[1];
      }
      o.uv1 = uv1;
    }
    if (this.tinted) o.color = new Float32Array(this.c);
    return o;
  }
}
const WHITE = [1, 1, 1];

/* Merge Geo.arrays() results that share channels into one set. */
export function mergeArrays(list) {
  if (list.length === 1) return list[0];
  const names = ["position", "normal", "uv", "uv1", "color"].filter((k) => list[0][k]);
  const verts = list.reduce((t, a) => t + a.position.length / 3, 0);
  const out = {};
  names.forEach((k) => {
    const arr = new Float32Array(list.reduce((t, a) => t + a[k].length, 0));
    let o = 0;
    list.forEach((a) => { arr.set(a[k], o); o += a[k].length; });
    out[k] = arr;
  });
  const idx = verts > 65535 ? new Uint32Array(list.reduce((t, a) => t + a.index.length, 0))
                            : new Uint16Array(list.reduce((t, a) => t + a.index.length, 0));
  let o = 0, base = 0;
  list.forEach((a) => {
    for (let k = 0; k < a.index.length; k++) idx[o + k] = a.index[k] + base;
    o += a.index.length;
    base += a.position.length / 3;
  });
  out.index = idx;
  return out;
}

/* A BufferGeometry from Geo.arrays()-shaped typed arrays. */
export function geometryFrom(a) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(a.position, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(a.normal, 3));
  g.setAttribute("uv", new THREE.BufferAttribute(a.uv, 2));
  if (a.uv1) g.setAttribute("uv1", new THREE.BufferAttribute(a.uv1, 2));
  if (a.color) g.setAttribute("color", new THREE.BufferAttribute(a.color, 3));
  g.setIndex(new THREE.BufferAttribute(a.index, 1));
  g.computeBoundingSphere();
  return g;
}

/* ---- vectors as plain arrays ---- */
export const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
export const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const len = (a) => Math.hypot(a[0], a[1], a[2]);
export const norm = (a) => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
export const lerp3 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
/* p + a*u + b*v (+ c*w) */
export const comb = (p, a, u, b, v, c, w) => [
  p[0] + a[0] * u + b[0] * v + (c ? c[0] * w : 0),
  p[1] + a[1] * u + b[1] * v + (c ? c[1] * w : 0),
  p[2] + a[2] * u + b[2] * v + (c ? c[2] * w : 0),
];
export const UP = [0, 1, 0];
export const DOWN = [0, -1, 0];

/* An axis-aligned box as six quads, into g (no light-map coordinates):
   for small fittings lit by the probe. */
export function box(g, cx, cy, cz, sx, sy, sz, tint) {
  const hx = sx / 2, hy = sy / 2, hz = sz / 2;
  const F = [
    [[1, 0, 0], [0, 0, -1], [0, 1, 0], hx, sz, sy],
    [[-1, 0, 0], [0, 0, 1], [0, 1, 0], hx, sz, sy],
    [[0, 1, 0], [1, 0, 0], [0, 0, -1], hy, sx, sz],
    [[0, -1, 0], [1, 0, 0], [0, 0, 1], hy, sx, sz],
    [[0, 0, 1], [1, 0, 0], [0, 1, 0], hz, sx, sy],
    [[0, 0, -1], [-1, 0, 0], [0, 1, 0], hz, sx, sy],
  ];
  F.forEach(([n, a, b, d, w, h]) => {
    const c = [cx + n[0] * d, cy + n[1] * d, cz + n[2] * d];
    const q = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([s, t]) =>
      g.v(comb(c, a, s * w / 2, b, t * h / 2), n, [(s + 1) / 2 * w / 100, (t + 1) / 2 * h / 100], null, tint));
    g.quad(q[0], q[1], q[2], q[3]);
  });
}

/* An oriented box: centre c, unit axes ax/ay/az (orthonormal), half sizes. */
export function obox(g, c, ax, ay, az, hx, hy, hz, tint) {
  // each face: its normal, the two axes across it, and the half extents
  const faces = [
    [ax, az, ay, hx, hz, hy], [mul(ax, -1), az, ay, hx, hz, hy],
    [az, ax, ay, hz, hx, hy], [mul(az, -1), ax, ay, hz, hx, hy],
    [ay, ax, az, hy, hx, hz], [mul(ay, -1), ax, az, hy, hx, hz],
  ];
  faces.forEach(([n, t, b, en, et, eb]) => {
    const fc = add(c, mul(n, en));
    const q = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([s, u]) =>
      g.v(comb(fc, t, s * et, b, u * eb), n, [(s + 1) / 2 * et / 50, (u + 1) / 2 * eb / 50], null, tint));
    g.quad(q[0], q[1], q[2], q[3]);
  });
}

/* A cylinder along unit axis `ax` from p0, length L, radius r, nSeg sides,
   capped at both ends. */
export function cylinder(g, p0, ax, L, r, nSeg, capEnd) {
  const ref = Math.abs(ax[1]) < 0.9 ? UP : [1, 0, 0];
  const e1 = norm(cross(ax, ref)), e2 = cross(ax, e1);
  const p1 = comb(p0, ax, L, [0, 0, 0], 0);
  const ring0 = [], ring1 = [];
  for (let k = 0; k <= nSeg; k++) {
    const a = (k / nSeg) * Math.PI * 2;
    const d = add(mul(e1, Math.cos(a)), mul(e2, Math.sin(a)));
    ring0.push(g.v(add(p0, mul(d, r)), d, [k / nSeg, 0]));
    ring1.push(g.v(add(p1, mul(d, r)), d, [k / nSeg, 1]));
  }
  for (let k = 0; k < nSeg; k++) g.quad(ring0[k], ring0[k + 1], ring1[k + 1], ring1[k]);
  const cap = (pc, n, lst) => {
    const c = g.v(pc, n, [0.5, 0.5]);
    const ids = [];
    for (let k = 0; k <= nSeg; k++) {
      const a = (k / nSeg) * Math.PI * 2;
      const d = add(mul(e1, Math.cos(a)), mul(e2, Math.sin(a)));
      ids.push(g.v(add(pc, mul(d, r)), n, [0.5 + 0.5 * Math.cos(a), 0.5 + 0.5 * Math.sin(a)]));
    }
    for (let k = 0; k < nSeg; k++) g.tri(c, ids[k], ids[k + 1]);
  };
  cap(p0, mul(ax, -1));
  if (capEnd !== false) cap(p1, ax);
}

/* A flat disc facing n. */
export function disc(g, c, n, r, nSeg) {
  const ref = Math.abs(n[1]) < 0.9 ? UP : [1, 0, 0];
  const e1 = norm(cross(n, ref)), e2 = cross(n, e1);
  const ctr = g.v(c, n, [0.5, 0.5]);
  const ids = [];
  for (let k = 0; k <= nSeg; k++) {
    const a = (k / nSeg) * Math.PI * 2;
    ids.push(g.v(add(c, add(mul(e1, r * Math.cos(a)), mul(e2, r * Math.sin(a)))), n,
      [0.5 + 0.5 * Math.cos(a), 0.5 + 0.5 * Math.sin(a)]));
  }
  for (let k = 0; k < nSeg; k++) g.tri(ctr, ids[k], ids[k + 1]);
}
