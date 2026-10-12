// 人物モデルの共通部品：材質・基本形状・顔（目／眉／口）とその動き。
// 面試官（interviewer.ts）と受付（receptionist.ts）が同じ顔の作りと表情ロジックを使う。
// 頭の原点 = 首の付け根。顔は本地 +z 向き。
import * as THREE from 'three';
import { colorOf, softMaterial } from './toon';
import { type ColorKey } from './dsl';

export type Emotion = 'neutral' | 'smile' | 'think' | 'serious' | 'nervous' | 'relieved';
export type V3 = [number, number, number];

// 肌膚・頭髮是霧面、西裝略帶布料光澤、眼睛和皮鞋是亮面
const ROUGH: Partial<Record<ColorKey, number>> = {
  skin: 0.7, skinShade: 0.75, hair: 0.55, hairBrown: 0.5, suit: 0.66, tie: 0.45, shirt: 0.6, navy: 0.66,
};
const mats = new Map<string, THREE.MeshStandardMaterial>();
export function mat(c: ColorKey, roughness = ROUGH[c] ?? 0.75): THREE.MeshStandardMaterial {
  const key = `${c}:${roughness}`;
  let m = mats.get(key);
  if (!m) { m = softMaterial({ color: colorOf(c), roughness }); mats.set(key, m); }
  return m;
}
export const glossy = (color: string) => softMaterial({ color, roughness: 0.18 });

export function mesh(geo: THREE.BufferGeometry, material: THREE.Material, p: V3, scale?: V3, rot?: V3): THREE.Mesh {
  const m = new THREE.Mesh(geo, material);
  m.position.set(...p);
  if (scale) m.scale.set(...scale);
  if (rot) m.rotation.set(...rot);
  m.castShadow = true;
  return m;
}
export const ball = (r: number, c: ColorKey, p: V3, scale?: V3, rot?: V3) =>
  mesh(new THREE.SphereGeometry(r, 32, 24), mat(c), p, scale, rot);
export const capsule = (r: number, len: number) => new THREE.CapsuleGeometry(r, len, 6, 16);

/** 上眼瞼角度：負 = 往後收（睜眼），π/2 = 闔上 */
const LID_OPEN: Record<Emotion, number> = {
  neutral: -1.15, smile: -0.95, think: -0.85, serious: -0.75, nervous: -1.4, relieved: -0.95,
};
const LID_CLOSED = 1.45;

/** 眼睛：眼白＋虹膜＋瞳孔＋高光（可轉向），上眼瞼是膚色半球、眨眼時往前蓋 */
function buildEye(sx: number, iris: string): { eye: THREE.Group; gaze: THREE.Group; lid: THREE.Mesh } {
  const eye = new THREE.Group();
  eye.position.set(sx * 0.052, 0.118, 0.128);
  eye.rotation.y = sx * 0.28;
  const shape = new THREE.Group();
  shape.scale.set(1.0, 0.9, 0.5);
  eye.add(shape);
  shape.add(mesh(new THREE.SphereGeometry(0.026, 24, 18), glossy('#fbf8f3'), [0, 0, 0]));
  const gaze = new THREE.Group();
  gaze.add(mesh(new THREE.SphereGeometry(0.019, 20, 14), glossy(iris), [0, 0, 0.018], [1, 1.05, 0.55]));
  gaze.add(mesh(new THREE.SphereGeometry(0.01, 14, 10), glossy('#120e0e'), [0, 0, 0.027], [1, 1.05, 0.5]));
  const spark = new THREE.Mesh(new THREE.SphereGeometry(0.0055, 10, 8), new THREE.MeshBasicMaterial({ color: '#ffffff' }));
  spark.position.set(-0.006, 0.007, 0.033);
  gaze.add(spark);
  shape.add(gaze);
  const lid = mesh(new THREE.SphereGeometry(0.028, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), mat('skin'), [0, 0, 0]);
  shape.add(lid);
  return { eye, gaze, lid };
}

export interface FaceOptions {
  iris?: string;
  brow?: ColorKey;
  /** 眉の太さ（女性はやや細め） */
  browWidth?: number;
  lips?: string;
}

/** 顔：頭殼・耳・鼻・腮紅・口・目・眉を組み、表情（口の開閉・眉・まばたき・視線）を動かす */
export class Face {
  private mouth: THREE.Mesh;
  private smileArc: THREE.Mesh;
  private gazes: THREE.Group[] = [];
  private lids: THREE.Mesh[] = [];
  private brows: THREE.Mesh[] = [];
  private nextBlink = 1 + Math.random() * 2;
  private blinkUntil = 0;
  private lidAngle = LID_OPEN.neutral;

  constructor(h: THREE.Group, opts: FaceOptions = {}) {
    // 頭殼：上寬下窄的蛋形＋下顎
    h.add(ball(0.13, 'skin', [0, 0.13, 0], [0.92, 1.05, 0.98]));
    h.add(ball(0.1, 'skin', [0, 0.06, 0.025], [0.95, 0.85, 0.95]));
    for (const sx of [-1, 1]) {
      h.add(ball(0.028, 'skin', [sx * 0.118, 0.11, -0.005], [0.45, 1, 0.75]));
      h.add(ball(0.016, 'skinShade', [sx * 0.124, 0.11, 0.0], [0.35, 0.8, 0.6]));
    }
    h.add(ball(0.018, 'skinShade', [0, 0.085, 0.142], [0.9, 1.1, 0.9]));
    h.add(ball(0.016, 'skin', [0, 0.092, 0.14], [0.85, 1.4, 0.95]));
    const blush = softMaterial({ color: colorOf('blush'), roughness: 0.9, transparent: true, opacity: 0.45 });
    for (const sx of [-1, 1]) h.add(mesh(new THREE.SphereGeometry(0.02, 16, 12), blush, [sx * 0.074, 0.068, 0.11], [1.3, 0.7, 0.4], [0, sx * 0.5, 0]));
    this.mouth = mesh(new THREE.SphereGeometry(0.02, 20, 12), softMaterial({ color: '#7a3434', roughness: 0.6 }), [0, 0.04, 0.128], [1.1, 0.2, 0.45]);
    h.add(this.mouth);
    this.smileArc = mesh(new THREE.TorusGeometry(0.02, 0.0035, 8, 20, Math.PI), softMaterial({ color: opts.lips ?? '#8a4a40' }), [0, 0.05, 0.131], undefined, [0.25, 0, Math.PI]);
    h.add(this.smileArc);
    for (const sx of [-1, 1]) {
      const { eye, gaze, lid } = buildEye(sx, opts.iris ?? '#4a3226');
      h.add(eye);
      this.gazes.push(gaze);
      this.lids.push(lid);
      const brow = mesh(capsule(opts.browWidth ?? 0.0065, 0.034), mat(opts.brow ?? 'hair'), [sx * 0.054, 0.163, 0.136], undefined, [0, sx * 0.25, Math.PI / 2]);
      h.add(brow);
      this.brows.push(brow);
    }
  }

  /** look = (左右, 上下) の視線角。k = 表情変化の補間係数 */
  update(t: number, talking: boolean, emotion: Emotion, look: THREE.Vector2, k: number): void {
    this.updateMouth(t, talking, emotion);
    this.updateBrows(emotion);
    this.updateEyes(t, emotion, look, k);
  }

  private updateMouth(t: number, talking: boolean, emotion: Emotion): void {
    const open = talking ? 0.35 + 0.65 * Math.abs(Math.sin(t * 13) * Math.sin(t * 4.3 + 1)) : 0;
    this.mouth.visible = talking;
    this.mouth.scale.set(1.2 - open * 0.2, 0.2 + open * 0.9, 0.45);
    this.smileArc.visible = !talking;
    const smile = emotion === 'smile' || emotion === 'relieved';
    this.smileArc.scale.set(smile ? 1.15 : 0.9, smile ? 1.0 : 0.45, 1);
  }

  private updateBrows(emotion: Emotion): void {
    const [bl, br] = this.brows;
    const tilt = emotion === 'serious' ? 0.25 : emotion === 'nervous' ? -0.28 : emotion === 'smile' ? -0.08 : 0;
    bl.rotation.z = Math.PI / 2 - tilt;
    br.rotation.z = Math.PI / 2 + tilt;
    const y = emotion === 'serious' ? 0.157 : emotion === 'nervous' || emotion === 'smile' ? 0.169 : 0.163;
    bl.position.y = y;
    br.position.y = y + (emotion === 'think' ? 0.012 : 0);
  }

  private updateEyes(t: number, emotion: Emotion, look: THREE.Vector2, k: number): void {
    if (t > this.nextBlink) {
      this.blinkUntil = t + 0.13;
      this.nextBlink = t + 2.5 + Math.random() * 3;
    }
    const target = t < this.blinkUntil ? LID_CLOSED : LID_OPEN[emotion];
    // 眨眼要快、表情變化慢
    this.lidAngle += (target - this.lidAngle) * (target === LID_CLOSED || this.lidAngle > 0.3 ? 0.6 : k);
    for (const lid of this.lids) lid.rotation.x = this.lidAngle;
    for (const gaze of this.gazes) gaze.position.set(look.x * 0.008, -look.y * 0.007, 0);
  }
}

/** 視線目標（ワールド座標）→ (左右, 上下) 角。headY = 本地座標での目の高さ */
export function lookAngles(group: THREE.Object3D, target: THREE.Vector3, headY: number, out: THREE.Vector2): void {
  const local = group.worldToLocal(target.clone());
  const dx = local.x, dy = local.y - headY, dz = Math.max(0.3, local.z);
  out.set(
    THREE.MathUtils.clamp(Math.atan2(dx, dz), -0.6, 0.6),
    THREE.MathUtils.clamp(-Math.atan2(dy, dz), -0.3, 0.3),
  );
}
