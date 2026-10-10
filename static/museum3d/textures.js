/* The museum's surfaces, made rather than fetched: the oak boards, the oak of
   the stair and the woodwork, the limestone of the door surrounds and the
   plaster's faint unevenness are each drawn once by a fragment shader into a
   tiling texture on the visitor's own GPU, and the signs — exit, placard,
   floor marker — are lettered on 2-D canvases. Nothing here is downloaded,
   so the walk costs the server no more than the paintings themselves. */

import * as THREE from "../vendor/three/three.module.min.js";

/* Shared GLSL: hashing and value noise, enough for wood and stone. */
const NOISE = /* glsl */ `
float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash21(i), hash21(i + vec2(1, 0)), u.x),
             mix(hash21(i + vec2(0, 1)), hash21(i + vec2(1, 1)), u.x), u.y);
}
// Noise that tiles with period per (in lattice cells), so the texture repeats seamlessly.
float tnoise(vec2 p, vec2 per) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  vec2 a = mod(i, per), b = mod(i + 1.0, per);
  return mix(mix(hash21(a), hash21(vec2(b.x, a.y)), u.x),
             mix(hash21(vec2(a.x, b.y)), hash21(b), u.x), u.y);
}
float tfbm(vec2 p, vec2 per) {
  float s = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) { s += a * tnoise(p, per); p *= 2.0; per *= 2.0; a *= 0.5; }
  return s;
}
`;

const VERT = /* glsl */ `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;

/* Render one fragment shader into a square texture. Albedo goes into an sRGB
   target (the shader writes linear colour; the hardware encodes it), data
   maps — roughness, normals — into a linear one. Mipmaps and anisotropy are
   on, since these surfaces are seen raking away from the eye. */
function bake(renderer, frag, size, opts) {
  const rt = new THREE.WebGLRenderTarget(size, size, {
    type: THREE.UnsignedByteType,
    colorSpace: opts.srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace,
    generateMipmaps: true,
    minFilter: THREE.LinearMipmapLinearFilter,
    magFilter: THREE.LinearFilter,
    wrapS: THREE.RepeatWrapping,
    wrapT: THREE.RepeatWrapping,
    // set here or not at all: a render target's sampling is fixed when it's made
    anisotropy: renderer.capabilities.getMaxAnisotropy(),
    depthBuffer: false,
  });
  const mat = new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: NOISE + frag,
    uniforms: opts.uniforms || {},
    depthTest: false,
    depthWrite: false,
  });
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), mat);
  const scene = new THREE.Scene();
  scene.add(quad);
  const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const prevTarget = renderer.getRenderTarget();
  const prevTone = renderer.toneMapping;
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.setRenderTarget(rt);
  renderer.render(scene, cam);
  renderer.setRenderTarget(prevTarget);
  renderer.toneMapping = prevTone;
  quad.geometry.dispose();
  mat.dispose();
  return rt;
}

/* ---------------------------------------------------------------- oak boards

   Strip oak, the boards about 14 cm wide and laid in a running bond along the
   tile's v axis: each column of boards has its own length (a half, a third or
   a quarter of the tile, so the repeat stays seamless) and its own offset.
   Every board picks its own tone — honey to tobacco, the spread a real floor
   of mixed boards has — and carries streaky long grain warped by noise, fine
   pores, and a hairline of shadow at each joint. The tile is FLOOR_TILE cm
   square. */
export const FLOOR_TILE = 336;            // 24 boards of 14 cm across

const BOARDS = /* glsl */ `
uniform float uTile;     // cm the tile covers
const float BW = 14.0;   // board width, cm
const int NB = 4;        // boards per column per tile, of random lengths
// For a point in cm, the board it falls on: id, position along/across it.
void board(vec2 p, out vec2 id, out vec2 local, out vec2 size) {
  float col = floor(p.x / BW);
  float cw = mod(col, uTile / BW);
  float lens[NB];
  float sum = 0.0;
  for (int k = 0; k < NB; k++) {
    float l = 0.5 + hash11(cw * 13.71 + float(k) * 3.17 + 0.5);
    lens[k] = l;
    sum += l;
  }
  float off = hash11(cw * 5.31 + 2.7) * uTile;
  float y = mod(p.y + off, uTile);
  float acc = 0.0;
  id = vec2(cw, float(NB - 1));
  local = vec2(p.x - col * BW, 0.0);
  size = vec2(BW, uTile);
  for (int k = 0; k < NB; k++) {
    float L = lens[k] / sum * uTile;
    if (y < acc + L || k == NB - 1) {
      id = vec2(cw, float(k));
      local = vec2(p.x - col * BW, y - acc);
      size = vec2(BW, L);
      return;
    }
    acc += L;
  }
}
float edgeDist(vec2 local, vec2 size) {
  return min(min(local.x, size.x - local.x), min(local.y, size.y - local.y));
}
`;

const FLOOR_ALBEDO = BOARDS + /* glsl */ `
varying vec2 vUv;
void main() {
  vec2 p = vUv * uTile;
  vec2 id, local, size;
  board(p, id, local, size);
  float r1 = hash21(id), r2 = hash21(id + 17.3), r3 = hash21(id + 41.9);
  // board tone, in linear light: pale honey to a mellow amber, most boards
  // between, a few at either end — the spread of a real strip-oak floor
  vec3 pale = vec3(0.45, 0.29, 0.155), deep = vec3(0.28, 0.165, 0.08);
  float t = 0.12 + 0.76 * (0.5 + 0.5 * sin((r1 - 0.5) * 3.14159));
  vec3 c = mix(pale, deep, t);
  c *= 0.95 + 0.1 * r2;
  c.r *= 0.98 + 0.04 * r3;
  // long grain: streaks along the board, warped, varying in strength per board
  vec2 g = vec2(local.x + r2 * 40.0, local.y + r3 * 300.0);
  float warp = tfbm(vec2(g.x * 0.12, g.y * 0.012), vec2(64.0, 64.0));
  float streak = sin((g.x * 1.1 + warp * 9.0) * 1.7) * 0.5 + 0.5;
  streak = pow(streak, 3.0);
  float fine = tfbm(vec2(g.x * 2.6, g.y * 0.09), vec2(256.0, 256.0));
  c *= 1.0 - 0.13 * streak * (0.55 + 0.6 * r3) - 0.08 * (fine - 0.5);
  // the occasional figure: soft darker flecks across the grain (medullary rays)
  float fleck = smoothstep(0.78, 0.95, vnoise(vec2(g.x * 0.9, g.y * 0.35) + id * 13.0));
  c *= 1.0 - 0.08 * fleck * r2;
  // slow tonal drift along the board's length
  c *= 0.95 + 0.1 * vnoise(vec2(id.x * 3.0, local.y * 0.02 + id.y * 5.0));
  // the joint: a hairline of shadow and a slightly darker eased edge
  float e = edgeDist(local, size);
  c *= mix(0.5, 1.0, smoothstep(0.0, 0.2, e));
  c *= mix(0.9, 1.0, smoothstep(0.2, 0.9, e));
  gl_FragColor = vec4(c, 1.0);
}
`;

const FLOOR_ROUGH = BOARDS + /* glsl */ `
varying vec2 vUv;
void main() {
  vec2 p = vUv * uTile;
  vec2 id, local, size;
  board(p, id, local, size);
  float r = hash21(id + 5.0);
  float fine = tfbm(vec2(local.x * 1.8 + r * 20.0, local.y * 0.08), vec2(128.0, 128.0));
  float rough = 0.25 + 0.08 * r + 0.07 * (fine - 0.5);
  float e = edgeDist(local, size);
  rough = mix(0.62, rough, smoothstep(0.0, 0.35, e));
  // MeshStandardMaterial reads roughness from green
  gl_FragColor = vec4(1.0, rough, 0.0, 1.0);
}
`;

/* The joints as a normal map: each board's eased edge rolls the surface
   away, so the reflections in the floor break at every board as they do on a
   real one. */
const FLOOR_NORMAL = BOARDS + /* glsl */ `
varying vec2 vUv;
float height(vec2 p) {
  vec2 id, local, size;
  board(p, id, local, size);
  float e = edgeDist(local, size);
  float bevel = smoothstep(0.0, 0.5, e);
  // a faint cup across each board and a little unevenness
  float cup = 0.04 * cos((local.x / size.x - 0.5) * 3.14159);
  return bevel * 0.35 + cup + 0.02 * hash21(id);
}
void main() {
  vec2 p = vUv * uTile;
  float d = uTile / 2048.0;
  float hx = height(p + vec2(d, 0.0)) - height(p - vec2(d, 0.0));
  float hy = height(p + vec2(0.0, d)) - height(p - vec2(0.0, d));
  vec3 n = normalize(vec3(-hx / (2.0 * d), -hy / (2.0 * d), 1.0));
  gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
}
`;

/* ------------------------------------------------------------- solid oak

   One plank's worth of oak for the woodwork: treads and risers, handrails,
   skirting, the frames of the lunettes. Straight grain along u. */
const OAK_ALBEDO = /* glsl */ `
varying vec2 vUv;
void main() {
  vec2 g = vUv * vec2(60.0, 240.0);   // cm: 60 across, 240 along
  vec3 c = vec3(0.42, 0.28, 0.15);
  float warp = tfbm(vec2(g.x * 0.15, g.y * 0.02), vec2(9.0, 4.8) * 2.0);
  float streak = pow(sin((g.x * 1.3 + warp * 8.0) * 1.6) * 0.5 + 0.5, 3.0);
  float fine = tfbm(vec2(g.x * 3.0, g.y * 0.1), vec2(180.0, 24.0));
  c *= 1.0 - 0.16 * streak - 0.10 * (fine - 0.5);
  c *= 0.95 + 0.1 * tnoise(vec2(g.x * 0.05, g.y * 0.01), vec2(3.0, 2.4));
  gl_FragColor = vec4(c, 1.0);
}
`;

/* -------------------------------------------------------------- limestone

   Lioz-like limestone for the door surrounds: pale warm grey, soft clouds of
   tone, a few faint veins, and the fine speckle of a honed face. The tile
   is 120 cm. */
const STONE_ALBEDO = /* glsl */ `
varying vec2 vUv;
void main() {
  vec2 p = vUv * 8.0;
  vec3 base = vec3(0.60, 0.56, 0.49);
  float cloud = tfbm(p, vec2(8.0));
  float cloud2 = tfbm(p * 2.3 + 7.0, vec2(8.0) * 2.0);
  vec3 c = base * (0.90 + 0.16 * cloud + 0.06 * cloud2);
  float v = abs(tfbm(p * 1.5 + vec2(cloud * 2.0, 0.0), vec2(12.0)) - 0.5);
  c *= 1.0 - 0.10 * (1.0 - smoothstep(0.0, 0.025, v));
  float speck = tnoise(vUv * 900.0, vec2(900.0));
  c *= 0.97 + 0.05 * speck;
  gl_FragColor = vec4(c, 1.0);
}
`;

/* --------------------------------------------------------------- plaster

   Barely-there unevenness for the walls and vaults: a normal map of soft
   trowel undulation so a raking light finds the surface hand-made. Tile
   200 cm. */
const PLASTER_NORMAL = /* glsl */ `
varying vec2 vUv;
float h(vec2 p) { return tfbm(p, vec2(16.0)) + 0.35 * tnoise(p * 9.0, vec2(144.0)); }
void main() {
  vec2 p = vUv * 16.0;
  float d = 16.0 / 512.0;
  float hx = h(p + vec2(d, 0.0)) - h(p - vec2(d, 0.0));
  float hy = h(p + vec2(0.0, d)) - h(p - vec2(0.0, d));
  vec3 n = normalize(vec3(-hx * 0.9, -hy * 0.9, 1.0));
  gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
}
`;

/* Every procedural texture the museum uses, drawn once per walk. `small`
   halves the sizes for phones, whose GPU memory is the scarce thing. */
export function makeTextures(renderer, small) {
  const S = small ? 1024 : 2048;
  const tile = { uTile: { value: FLOOR_TILE } };
  const t = {
    floorMap: bake(renderer, FLOOR_ALBEDO, S, { srgb: true, uniforms: tile }),
    floorRough: bake(renderer, FLOOR_ROUGH, S / 2, { uniforms: tile }),
    floorNormal: bake(renderer, FLOOR_NORMAL, S, { uniforms: tile }),
    oakMap: bake(renderer, OAK_ALBEDO, 1024, { srgb: true }),
    stoneMap: bake(renderer, STONE_ALBEDO, small ? 512 : 1024, { srgb: true }),
    plasterNormal: bake(renderer, PLASTER_NORMAL, 512, {}),
  };
  return t;
}

/* -------------------------------------------------------------- canvases */

function canvasTexture(cv, srgb) {
  const tex = new THREE.CanvasTexture(cv);
  if (srgb !== false) tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  return tex;
}

/* The green emergency-exit sign (ISO 7010 E002 style): a running figure, the
   doorway it runs for, and an arrow. `dir` is -1 for an arrow pointing left,
   1 for right, 0 for straight on (up). Drawn white on safety green. */
export function exitSignTexture(dir) {
  const W = 512, H = 256;
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const g = cv.getContext("2d");
  g.fillStyle = "#0a8a3e";
  g.fillRect(0, 0, W, H);
  g.strokeStyle = "rgba(255,255,255,0.9)";
  g.lineWidth = 8;
  g.strokeRect(10, 10, W - 20, H - 20);
  g.fillStyle = "#ffffff";
  g.strokeStyle = "#ffffff";
  g.lineCap = "round";
  g.lineJoin = "round";
  // the arrow, in its own square
  const ax = dir > 0 ? W - 128 : 128, ay = H / 2;
  g.save();
  g.translate(ax, ay);
  if (dir > 0) g.rotate(Math.PI);
  else if (dir === 0) g.rotate(Math.PI / 2);
  g.beginPath();
  g.moveTo(-70, 0); g.lineTo(-10, -55); g.lineTo(-10, -22); g.lineTo(70, -22);
  g.lineTo(70, 22); g.lineTo(-10, 22); g.lineTo(-10, 55); g.closePath();
  g.fill();
  g.restore();
  // the doorway: an open white frame on the arrow's far side
  const dx = dir > 0 ? 70 : W - 190;
  g.lineWidth = 12;
  g.strokeRect(dx + 60, 46, 76, 164);
  // the running figure, heading for the door
  g.save();
  g.translate(dx + 40, 0);
  if (dir > 0) { g.translate(120, 0); g.scale(-1, 1); }
  g.lineWidth = 20;
  g.beginPath(); g.arc(78, 62, 17, 0, Math.PI * 2); g.fill();
  g.beginPath();
  g.moveTo(66, 92); g.lineTo(52, 150);             // torso
  g.moveTo(66, 98); g.lineTo(92, 116); g.lineTo(110, 102);   // fore arm
  g.moveTo(64, 100); g.lineTo(40, 112); g.lineTo(26, 132);   // back arm
  g.moveTo(52, 150); g.lineTo(80, 176); g.lineTo(76, 214);   // front leg
  g.moveTo(52, 150); g.lineTo(30, 182); g.lineTo(8, 188);    // back leg
  g.stroke();
  g.restore();
  return canvasTexture(cv);
}

/* The wall placard: artist, title and date, medium and size, set the way the
   old CSS label set them — drawn at 24 px per centimetre so it stays crisp
   to a visitor's nose. */
export function placardTexture(lb, wcm, hcm) {
  const K = 24;
  const W = Math.round(wcm * K), H = Math.round(hcm * K);
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const g = cv.getContext("2d");
  g.fillStyle = "#efe9dc";
  g.fillRect(0, 0, W, H);
  const pad = 1.6 * K;
  const maxW = W - 2 * pad;
  const fit = (txt, font) => {
    g.font = font;
    if (g.measureText(txt).width <= maxW) return txt;
    let s = txt;
    while (s.length > 1 && g.measureText(s + "…").width > maxW) s = s.slice(0, -1);
    return s + "…";
  };
  g.textBaseline = "alphabetic";
  let y = pad + 1.6 * K;
  g.fillStyle = "#2c261e";
  const fa = "700 " + (1.55 * K) + "px Helvetica, Arial, sans-serif";
  g.fillText(fit(lb.artist || "Unknown artist", fa), pad, y);
  y += 2.0 * K;
  g.fillStyle = "#3a332a";
  const ft = "italic 400 " + (1.5 * K) + "px Georgia, 'Times New Roman', serif";
  g.font = ft;
  // the title may wrap to two lines; the date rides on the last
  const words = String(lb.title || "Untitled").split(/\s+/);
  const lines = [];
  let cur = "";
  for (const w of words) {
    const tryS = cur ? cur + " " + w : w;
    if (g.measureText(tryS).width > maxW && cur) { lines.push(cur); cur = w; }
    else cur = tryS;
  }
  if (cur) lines.push(cur);
  const shown = lines.slice(0, 2);
  if (lines.length > 2) shown[1] = fit(shown[1] + " " + lines.slice(2).join(" "), ft);
  shown.forEach((ln, k) => {
    g.font = ft;
    g.fillText(fit(ln, ft), pad, y);
    if (k === shown.length - 1 && lb.date) {
      const wv = g.measureText(fit(ln, ft)).width;
      g.font = "400 " + (1.5 * K) + "px Georgia, 'Times New Roman', serif";
      const dt = ", " + lb.date;
      if (wv + g.measureText(dt).width <= maxW) g.fillText(dt, pad + wv, y);
    }
    y += 1.85 * K;
  });
  if (lb.line3) {
    g.fillStyle = "#6d6353";
    const fm = "400 " + (1.2 * K) + "px Helvetica, Arial, sans-serif";
    g.fillText(fit(lb.line3, fm), pad, Math.min(H - pad * 0.8, y + 0.4 * K));
  }
  return canvasTexture(cv);
}

/* The marker on the floor before each doorway: a pale ring with a chevron
   pointing through, on transparent ground. */
export function markerTexture() {
  const S = 256;
  const cv = document.createElement("canvas");
  cv.width = S; cv.height = S;
  const g = cv.getContext("2d");
  const c = S / 2;
  const grad = g.createRadialGradient(c, c, 0, c, c, c);
  grad.addColorStop(0, "rgba(255,252,246,0.22)");
  grad.addColorStop(0.62, "rgba(255,252,246,0.22)");
  grad.addColorStop(0.66, "rgba(255,252,246,0.75)");
  grad.addColorStop(0.78, "rgba(255,252,246,0.75)");
  grad.addColorStop(0.82, "rgba(255,252,246,0.18)");
  grad.addColorStop(1, "rgba(255,252,246,0)");
  g.fillStyle = grad;
  g.beginPath(); g.arc(c, c, c, 0, Math.PI * 2); g.fill();
  g.strokeStyle = "rgba(255,253,248,0.95)";
  g.lineWidth = 16;
  g.lineCap = "round";
  g.lineJoin = "round";
  g.beginPath();
  g.moveTo(c - 44, c + 22); g.lineTo(c, c - 26); g.lineTo(c + 44, c + 22);
  g.stroke();
  return canvasTexture(cv);
}

/* A tiny neutral studio environment, so metal and gloss have something to
   reflect before a room's own probe has been captured. */
export function neutralEnvironment(renderer) {
  const scene = new THREE.Scene();
  const box = new THREE.Mesh(new THREE.BoxGeometry(10, 6, 10),
    new THREE.MeshBasicMaterial({ color: 0x8a8378, side: THREE.BackSide }));
  box.position.y = 2;
  scene.add(box);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(10, 10),
    new THREE.MeshBasicMaterial({ color: 0x6b4a2c }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -0.99;
  scene.add(floor);
  const panel = (x, y, z, w, h, ry, rx, k) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(k, k, k * 0.96) }));
    m.position.set(x, y, z);
    m.rotation.set(rx || 0, ry || 0, 0);
    scene.add(m);
  };
  panel(0, 4.9, 0, 6, 6, 0, Math.PI / 2, 3.0);
  panel(-4.9, 2.5, 0, 6, 2, Math.PI / 2, 0, 1.4);
  panel(4.9, 2.5, 0, 6, 2, -Math.PI / 2, 0, 1.4);
  const pm = new THREE.PMREMGenerator(renderer);
  const rt = pm.fromScene(scene, 0.04);
  pm.dispose();
  scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  return rt.texture;
}
