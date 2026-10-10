/* Materials. Everything is three.js's MeshStandardMaterial — physically
   based, so plaster, oak, limestone, gilt and varnish each answer the light
   the way the real stuff does — with one patch applied to all of them:

   - A surface that carries a baked light map takes ALL of its diffuse light
     from that map. three.js would otherwise add the environment's diffuse
     glow on top and light the room twice; the environment is kept for
     reflections only.
   - Reflections are box-projected: the room's probe is a cube captured from
     its middle, and a reflected ray is first run out to the room's walls and
     then looked up from the probe, so a painting's reflection lies on the
     floor in front of the painting rather than smeared toward the middle.

   Each room keeps its own probe (its box, its capture point and its cube),
   and every material in it points at the same three uniforms, so swapping a
   probe in is one assignment per material and never a recompile. */

import * as THREE from "../vendor/three/three.module.min.js";

const VERT_DECL = "#include <common>\nvarying vec3 vBoxWorld;";
const VERT_BODY = "#include <worldpos_vertex>\nvBoxWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;";

const FRAG_DECL = /* glsl */ `#include <common>
varying vec3 vBoxWorld;
uniform vec3 uBoxMin;
uniform vec3 uBoxMax;
uniform vec3 uProbePos;
vec3 boxProject(vec3 dir) {
  vec3 d = normalize(dir);
  vec3 safe = mix(vec3(1e-5), d, step(1e-5, abs(d)));
  vec3 t1 = (uBoxMax - vBoxWorld) / safe;
  vec3 t2 = (uBoxMin - vBoxWorld) / safe;
  vec3 tf = max(t1, t2);
  float t = max(0.0, min(min(tf.x, tf.y), tf.z));
  return (vBoxWorld + d * t) - uProbePos;
}`;

const IBL_IRR = "iblIrradiance += getIBLIrradiance( geometryNormal );";
const IBL_IRR_PATCHED = "#ifndef USE_LIGHTMAP\n" + IBL_IRR + "\n#endif";
const REFLECT = "reflectVec = transformDirectionByInverseViewMatrix( reflectVec, viewMatrix );";
const REFLECT_PATCHED = REFLECT + "\nreflectVec = boxProject( reflectVec );";

function patch(material, probe) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uBoxMin = probe.uMin;
    shader.uniforms.uBoxMax = probe.uMax;
    shader.uniforms.uProbePos = probe.uPos;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", VERT_DECL)
      .replace("#include <worldpos_vertex>", VERT_BODY);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", FRAG_DECL)
      .replace("#include <lights_fragment_maps>",
        THREE.ShaderChunk.lights_fragment_maps.replace(IBL_IRR, IBL_IRR_PATCHED))
      .replace("#include <envmap_physical_pars_fragment>",
        THREE.ShaderChunk.envmap_physical_pars_fragment.replace(REFLECT, REFLECT_PATCHED));
  };
  material.customProgramCacheKey = () => "mu-boxproj-1";
  return material;
}

/* A room's reflection probe: the box reflections are projected onto, where
   the cube was captured, and the cube itself (the neutral studio until the
   room's own capture lands). */
export function makeProbe(env) {
  return {
    uMin: { value: new THREE.Vector3(-1e5, -1e5, -1e5) },
    uMax: { value: new THREE.Vector3(1e5, 1e5, 1e5) },
    uPos: { value: new THREE.Vector3() },
    env: env,
    mats: [],
  };
}

export function setProbeEnv(probe, env) {
  probe.env = env;
  probe.mats.forEach((m) => { m.envMap = env; });
}

/* sRGB hex (as typed in the arrange screen) → linear albedo triple, held
   below the reflectance of real white paint so a pure #ffffff doesn't glow. */
export function hexToLinear(hex) {
  const c = new THREE.Color(hex);           // ColorManagement converts sRGB → linear
  return [c.r, c.g, c.b].map((v) => Math.min(0.86, v));
}

/* The house plaster: a warm white, reflectance about 0.8 — what the
   reference galleries' walls read as under their light. */
export const PLASTER_RGB = [0.76, 0.75, 0.72];
export const OAK_RGB = [0.42, 0.28, 0.15];
export const STONE_RGB = [0.58, 0.54, 0.47];
export const FLOOR_RGB = [0.36, 0.25, 0.15];
export const SOFFIT_RGB = [0.075, 0.058, 0.046];

/* The material set for one room. `lm` is the room's baked light map (a
   DataTexture read through uv1), `probe` its reflection probe, `tex` the
   shared procedural textures, `wall` the room's wall albedo. */
export function roomMaterials(tex, lm, probe, wall, frames) {
  const std = (o) => {
    const m = patch(new THREE.MeshStandardMaterial(o), probe);
    m.envMap = probe.env;
    probe.mats.push(m);
    return m;
  };
  const col = (rgb) => new THREE.Color(rgb[0], rgb[1], rgb[2]);
  const baked = lm ? { lightMap: lm, lightMapIntensity: 1 } : {};
  const M = {
    plaster: std(Object.assign({
      color: col(wall || PLASTER_RGB), roughness: 0.93, metalness: 0,
      normalMap: tex.plasterNormal.texture, normalScale: new THREE.Vector2(0.18, 0.18),
      envMapIntensity: 0.35,
    }, baked)),
    // vaults and ceilings stay white whatever the walls are painted
    ceiling: std(Object.assign({
      color: col(PLASTER_RGB), roughness: 0.93, metalness: 0,
      normalMap: tex.plasterNormal.texture, normalScale: new THREE.Vector2(0.18, 0.18),
      envMapIntensity: 0.35,
    }, baked)),
    floor: std(Object.assign({
      color: 0xffffff, map: tex.floorMap.texture,
      roughnessMap: tex.floorRough.texture, roughness: 1,
      normalMap: tex.floorNormal.texture, normalScale: new THREE.Vector2(0.55, 0.55),
      metalness: 0, envMapIntensity: 1.0,
    }, baked)),
    stone: std(Object.assign({
      color: 0xffffff, map: tex.stoneMap.texture, roughness: 0.62, metalness: 0,
      envMapIntensity: 0.6,
    }, baked)),
    oak: std(Object.assign({
      color: 0xffffff, map: tex.oakMap.texture, roughness: 0.5, metalness: 0,
      envMapIntensity: 0.8,
    }, baked)),
    soffit: std(Object.assign({
      color: col(SOFFIT_RGB), roughness: 0.88, metalness: 0, envMapIntensity: 0.3,
    }, baked)),
    // trim that borrows a wall's light, tinted per face by which way it turns
    oakTint: std(Object.assign({
      color: 0xffffff, map: tex.oakMap.texture, roughness: 0.5, metalness: 0,
      envMapIntensity: 0.8, vertexColors: true,
    }, baked)),
    stoneTint: std(Object.assign({
      color: 0xffffff, map: tex.stoneMap.texture, roughness: 0.62, metalness: 0,
      envMapIntensity: 0.6, vertexColors: true,
    }, baked)),
    ceilingTint: std(Object.assign({
      color: col(PLASTER_RGB), roughness: 0.93, metalness: 0,
      envMapIntensity: 0.35, vertexColors: true,
    }, baked)),
    // Unbaked pieces lit by the probe alone: fixtures, rails, door leaves.
    oakLit: std({ color: 0xffffff, map: tex.oakMap.texture, roughness: 0.48, metalness: 0 }),
    metalWhite: std({ color: 0xe9e7e2, roughness: 0.38, metalness: 0.15 }),
    steel: std({ color: 0xb9b8b4, roughness: 0.3, metalness: 1 }),
    placardBack: std({ color: 0xe8e2d4, roughness: 0.85, metalness: 0 }),
    // the two photographed gilt frames on their mouldings: the photograph
    // carries the gilding and carving, the light map the lamp, the probe a
    // little burnish
    frame0: std(Object.assign({ map: frames[0], color: 0xffffff, roughness: 0.42, metalness: 0.3,
                                vertexColors: true, envMapIntensity: 0.9 }, baked)),
    frame1: std(Object.assign({ map: frames[1], color: 0xffffff, roughness: 0.46, metalness: 0.3,
                                vertexColors: true, envMapIntensity: 0.9 }, baked)),
    leafOak: std({ color: new THREE.Color(0.5, 0.36, 0.25), map: tex.oakMap.texture, roughness: 0.45, metalness: 0 }),
    // a passage dimming into the dark, its fall-off carried in the vertices
    passWall: new THREE.MeshBasicMaterial({ color: col(wall || PLASTER_RGB), vertexColors: true }),
    passFloor: new THREE.MeshBasicMaterial({ map: tex.floorMap.texture, vertexColors: true }),
    red: std({ color: 0xa3120f, roughness: 0.45, metalness: 0 }),
    // Light itself: glowing faces that tone mapping rolls off to white.
    lampFace: new THREE.MeshBasicMaterial({ color: new THREE.Color(6, 5.6, 5.0) }),
    daylight: new THREE.MeshBasicMaterial({ color: new THREE.Color(2.4, 2.5, 2.6) }),
    led: new THREE.MeshBasicMaterial({ color: new THREE.Color(5, 5, 5) }),
    shadowVoid: new THREE.MeshBasicMaterial({ color: 0x050403 }),
    // a lit room glimpsed through a far doorway of the stair hall
    farRoom: new THREE.MeshBasicMaterial({ color: new THREE.Color(0.62, 0.6, 0.56) }),
    plasterLit: std({ color: col(wall || PLASTER_RGB), roughness: 0.93, metalness: 0 }),
  };
  return M;
}

/* A painting's canvas: its picture under the room's baked light, a little
   sheen of varnish from the probe. */
export function canvasMaterial(map, lm, probe) {
  const m = patch(new THREE.MeshStandardMaterial({
    map: map, color: 0xffffff, roughness: 0.55, metalness: 0,
    envMapIntensity: 0.22,
    lightMap: lm, lightMapIntensity: 1,
  }), probe);
  m.envMap = probe.env;
  probe.mats.push(m);
  return m;
}

export function placardMaterial(map, probe) {
  const m = patch(new THREE.MeshStandardMaterial({
    map: map, color: 0xffffff, roughness: 0.8, metalness: 0, envMapIntensity: 1.25,
  }), probe);
  m.envMap = probe.env;
  probe.mats.push(m);
  return m;
}
