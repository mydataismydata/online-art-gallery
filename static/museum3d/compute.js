/* Everything about a room that doesn't need the GPU: its architecture, its
   baked light, and its meshes as plain typed arrays — the work a Web
   Worker does (bakeworker.js) so walking never stutters while the rooms
   ahead are being lit. The renderer only turns the arrays into buffers. */

import { buildRoom } from "./arch.js";
import { bakeRoom, atlasUV } from "./bake.js";
import { buildStairHall } from "./stairs.js";
import { mergeArrays } from "./geo.js";

/* One baked piece (a room or a stair hall): its light map, its baked
   surfaces merged per material, borrowed-light trim, probe-lit fittings and
   the invisible faces a tap lands on. */
function packPiece(arch, bk) {
  const baked = {}, plain = {};
  const add = (bag, k, a) => { (bag[k] = bag[k] || []).push(a); };
  arch.S.forEach((s) => { if (s.mat !== "canvas" && s.geo) add(baked, s.mat, s.geo.arrays(atlasUV(s))); });
  arch.derived.forEach((d) => add(baked, d.mat + "Tint", d.geo.arrays(atlasUV(d.ref))));
  const picks = [];
  arch.plain.forEach((pl) => {
    if (pl.pick) picks.push({ g: pl.geo.arrays(), pick: pl.pick });
    else add(plain, pl.mat, pl.geo.arrays());
  });
  const merge = (bag) => {
    const o = {};
    Object.keys(bag).forEach((k) => { o[k] = mergeArrays(bag[k]); });
    return o;
  };
  return { lm: { half: bk.half, W: bk.W, H: bk.H }, baked: merge(baked), plain: merge(plain), picks };
}

export function computeRoom(room, opts, stair) {
  const arch = buildRoom(room, opts);
  const bk = bakeRoom(arch);
  const piece = packPiece(arch, bk);
  const fr = arch.fr, c = arch.c;
  piece.box = {
    x0: fr.x0, x1: fr.x1, z0: fr.z0, z1: fr.z1,
    top: c.type === "skylight" ? c.Hc + c.well : c.Hs + (c.R || 0),
  };
  piece.wallRGB = arch.S.find((s) => s.kind === "wall").rho;
  piece.arts = arch.out.arts.map((pa) => ({
    art: pa.art,
    canvas: pa.canvas.geo.arrays(atlasUV(pa.canvas)),
    frame: pa.frame.arrays(atlasUV(pa.frameLight)),
  }));
  piece.signs = arch.out.signs.map((s) => ({ p: s.p, n: s.n }));
  // where the room's lamps are, for the reflection probe (the hidden
  // uplights excepted: nobody sees those)
  piece.lamps = arch.out.lights.filter((L) => L.d[1] < 0.5).map((L) => L.p);
  // the ground beyond the walls the room also takes up: its corridors here,
  // its stair halls below
  piece.extents = arch.out.extents;
  piece.halls = (arch.out.stairs || []).map((st) => {
    const hall = buildStairHall(st, stair, opts.floorTile);
    const hp = packPiece(hall, bakeRoom(hall));
    hp.door = { pi: st.d.pi, end: st.d.end, kind: st.d.kind };
    const { H0, X, Z } = hall.frame, { HW, HD } = hall.dims;
    const xs = [], zs = [];
    [-HW / 2, HW / 2].forEach((x) => [0, HD].forEach((z) => {
      xs.push(H0[0] + X[0] * x + Z[0] * z);
      zs.push(H0[2] + X[2] * x + Z[2] * z);
    }));
    hp.box = { x0: Math.min(...xs), x1: Math.max(...xs), z0: Math.min(...zs), z1: Math.max(...zs) };
    return hp;
  });
  return piece;
}

/* The buffers to hand over (not copy) when a worker posts a room back. */
export function transferList(piece) {
  const out = new Set();
  const arrs = (o) => Object.values(o).forEach((a) => {
    ["position", "normal", "uv", "uv1", "color", "index"].forEach((k) => { if (a[k]) out.add(a[k].buffer); });
  });
  const pieceBufs = (p) => {
    out.add(p.lm.half.buffer);
    arrs(p.baked);
    arrs(p.plain);
    p.picks.forEach((pk) => arrs({ g: pk.g }));
  };
  pieceBufs(piece);
  piece.arts.forEach((a) => arrs({ c: a.canvas, f: a.frame }));
  piece.halls.forEach(pieceBufs);
  return [...out];
}
