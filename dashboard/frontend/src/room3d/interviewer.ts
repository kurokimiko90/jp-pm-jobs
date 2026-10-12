// 面試官：人物（デフォルメ寄りの頭身・西裝＋領帶＋細框眼鏡），原創造型。
// 說話時嘴巴開合＋頭微動＋手勢、聽回答時點頭、平時呼吸／眨眼、
// 視線跟著候選人（鏡頭）。節奏對齊 VN 頁 Avatar 的 vnTalkMouth / vnNod。
// 顔の作りと表情は figure.ts の Face を受付と共用。
// 正面朝本地 +z，原點 = 椅子中心地面。
import * as THREE from 'three';
import { softMaterial } from './toon';
import { Face, ball, capsule, lookAngles, mat, mesh, type Emotion } from './figure';

export type InterviewerEmotion = Emotion;

export interface InterviewerState {
  talking: boolean;
  listening: boolean;
  emotion: InterviewerEmotion;
}

/** 前臂平時略往下擱在桌面（桌面 y = 0.75） */
const FOREARM_REST = 0.13;
/** 頭の高さ（視線計算用、本地座標） */
const HEAD_Y = 1.3;

/** 手臂：肩 → 肘 → 手，前臂可繞肘轉（手勢） */
function buildArm(sx: number): { root: THREE.Group; elbow: THREE.Group } {
  const root = new THREE.Group();
  root.add(mesh(capsule(0.046, 0.2), mat('suit'), [sx * 0.015, -0.13, 0.05], undefined, [0.45, 0, sx * 0.08]));
  const elbow = new THREE.Group();
  elbow.position.set(sx * 0.03, -0.24, 0.11);
  elbow.add(mesh(capsule(0.042, 0.26), mat('suit'), [0, 0, 0.17], undefined, [Math.PI / 2, 0, 0]));
  // 袖口露出一截白襯衫
  elbow.add(mesh(new THREE.CylinderGeometry(0.039, 0.039, 0.03, 16), mat('shirt'), [0, 0, 0.33], undefined, [Math.PI / 2, 0, 0]));
  // 手：手掌＋併攏的四指＋拇指
  elbow.add(ball(0.042, 'skin', [0, -0.005, 0.375], [0.95, 0.6, 1.1]));
  elbow.add(ball(0.034, 'skin', [0, -0.012, 0.42], [1.05, 0.5, 0.9]));
  elbow.add(ball(0.015, 'skin', [-sx * 0.038, 0.004, 0.37], [1, 0.9, 1.6], [0, -sx * 0.5, 0]));
  root.add(elbow);
  return { root, elbow };
}

/** 細框眼鏡：兩個圓角鏡框＋鼻橋＋鏡腳 */
function buildGlasses(): THREE.Group {
  const g = new THREE.Group();
  const frame = softMaterial({ color: '#2d2a2e', roughness: 0.3, metalness: 0.4 });
  for (const sx of [-1, 1]) {
    const rim = mesh(new THREE.TorusGeometry(0.036, 0.004, 8, 32), frame, [sx * 0.054, 0.118, 0.158], [1.1, 0.85, 1], [0, sx * 0.22, 0]);
    g.add(rim);
    g.add(mesh(new THREE.CylinderGeometry(0.0035, 0.0035, 0.13, 8), frame, [sx * 0.118, 0.124, 0.09], undefined, [Math.PI / 2, 0, 0]));
  }
  g.add(mesh(new THREE.CylinderGeometry(0.0035, 0.0035, 0.028, 8), frame, [0, 0.126, 0.168], undefined, [0, 0, Math.PI / 2]));
  return g;
}

export class Interviewer {
  readonly group = new THREE.Group();
  private head = new THREE.Group();
  private torso = new THREE.Group();
  private face: Face;
  private elbows: THREE.Group[] = [];
  private gesture = 0;
  private look = new THREE.Vector2();
  private state: InterviewerState = { talking: false, listening: false, emotion: 'neutral' };

  constructor() {
    this.buildLegs();
    this.buildTorso();
    // 頭：比寫實比例大一點（親しみやすさ），但保持人的頭身
    this.head.position.set(0, 1.13, 0.0);
    this.group.add(this.head);
    this.face = new Face(this.head);
    this.buildHair();
    this.head.add(buildGlasses());
  }

  private buildLegs(): void {
    for (const sx of [-1, 1]) {
      this.group.add(mesh(capsule(0.072, 0.3), mat('suit'), [sx * 0.1, 0.56, 0.12], undefined, [Math.PI / 2, 0, 0]));
      this.group.add(mesh(capsule(0.06, 0.36), mat('suit'), [sx * 0.1, 0.3, 0.3]));
      // 皮鞋：鞋身＋鞋底
      this.group.add(mesh(capsule(0.048, 0.14), mat('shoe', 0.3), [sx * 0.1, 0.05, 0.37], [1, 0.7, 1], [Math.PI / 2, 0, 0]));
      this.group.add(mesh(new THREE.BoxGeometry(0.1, 0.018, 0.23), mat('black', 0.8), [sx * 0.1, 0.009, 0.37]));
    }
  }

  private buildTorso(): void {
    const t = this.torso;
    this.group.add(t);
    // 西裝：上寬下窄的軀幹＋肩膀
    t.add(ball(0.2, 'suit', [0, 0.83, 0], [1.05, 1.3, 0.68]));
    for (const sx of [-1, 1]) t.add(ball(0.066, 'suit', [sx * 0.175, 1.02, 0], [1.1, 0.72, 0.95]));
    // 襯衫前襟（V 字に見える細め）＋領帶
    t.add(ball(0.07, 'shirt', [0, 1.01, 0.105], [0.62, 1.25, 0.42]));
    t.add(ball(0.016, 'tie', [0, 1.06, 0.14], [1.1, 0.8, 0.6]));
    t.add(ball(0.022, 'tie', [0, 0.97, 0.146], [0.8, 3.0, 0.3], [-0.12, 0, 0]));
    // 翻領
    for (const sx of [-1, 1]) {
      t.add(mesh(capsule(0.024, 0.17), mat('navy'), [sx * 0.058, 0.975, 0.13], [1, 1, 0.35], [-0.2, 0, sx * 0.3]));
    }
    // 脖子＋襯衫領口
    t.add(mesh(new THREE.CylinderGeometry(0.048, 0.052, 0.08, 20), mat('skin'), [0, 1.11, 0.01]));
    t.add(mesh(new THREE.CylinderGeometry(0.06, 0.066, 0.05, 20, 1, true), mat('shirt'), [0, 1.08, 0.01]));
    for (const sx of [-1, 1]) {
      const { root, elbow } = buildArm(sx);
      root.position.set(sx * 0.2, 1.03, 0);
      t.add(root);
      this.elbows.push(elbow);
    }
  }

  /** 短髮：後腦＋頭頂一片、旁分瀏海、鬢角 */
  private buildHair(): void {
    const h = this.head;
    // 頭頂到後腦：略大於頭殼的半球，往後傾
    h.add(mesh(new THREE.SphereGeometry(0.138, 32, 20, 0, Math.PI * 2, 0, Math.PI * 0.55), mat('hair'), [0, 0.135, -0.008], [0.96, 1.08, 1.03], [-0.25, 0, 0]));
    // 後腦下緣
    h.add(ball(0.11, 'hair', [0, 0.1, -0.05], [1.08, 0.95, 0.9]));
    // 旁分瀏海：從右分線往左前方掃
    h.add(ball(0.07, 'hair', [-0.035, 0.215, 0.075], [1.35, 0.5, 0.95], [0.35, 0, 0.2]));
    h.add(ball(0.05, 'hairHi', [0.05, 0.23, 0.06], [1.2, 0.45, 1], [0.3, 0, -0.25]));
    // 鬢角
    for (const sx of [-1, 1]) h.add(ball(0.03, 'hair', [sx * 0.112, 0.15, 0.02], [0.5, 1.2, 0.9]));
  }

  setState(state: InterviewerState): void {
    this.state = state;
  }

  /** 視線目標（世界座標，通常是鏡頭位置） */
  lookAt(target: THREE.Vector3): void {
    lookAngles(this.group, target, HEAD_Y, this.look);
  }

  update(t: number, dt: number): void {
    const { talking, listening, emotion } = this.state;
    const k = 1 - Math.exp(-dt * 4);
    this.torso.scale.y = 1 + Math.sin(t * 1.6) * 0.006;
    // 頭：看向候選人＋說話起伏＋聽時點頭＋思考歪頭
    const nod = listening ? Math.max(0, Math.sin(t * 2.6)) ** 6 * 0.15 : 0;
    this.head.rotation.y += (this.look.x * 0.75 + Math.sin(t * 0.43) * 0.03 - this.head.rotation.y) * k;
    this.head.rotation.x += (this.look.y * 0.7 + nod + (talking ? Math.sin(t * 3.1) * 0.03 : 0) - this.head.rotation.x) * k;
    this.head.rotation.z = emotion === 'think' ? 0.08 : Math.sin(t * 0.7) * 0.012;
    // 手勢：說話時右手偶爾抬起、左手輕動
    this.gesture += ((talking ? 1 : 0) - this.gesture) * (1 - Math.exp(-dt * 2.5));
    const [left, right] = this.elbows;
    right.rotation.x = FOREARM_REST - this.gesture * (0.35 + Math.max(0, Math.sin(t * 1.7)) * 0.45);
    right.rotation.y = this.gesture * Math.sin(t * 2.3) * 0.25;
    left.rotation.x = FOREARM_REST - this.gesture * Math.max(0, Math.sin(t * 1.3 + 2)) * 0.2;
    this.face.update(t, talking, emotion, this.look, k);
  }
}
