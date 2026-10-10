/* The light baker. Each room is lit once, on the visitor's machine, and the
   result is written into a light map — the way an architectural visualiser
   bakes a scene, only in milliseconds and in plain JavaScript.

   What reaches a point on a wall, the floor, the vault or a canvas:

   - DIRECT light, exactly: every spotlight (inverse square, its cone's soft
     edge, the angle it strikes at), every downlight, and every area of light
     — a frosted lunette, a skylight's opening, the bright doorway into the
     next room — through the analytic irradiance of a glowing polygon,
     clipped to the point's horizon. A framed painting stands in the way of
     its spotlight, so the wall below a frame and the top of a canvas under
     its rail fall into shadow.
   - BOUNCED light, approximately: the room is cut into half-metre patches,
     each patch's direct light is reflected (warm off the oak, off-white off
     the plaster, dimmer where paintings cover a wall) and passed around the
     room for a few rounds of radiosity. Every point then gathers what the
     patches send it. This is what fills the corners, warms the lower walls
     and lights a vault no lamp points at.

   The room's inside is (nearly) convex, so nothing is tested for visibility
   between patches; the reveals of the doorways, which aren't inside the box,
   are shaded from the wall beside them. */

const PATCH = 46;            // radiosity patch size, cm
const BOUNCES = 5;
const GRID_CM = 20;          // bounced and area light are gathered on this grid and interpolated
const PAD = 2;               // atlas padding around each surface, texels

/* ------------------------------------------------------------- vector ops */
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function nrm(a) { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function smoothstep(e0, e1, x) { const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); }

/* Does the segment p→q pass through the box? (slab test) */
function segHitsBox(p, q, b) {
  let t0 = 0, t1 = 1;
  for (let k = 0; k < 3; k++) {
    const d = q[k] - p[k];
    if (Math.abs(d) < 1e-9) {
      if (p[k] < b.min[k] || p[k] > b.max[k]) return false;
    } else {
      let ta = (b.min[k] - p[k]) / d, tb = (b.max[k] - p[k]) / d;
      if (ta > tb) { const t = ta; ta = tb; tb = t; }
      t0 = Math.max(t0, ta); t1 = Math.min(t1, tb);
      if (t0 > t1) return false;
    }
  }
  return t1 > 1e-4 && t0 < 1 - 1e-4;
}

/* Irradiance at p (normal n) from a Lambertian polygon of unit radiance:
   the polygon is clipped to the hemisphere above p, then summed edge by
   edge (Lambert's formula). */
function polyIrradiance(p, n, pts) {
  const rel = pts.map((q) => sub(q, p));
  // clip against the plane n·x > eps
  const eps = 0.01;
  const clipped = [];
  for (let k = 0; k < rel.length; k++) {
    const a = rel[k], b = rel[(k + 1) % rel.length];
    const da = dot(a, n) - eps, db = dot(b, n) - eps;
    if (da >= 0) clipped.push(a);
    if ((da >= 0) !== (db >= 0)) {
      const t = da / (da - db);
      clipped.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
    }
  }
  if (clipped.length < 3) return 0;
  let sum = 0;
  for (let k = 0; k < clipped.length; k++) {
    const a = nrm(clipped[k]), b = nrm(clipped[(k + 1) % clipped.length]);
    const c = Math.max(-1, Math.min(1, dot(a, b)));
    const th = Math.acos(c);
    const g = cross(a, b);
    const gl = Math.hypot(g[0], g[1], g[2]);
    if (gl < 1e-9) continue;
    sum += th * dot(n, g) / gl;
  }
  return Math.abs(sum) * 0.5;
}

/* ---------------------------------------------------------------- direct */
/* `parts` picks what to count: "point" (lamps), "area" (glowing polygons)
   or both. Lamps make the crisp things — a spotlight's pool, a frame's
   shadow — and are counted at every texel; areas of light vary slowly and
   are counted on the coarse grid with the bounced light. */
function makeDirect(out, which, parts) {
  const pickL = (L) => !which || which(L);
  const lights = parts === "area" ? [] : out.lights.filter(pickL);
  const emit = parts === "point" ? [] : out.emitters.concat(out.openings).filter(pickL);
  return function direct(p, n, occ, res) {
    let r = 0, g = 0, b = 0;
    for (let k = 0; k < lights.length; k++) {
      const L = lights[k];
      const lx = L.p[0] - p[0], ly = L.p[1] - p[1], lz = L.p[2] - p[2];
      const d2 = lx * lx + ly * ly + lz * lz;
      const d = Math.sqrt(d2);
      const cosS = (n[0] * lx + n[1] * ly + n[2] * lz) / d;
      if (cosS <= 0) continue;
      const cosA = -(L.d[0] * lx + L.d[1] * ly + L.d[2] * lz) / d;
      let f;
      if (L.type === "spot") {
        f = smoothstep(L.cosOut, L.cosIn, cosA);
        if (f <= 0) continue;
      } else {
        if (cosA <= 0) continue;
        f = Math.pow(cosA, L.m);
      }
      if (occ && occ.length) {
        // tested from a hair off the surface, so a face of an occluding
        // block never shadows itself
        const q = [p[0] + n[0] * 0.6, p[1] + n[1] * 0.6, p[2] + n[2] * 0.6];
        let hit = false;
        for (let o = 0; o < occ.length; o++) if (segHitsBox(q, L.p, occ[o])) { hit = true; break; }
        if (hit) continue;
      }
      const e = f * cosS / Math.max(d2, 100);
      r += L.I[0] * e; g += L.I[1] * e; b += L.I[2] * e;
    }
    for (let k = 0; k < emit.length; k++) {
      const E = emit[k];
      // only the side the light faces
      if (dot(sub(p, E.pts[0]), E.n) <= 0.5) continue;
      let f = polyIrradiance(p, n, E.pts);
      if (f <= 0) continue;
      // inside the stair hall the flights stand in the way: the share of the
      // window or doorway a point can see, from a few points across it
      if (occ && occ.length && E.samples) {
        const q = [p[0] + n[0] * 1.2, p[1] + n[1] * 1.2, p[2] + n[2] * 1.2];
        let seen = 0;
        for (const sp of E.samples) {
          let hit = false;
          for (let o = 0; o < occ.length; o++) if (segHitsBox(q, sp, occ[o])) { hit = true; break; }
          if (!hit) seen++;
        }
        f *= seen / E.samples.length;
        if (f <= 0) continue;
      }
      r += E.L[0] * f; g += E.L[1] * f; b += E.L[2] * f;
    }
    res[0] = r; res[1] = g; res[2] = b;
    return res;
  };
}

/* ------------------------------------------------------------- radiosity */
function makePatches(surfs) {
  const P = [];
  // a big hall gets bigger patches, so no room costs much more than another:
  // the bounced light is smooth, and about this many patches carry it well
  const area = surfs.reduce((t, s) => t + (s.patch === false || s.special ? 0 : s.U * s.V), 0);
  const size = Math.max(PATCH, Math.sqrt(area / 750));
  surfs.forEach((s) => {
    if (s.patch === false || s.special) return;
    const nu = Math.max(1, Math.round(s.U / size)), nv = Math.max(1, Math.round(s.V / size));
    const du = s.U / nu, dv = s.V / nv;
    for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) {
      const u = (i + 0.5) * du, v = (j + 0.5) * dv;
      if (s.inside && !s.inside(u, v)) continue;
      const p = s.P(u, v), n = s.N(u, v);
      // area: the parametrisation's stretch, by finite differences
      const pu = s.P(Math.min(s.U, u + 1), v), pv = s.P(u, Math.min(s.V, v + 1));
      const J = Math.hypot(...cross(sub(pu, p), sub(pv, p)));
      let rho = s.rhoHigh && v > s.rhoHigh.v ? s.rhoHigh.rho : s.rho;
      if (s.hung) {
        // paintings take the wall's place in the bounce
        let cov = 0;
        for (let a = 0; a < 4; a++) for (let b = 0; b < 4; b++) {
          const uu = (i + (a + 0.5) / 4) * du, vv = (j + (b + 0.5) / 4) * dv;
          if (s.hung.some((h) => uu > h[0] && uu < h[1] && vv > h[2] && vv < h[3])) cov++;
        }
        const f = cov / 16;
        rho = [rho[0] * (1 - f) + 0.2 * f, rho[1] * (1 - f) + 0.18 * f, rho[2] * (1 - f) + 0.15 * f];
      }
      P.push({ p, n, A: du * dv * (J || 1), rho, wall: s.kind === "wall" });
    }
  });
  return P;
}

function formFactors(P) {
  const n = P.length;
  const F = new Float32Array(n * n);
  for (let i = 0; i < n; i++) {
    const pi = P[i].p, ni = P[i].n;
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const pj = P[j].p, nj = P[j].n;
      const dx = pj[0] - pi[0], dy = pj[1] - pi[1], dz = pj[2] - pi[2];
      const d2 = dx * dx + dy * dy + dz * dz;
      const d = Math.sqrt(d2) || 1;
      const ci = (ni[0] * dx + ni[1] * dy + ni[2] * dz) / d;
      if (ci <= 0) continue;
      const cj = -(nj[0] * dx + nj[1] * dy + nj[2] * dz) / d;
      if (cj <= 0) continue;
      F[i * n + j] = ci * cj * P[j].A / (Math.PI * d2 + P[j].A);
    }
  }
  return F;
}

/* Direct light at every patch, then BOUNCES rounds of passing it round.
   Returns each patch's outgoing radiance and the irradiance it received. */
function solveRadiosity(P, F, direct) {
  const n = P.length;
  const E0 = new Float32Array(n * 3);
  const res = [0, 0, 0];
  for (let i = 0; i < n; i++) {
    direct(P[i].p, P[i].n, null, res);
    E0[3 * i] = res[0]; E0[3 * i + 1] = res[1]; E0[3 * i + 2] = res[2];
  }
  let L = new Float32Array(n * 3), Ln = new Float32Array(n * 3);
  const Ein = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) for (let c = 0; c < 3; c++) L[3 * i + c] = P[i].rho[c] * E0[3 * i + c] / Math.PI;
  for (let it = 0; it < BOUNCES; it++) {
    for (let i = 0; i < n; i++) {
      let r = 0, g = 0, b = 0;
      const row = i * n;
      for (let j = 0; j < n; j++) {
        const f = F[row + j];
        if (f === 0) continue;
        r += f * L[3 * j]; g += f * L[3 * j + 1]; b += f * L[3 * j + 2];
      }
      // E_ind = Σ π L_j F_ij; L_i = ρ/π (E0 + E_ind)
      const rho = P[i].rho;
      Ein[3 * i] = E0[3 * i] + Math.PI * r;
      Ein[3 * i + 1] = E0[3 * i + 1] + Math.PI * g;
      Ein[3 * i + 2] = E0[3 * i + 2] + Math.PI * b;
      Ln[3 * i] = rho[0] * Ein[3 * i] / Math.PI;
      Ln[3 * i + 1] = rho[1] * Ein[3 * i + 1] / Math.PI;
      Ln[3 * i + 2] = rho[2] * Ein[3 * i + 2] / Math.PI;
    }
    const t = L; L = Ln; Ln = t;
  }
  return { L, Ein };
}

/* Indirect irradiance at a point, gathered from every patch. */
function makeIndirect(P, L) {
  const n = P.length;
  const px = new Float32Array(n), py = new Float32Array(n), pz = new Float32Array(n);
  const nx = new Float32Array(n), ny = new Float32Array(n), nz = new Float32Array(n);
  const A = new Float32Array(n);
  for (let j = 0; j < n; j++) {
    px[j] = P[j].p[0]; py[j] = P[j].p[1]; pz[j] = P[j].p[2];
    nx[j] = P[j].n[0]; ny[j] = P[j].n[1]; nz[j] = P[j].n[2];
    A[j] = P[j].A;
  }
  return function indirect(p, nn, res) {
    let r = 0, g = 0, b = 0;
    for (let j = 0; j < n; j++) {
      const dx = px[j] - p[0], dy = py[j] - p[1], dz = pz[j] - p[2];
      const d2 = dx * dx + dy * dy + dz * dz;
      const d = Math.sqrt(d2) || 1;
      const ci = (nn[0] * dx + nn[1] * dy + nn[2] * dz) / d;
      if (ci <= 0) continue;
      const cj = -(nx[j] * dx + ny[j] * dy + nz[j] * dz) / d;
      if (cj <= 0) continue;
      const f = ci * cj * A[j] / (Math.PI * d2 + A[j]) * Math.PI;
      r += f * L[3 * j]; g += f * L[3 * j + 1]; b += f * L[3 * j + 2];
    }
    res[0] = r; res[1] = g; res[2] = b;
    return res;
  };
}

/* ----------------------------------------------------------------- atlas */
function pack(surfs) {
  const items = surfs.map((s) => {
    const nu = Math.max(2, Math.ceil(s.U / s.t)), nv = Math.max(2, Math.ceil(s.V / s.t));
    return { s, nu, nv, w: nu + 2 * PAD, h: nv + 2 * PAD };
  });
  const area = items.reduce((t, it) => t + it.w * it.h, 0);
  let W = 256;
  while (W * W < area * 1.25 && W < 4096) W *= 2;
  items.sort((p, q) => q.h - p.h);
  let x = 0, y = 0, rowH = 0;
  items.forEach((it) => {
    if (it.w > W) W = Math.min(4096, 1 << Math.ceil(Math.log2(it.w)));
    if (x + it.w > W) { x = 0; y += rowH; rowH = 0; }
    it.x = x; it.y = y;
    x += it.w;
    rowH = Math.max(rowH, it.h);
  });
  const H = 1 << Math.ceil(Math.log2(Math.max(4, y + rowH)));
  return { items, W, H };
}

/* float32 → float16 bits */
const f32 = new Float32Array(1), u32 = new Uint32Array(f32.buffer);
function toHalf(v) {
  f32[0] = v;
  const x = u32[0];
  const sign = (x >>> 16) & 0x8000;
  let e = ((x >>> 23) & 0xff) - 127 + 15;
  let m = x & 0x7fffff;
  if (e <= 0) {
    if (e < -10) return sign;
    m = (m | 0x800000) >> (1 - e);
    return sign | (m >> 13);
  }
  if (e >= 31) return sign | 0x7c00;
  return sign | (e << 10) | (m >> 13);
}

/* ------------------------------------------------------------------ bake */

/* Bake one room. Returns the atlas (half-float RGBA texels), its size, and a
   per-surface mapping from surface (u, v) to atlas uv. */
/* The room's general light — downlights, the vault's wash, windows and
   skylights — is balanced the way a lighting designer would: scaled so the
   walls, on average, receive the same general light in every room, whatever
   mix of sources it has. The art spotlights and the glow of the next room
   through a doorway are left as they are, on top: a spot lifts its picture
   without dimming the room around it. Light adds, so each set is solved
   once and the balance is a single factor. */
const WALL_TARGET = Math.PI * 0.62;

export function bakeRoom(arch) {
  const out = arch.out;
  const surfs = arch.S;
  const general = (L) => L.type === "down" || (!L.type && !L.w);
  const P = makePatches(surfs);
  const F = formFactors(P);
  const solG = solveRadiosity(P, F, makeDirect(out, general));
  const solR = solveRadiosity(P, F, makeDirect(out, (L) => !general(L)));
  let sumG = 0, area = 0;
  P.forEach((pt, i) => {
    if (!pt.wall) return;
    sumG += (0.3 * solG.Ein[3 * i] + 0.59 * solG.Ein[3 * i + 1] + 0.11 * solG.Ein[3 * i + 2]) * pt.A;
    area += pt.A;
  });
  const k = area && sumG > 0 ? Math.max(0.05, Math.min(3, WALL_TARGET / (sumG / area))) : 1;
  out.lights.forEach((L) => { if (general(L)) L.I = L.I.map((v) => v * k); });
  out.emitters.forEach((E) => { E.L = E.L.map((v) => v * k); });
  const Lall = new Float32Array(solG.L.length);
  for (let q = 0; q < Lall.length; q++) Lall[q] = k * solG.L[q] + solR.L[q];
  const direct = makeDirect(out);
  const directPoint = makeDirect(out, null, "point");
  const directArea = makeDirect(out, null, "area");
  const indirect = makeIndirect(P, Lall);
  const { items, W, H } = pack(surfs);
  const data = new Float32Array(W * H * 4);
  const res = [0, 0, 0], res2 = [0, 0, 0];

  // the full lighting at any point: direct + bounced
  const full = (p, n, occ, o) => {
    direct(p, n, occ, res);
    indirect(p, n, res2);
    o[0] = res[0] + res2[0]; o[1] = res[1] + res2[1]; o[2] = res[2] + res2[2];
    return o;
  };

  items.forEach((it) => {
    const s = it.s;
    const nu = it.nu, nv = it.nv;
    const du = s.U / nu, dv = s.V / nv;
    const px = (i, j, rgb) => {
      const k = ((it.y + PAD + j) * W + (it.x + PAD + i)) * 4;
      data[k] = rgb[0]; data[k + 1] = rgb[1]; data[k + 2] = rgb[2]; data[k + 3] = 1;
    };
    if (s.special) {
      bakeReveal(s, it, du, dv, full, px);
    } else {
      // indirect on a coarse grid, interpolated; direct per texel
      const gs = Math.max(1, Math.round(GRID_CM / s.t));
      const gu = Math.ceil(nu / gs) + 1, gv = Math.ceil(nv / gs) + 1;
      const grid = new Float32Array(gu * gv * 3);
      const res3 = [0, 0, 0];
      const near0 = s.occ && s.occ.length && s.occAll ? s.occ : null;
      for (let a = 0; a < gu; a++) for (let b = 0; b < gv; b++) {
        const u = Math.min(s.U, (a * gs + 0.5) * du), v = Math.min(s.V, (b * gs + 0.5) * dv);
        const p = s.P(u, v), n = s.N(u, v);
        indirect(p, n, res2);
        directArea(p, n, near0, res3);
        const k = (a * gv + b) * 3;
        grid[k] = res2[0] + res3[0]; grid[k + 1] = res2[1] + res3[1]; grid[k + 2] = res2[2] + res3[2];
      }
      const near = s.occ && s.occ.length ? s.occ : null;
      for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) {
        const u = (i + 0.5) * du, v = (j + 0.5) * dv;
        const p = s.P(u, v), n = s.N(u, v);
        // only the frames near this texel can shadow it
        let occ = null;
        if (near && s.occAll) occ = near;
        else if (near) {
          occ = near.filter((b) =>
            p[0] > b.min[0] - 60 && p[0] < b.max[0] + 60 &&
            p[1] > b.min[1] - 90 && p[1] < b.max[1] + 30 &&
            p[2] > b.min[2] - 60 && p[2] < b.max[2] + 60);
        }
        directPoint(p, n, occ, res);
        const fa = i / gs, fb = j / gs;
        const a0 = Math.min(gu - 2, Math.floor(fa)), b0 = Math.min(gv - 2, Math.floor(fb));
        const ta = Math.min(1, fa - a0), tb = Math.min(1, fb - b0);
        let ind0 = 0, ind1 = 0, ind2 = 0;
        for (let c = 0; c < 3; c++) {
          const g00 = grid[(a0 * gv + b0) * 3 + c], g10 = grid[((a0 + 1) * gv + b0) * 3 + c];
          const g01 = grid[(a0 * gv + b0 + 1) * 3 + c], g11 = grid[((a0 + 1) * gv + b0 + 1) * 3 + c];
          const val = (g00 * (1 - ta) + g10 * ta) * (1 - tb) + (g01 * (1 - ta) + g11 * ta) * tb;
          if (c === 0) ind0 = val; else if (c === 1) ind1 = val; else ind2 = val;
        }
        // the soft contact shade a frame casts on the wall right round it
        const aoS = s.aoFn ? s.aoFn(u, v) : 1;   // a stair's step lines darken the lamps' light too
        let ao = aoS;
        if (s.hung) {
          for (const h of s.hung) {
            const dx = Math.max(h[0] - u, 0, u - h[1]), dy = Math.max(h[2] - v, 0, v - h[3]);
            const dd = Math.hypot(dx, dy);
            if (dd < 7) ao = Math.min(ao, 0.62 + 0.38 * (dd / 7));
          }
        }
        // and the room's floor and wall corners gather a touch of occlusion
        if (s.kind === "wall") {
          const edge = Math.min(u, s.U - u);
          ao *= 0.86 + 0.14 * smoothstep(0, 45, edge);
          ao *= 0.9 + 0.1 * smoothstep(0, 30, v);
        } else if (s.kind === "floor") {
          const edge = Math.min(u, s.U - u, v, s.V - v);
          ao *= 0.82 + 0.18 * smoothstep(0, 40, edge);
        }
        res[0] = res[0] * aoS + ind0 * ao; res[1] = res[1] * aoS + ind1 * ao; res[2] = res[2] * aoS + ind2 * ao;
        px(i, j, res);
      }
    }
    // padding: copy the edge texels outward
    for (let j = -PAD; j < nv + PAD; j++) for (let i = -PAD; i < nu + PAD; i++) {
      if (i >= 0 && i < nu && j >= 0 && j < nv) continue;
      const ci = Math.max(0, Math.min(nu - 1, i)), cj = Math.max(0, Math.min(nv - 1, j));
      const ks = ((it.y + PAD + cj) * W + (it.x + PAD + ci)) * 4;
      const kd = ((it.y + PAD + j) * W + (it.x + PAD + i)) * 4;
      data[kd] = data[ks]; data[kd + 1] = data[ks + 1]; data[kd + 2] = data[ks + 2]; data[kd + 3] = 1;
    }
    s.atlas = { x: it.x + PAD, y: it.y + PAD, nu, nv, W, H };
  });

  const half = new Uint16Array(W * H * 4);
  for (let k = 0; k < half.length; k++) half[k] = toHalf(data[k]);
  return { half, W, H, full };
}

/* Map a surface's (u, v) to its atlas uv. */
export function atlasUV(s) {
  const a = s.atlas;
  return (u, v) => [(a.x + (u / s.U) * a.nu) / a.W, (a.y + (v / s.V) * a.nv) / a.H];
}

/* The reveals: lit by the room through the opening, so each texel takes the
   light of the wall face beside it and dims with depth into the wall — the
   jambs a little, the soffit or intrados (facing the floor) much more, and
   most of all midway through a passage, furthest from both rooms. */
function bakeReveal(s, it, du, dv, full, px) {
  const sp = s.special, w = sp.w;
  const o = [0, 0, 0];
  const rgb = [0, 0, 0];
  const T = sp.T;
  const through = T > 30;
  for (let i = 0; i < it.nu; i++) for (let j = 0; j < it.nv; j++) {
    const u = (i + 0.5) * du, v = (j + 0.5) * dv;
    let fp, depth;
    if (sp.reveal === "jamb") {
      depth = u;
      const side = s.N(0, 0);       // facing into the opening: step the other way onto the wall
      fp = [w.O[0] + w.a[0] * sp.u - side[0] * 6 + w.n[0] * 2, v, w.O[2] + w.a[2] * sp.u - side[2] * 6 + w.n[2] * 2];
    } else if (sp.reveal === "intrados") {
      depth = u;
      const p = s.P(0, v), nn = s.N(0, v);
      fp = [p[0] - nn[0] * 6 + w.n[0] * 2, p[1] - nn[1] * 6, p[2] - nn[2] * 6 + w.n[2] * 2];
    } else if (sp.reveal === "soffit") {
      depth = v;
      const p = s.P(u, 0);
      fp = [p[0] + w.n[0] * 2, p[1] + 6, p[2] + w.n[2] * 2];
    } else {
      depth = v;
      const p = s.P(u, 0);
      fp = [p[0] + w.n[0] * 8, 0.5, p[2] + w.n[2] * 8];
    }
    const t = Math.max(0, Math.min(1, depth / T));
    const mid = through ? Math.sin(Math.PI * t) : t;
    full(fp, sp.reveal === "floor" ? [0, 1, 0] : w.n, null, o);
    let f;
    if (sp.reveal === "jamb") f = 0.84 - 0.26 * mid;
    else if (sp.reveal === "floor") f = 0.9 - 0.22 * mid;
    else if (sp.reveal === "intrados") {
      const th = v / (s.V / Math.PI) ;
      const facingDown = Math.sin(th);
      f = (0.8 - 0.32 * facingDown) - 0.18 * mid;
    } else f = 0.5 - 0.16 * mid;
    rgb[0] = o[0] * f; rgb[1] = o[1] * f; rgb[2] = o[2] * f;
    px(i, j, rgb);
  }
}
