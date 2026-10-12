// 受付係：カウンターの奥に立つ人物（ボブヘア・紺の制服＋金のスカーフ＋名札）。
// 来訪者（鏡頭）が近づくと「いらっしゃいませ」でお辞儀、近くにいる間は目で追い、
// 離れるとモニターへ視線を落として作業に戻る。顔は面接官と同じ figure.ts の Face。
// 正面朝本地 +z，原點 = 足元。
import * as THREE from 'three';
import { Face, ball, capsule, lookAngles, mat, mesh } from './figure';

/** 腰（お辞儀の支点）の高さ */
const HIP_Y = 0.92;
/** 目の高さ（視線計算用、本地座標） */
const HEAD_Y = 1.5;
/** この距離まで近づいたら挨拶、GREET_RESET より離れたら次回また挨拶する */
const GREET_NEAR = 4.0;
const GREET_RESET = 5.5;
/** これより遠いと来訪者を見ずにモニター作業 */
const WORK_DIST = 6.0;
/** お辞儀：倒す → 止める → 戻す（秒）と最大角 */
const BOW = { down: 0.45, hold: 1.1, up: 1.7, angle: 0.42 } as const;
/** 「いらっしゃいませ」の口パク時間 */
const GREET_TALK = 1.2;
/** モニター作業中の視線（右下） */
const WORK_LOOK = new THREE.Vector2(0.45, 0.25);

const smooth = (x: number) => x * x * (3 - 2 * x);

/** 腕：肩 → 肘 → 手。前腕を内側へ向けて体の前で手を重ねる */
function buildArm(sx: number): THREE.Group {
  const root = new THREE.Group();
  root.position.set(sx * 0.138, 0.34, 0);
  root.add(mesh(capsule(0.031, 0.18), mat('navy'), [sx * 0.01, -0.11, 0.01], undefined, [0.1, 0, sx * 0.06]));
  const elbow = new THREE.Group();
  elbow.position.set(sx * 0.02, -0.22, 0.03);
  elbow.rotation.set(0.45, -sx * 0.75, 0);
  elbow.add(mesh(capsule(0.029, 0.15), mat('navy'), [0, 0, 0.09], undefined, [Math.PI / 2, 0, 0]));
  elbow.add(mesh(new THREE.CylinderGeometry(0.031, 0.031, 0.025, 16), mat('shirt'), [0, 0, 0.175], undefined, [Math.PI / 2, 0, 0]));
  elbow.add(ball(0.032, 'skin', [0, 0, 0.205], [0.9, 0.6, 1.15]));
  root.add(elbow);
  return root;
}

export class Receptionist {
  readonly group = new THREE.Group();
  private upper = new THREE.Group();
  private head = new THREE.Group();
  private face: Face;
  private look = new THREE.Vector2();
  private gaze = new THREE.Vector2();
  private dist = Infinity;
  private near = false;
  private greetAt = -Infinity;

  constructor() {
    this.buildLower();
    this.upper.position.y = HIP_Y;
    this.group.add(this.upper);
    this.buildUpper();
    this.head.position.set(0, 0.47, 0);
    this.upper.add(this.head);
    this.face = new Face(this.head, { iris: '#5a3a2b', brow: 'hairBrown', browWidth: 0.0052, lips: '#b25a52' });
    this.buildHair();
  }

  /** 腰から下：タイトスカート＋脚＋パンプス（ほぼカウンターに隠れる） */
  private buildLower(): void {
    const g = this.group;
    // 腰の丸み（お辞儀で上半身が傾いても段差が出ないように）＋膝丈のタイトスカート
    g.add(ball(0.135, 'navy', [0, HIP_Y - 0.01, 0], [1.02, 0.7, 0.72]));
    g.add(mesh(new THREE.CylinderGeometry(0.135, 0.155, 0.4, 24), mat('navy'), [0, HIP_Y - 0.21, 0], [1, 1, 0.72]));
    for (const sx of [-1, 1]) {
      g.add(mesh(capsule(0.05, 0.4), mat('stocking'), [sx * 0.062, 0.3, 0], [1, 1, 0.9]));
      g.add(mesh(capsule(0.036, 0.1), mat('shoe', 0.25), [sx * 0.065, 0.04, 0.04], [1, 0.75, 1], [Math.PI / 2, 0, 0]));
    }
  }

  /** 上半身：制服ジャケット＋ブラウス＋スカーフ＋名札＋腕 */
  private buildUpper(): void {
    const u = this.upper;
    u.add(ball(0.15, 'navy', [0, 0.2, 0], [1, 1.3, 0.66]));
    for (const sx of [-1, 1]) u.add(ball(0.048, 'navy', [sx * 0.125, 0.355, 0], [1.1, 0.72, 0.95]));
    // ブラウスの V 字と襟
    u.add(ball(0.055, 'shirt', [0, 0.33, 0.072], [0.62, 1.1, 0.42]));
    for (const sx of [-1, 1]) {
      u.add(mesh(capsule(0.018, 0.12), mat('suit'), [sx * 0.05, 0.31, 0.088], [1, 1, 0.35], [-0.2, 0, sx * 0.32]));
    }
    // スカーフ：結び目＋左右に垂れる端
    u.add(ball(0.026, 'tie', [0, 0.4, 0.08], [1.4, 0.75, 0.6]));
    for (const sx of [-1, 1]) u.add(ball(0.02, 'tie', [sx * 0.028, 0.36, 0.09], [0.75, 1.5, 0.35], [0, 0, sx * 0.45]));
    // 名札（左胸）
    u.add(mesh(new THREE.BoxGeometry(0.06, 0.022, 0.008), mat('white', 0.5), [0.07, 0.27, 0.098], undefined, [-0.15, 0, 0]));
    u.add(mesh(new THREE.BoxGeometry(0.06, 0.005, 0.009), mat('gold', 0.35), [0.07, 0.28, 0.099], undefined, [-0.15, 0, 0]));
    // 首
    u.add(mesh(new THREE.CylinderGeometry(0.038, 0.042, 0.08, 20), mat('skin'), [0, 0.44, 0.005]));
    for (const sx of [-1, 1]) u.add(buildArm(sx));
  }

  /** ボブヘア：頭頂のキャップ＋耳を隠す丸いボリューム＋顔の横の毛束＋前髪＋ヘアピン */
  private buildHair(): void {
    const h = this.head;
    h.add(mesh(new THREE.SphereGeometry(0.142, 32, 20, 0, Math.PI * 2, 0, Math.PI * 0.55), mat('hairBrown'), [0, 0.135, -0.005], [0.98, 1.08, 1.04], [-0.2, 0, 0]));
    h.add(ball(0.13, 'hairBrown', [0, 0.08, -0.03], [1.12, 1.0, 0.95]));
    for (const sx of [-1, 1]) h.add(ball(0.045, 'hairBrown', [sx * 0.106, 0.065, 0.04], [0.55, 1.5, 0.9]));
    // 前髪：眉にかからない高さで横一列
    h.add(ball(0.1, 'hairBrown', [0, 0.218, 0.07], [1.15, 0.42, 0.75], [0.45, 0, 0]));
    h.add(ball(0.05, 'hairBrownHi', [0.04, 0.245, 0.055], [1.3, 0.45, 1], [0.3, 0, -0.2]));
    h.add(mesh(new THREE.CapsuleGeometry(0.006, 0.03, 4, 8), mat('gold', 0.3), [0.088, 0.2, 0.085], undefined, [0, 0, -0.9]));
  }

  /** 来訪者（鏡頭）の位置。距離で挨拶・視線を切り替える */
  lookAt(target: THREE.Vector3): void {
    lookAngles(this.group, target, HEAD_Y, this.look);
    const p = this.group.position;
    this.dist = Math.hypot(target.x - p.x, target.z - p.z);
  }

  update(t: number, dt: number): void {
    if (!this.near && this.dist < GREET_NEAR) { this.near = true; this.greetAt = t; }
    else if (this.near && this.dist > GREET_RESET) this.near = false;

    const since = t - this.greetAt;
    const bow = since < BOW.down ? smooth(since / BOW.down)
      : since < BOW.hold ? 1
        : since < BOW.up ? 1 - smooth((since - BOW.hold) / (BOW.up - BOW.hold)) : 0;
    this.upper.rotation.x = bow * BOW.angle;

    const k = 1 - Math.exp(-dt * 4);
    const target = this.dist > WORK_DIST ? WORK_LOOK : this.look;
    this.gaze.lerp(target, k);
    // お辞儀中は頭も体と一緒に下がる（相手を見続けない）
    const follow = 1 - bow;
    this.head.rotation.y += (this.gaze.x * 0.7 * follow + Math.sin(t * 0.37) * 0.025 - this.head.rotation.y) * k;
    this.head.rotation.x += (this.gaze.y * 0.6 * follow - this.head.rotation.x) * k;
    this.head.rotation.z = Math.sin(t * 0.6 + 1) * 0.02 + (this.near ? 0.05 : 0);
    this.upper.scale.y = 1 + Math.sin(t * 1.5) * 0.005;

    const talking = since >= 0 && since < GREET_TALK;
    this.face.update(t, talking, this.dist > WORK_DIST ? 'neutral' : 'smile', this.gaze, k);
  }
}
