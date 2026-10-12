// 畫風：Ginza 專案同款「3D 動畫化」質感 — PBR（MeshStandard）+ 環境反射 + 柔和陰影、無墨線。
// 舊版三階卡通 + 墨線看起來像積木，2026-10 改掉；描邊材質保留給需要時 opt-in。
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js';
import { mergeGeometries, mergeVertices } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { PALETTE, type ColorKey, type Model3D, type Part } from './dsl';

/** 霧面略帶光澤（像黏土動畫／Pixar 室內），roughness 可個別覆寫 */
export function softMaterial(params: THREE.MeshStandardMaterialParameters = {}): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ roughness: 0.72, metalness: 0, ...params });
}

export const colorOf = (c: ColorKey): THREE.Color => new THREE.Color(PALETTE[c]);

const OUTLINE_VERTEX = `
uniform float width;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vec3 n = normalize(normalMatrix * normal);
  mv.xyz += n * width * clamp(-mv.z, 0.4, 30.0);
  gl_Position = projectionMatrix * mv;
}`;
const OUTLINE_FRAGMENT = `
uniform vec3 color;
void main() {
  gl_FragColor = vec4(color, 1.0);
  #include <colorspace_fragment>
}`;

let sharedOutline: THREE.ShaderMaterial | null = null;

/** 反轉外殼描邊：只畫背面、沿法線外推（依距離放大，畫面線寬大致固定） */
export function outlineMaterial(): THREE.ShaderMaterial {
  if (sharedOutline) return sharedOutline;
  sharedOutline = new THREE.ShaderMaterial({
    uniforms: { color: { value: new THREE.Color(PALETTE.ink) }, width: { value: 0.0024 } },
    vertexShader: OUTLINE_VERTEX,
    fragmentShader: OUTLINE_FRAGMENT,
    side: THREE.BackSide,
  });
  return sharedOutline;
}

function partGeometry(part: Part): THREE.BufferGeometry {
  let g: THREE.BufferGeometry;
  switch (part.s) {
    case 'box': {
      const [w, h, d] = part.size;
      const round = Math.min(part.round ?? 0, Math.min(w, h, d) / 2 - 1e-4);
      g = round > 0.004 ? new RoundedBoxGeometry(w, h, d, 2, round) : new THREE.BoxGeometry(w, h, d);
      break;
    }
    case 'cyl': g = new THREE.CylinderGeometry(part.r1, part.r0, part.h, part.seg ?? 20); break;
    case 'sph': g = new THREE.SphereGeometry(part.rad, 28, 20); break;
    case 'cone': g = new THREE.ConeGeometry(part.rad, part.h, part.seg ?? 16); break;
  }
  if (part.s === 'sph' && part.scale) g.scale(...part.scale);
  if (part.r) {
    const [rx, ry, rz] = part.r.map(THREE.MathUtils.degToRad);
    g.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(rx, ry, rz)));
  }
  g.translate(...part.p);
  const flat = g.index ? g.toNonIndexed() : g;
  if (flat !== g) g.dispose();
  flat.deleteAttribute('uv');
  const color = colorOf(part.c);
  const count = flat.attributes.position.count;
  const colors = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) colors.set([color.r, color.g, color.b], i * 3);
  flat.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return flat;
}

const modelMaterial = (() => {
  let m: THREE.MeshStandardMaterial | null = null;
  return () => (m ??= softMaterial({ vertexColors: true }));
})();

/** 積木 → 一個 Group（本體；outline: true 時加描邊外殼），全部共用材質 */
export function buildModel(model: Model3D): THREE.Group {
  const parts = model.parts.map(partGeometry);
  const geometry = mergeGeometries(parts);
  for (const p of parts) p.dispose();
  if (!geometry) throw new Error('模型合併失敗');
  geometry.computeBoundingSphere();
  const group = new THREE.Group();
  const body = new THREE.Mesh(geometry, modelMaterial());
  body.castShadow = true;
  body.receiveShadow = true;
  group.add(body);
  if (model.outline === true) {
    const hull = new THREE.BufferGeometry();
    hull.setAttribute('position', geometry.attributes.position.clone());
    const outline = mergeVertices(hull, 1e-3);
    hull.dispose();
    outline.computeVertexNormals();
    group.add(new THREE.Mesh(outline, outlineMaterial()));
  }
  return group;
}
