// 3D 家具の「積木」描述：每件 = 幾個基本形體。純資料、不依賴 three.js。
// 移植自 la-game-ginza3d 的 src/lib/city3d/model-dsl.ts（同作者，Ginza 3D 的 React 版），
// 色票改成辦公室色系。座標：公尺；地面 y = 0；物件正面朝本地 +z。

export const PALETTE = {
  ink: '#3a3340',
  wall: '#f2e6d4',
  wallWarm: '#e8cfae',
  floor: '#d2a878',
  carpet: '#9fb4a8',
  carpetWarm: '#d9b99a',
  wood: '#c48a58',
  woodDark: '#7a5236',
  white: '#fafafa',
  light: '#e3e6e8',
  grey: '#90a4ae',
  steel: '#b0bec5',
  black: '#3a3a44',
  navy: '#3d5a80',
  suit: '#3f5574',
  shirt: '#e4eef7',
  tie: '#e3a23e',
  skin: '#f1cfb2',
  hair: '#2b2a2e',
  green: '#7cbf6e',
  dgreen: '#4f9a5a',
  pot: '#d9d2c5',
  gold: '#c9a227',
  glass: '#b9dcef',
  red: '#d06a5f',
  cream: '#fff6e8',
  indigo: '#e8a87c',
  // 面試官（人物）
  skinShade: '#e2b796',
  blush: '#efa894',
  hairHi: '#3d3a40',
  shoe: '#2a2428',
  // 受付（人物）
  hairBrown: '#5a3a2b',
  hairBrownHi: '#74503b',
  stocking: '#d9b49a',
} as const;

export type ColorKey = keyof typeof PALETTE;
export type Vec3 = [number, number, number];

interface PartBase {
  c: ColorKey;
  /** 形體中心 */
  p: Vec3;
  /** 旋轉（度），XYZ 順序 */
  r?: Vec3;
}

export type Part =
  | (PartBase & { s: 'box'; size: Vec3; round?: number })
  | (PartBase & { s: 'cyl'; r0: number; r1: number; h: number; seg?: number })
  | (PartBase & { s: 'sph'; rad: number; scale?: Vec3 })
  | (PartBase & { s: 'cone'; rad: number; h: number; seg?: number });

export interface Model3D {
  parts: Part[];
  /** 預設不描邊；true 才加墨線外殼 */
  outline?: boolean;
}

interface Opt {
  r?: Vec3;
}

// y 參數是「底部」高度（球是中心）
export const box = (w: number, h: number, d: number, x: number, y: number, z: number, c: ColorKey, o: Opt & { round?: number } = {}): Part =>
  ({ s: 'box', size: [w, h, d], p: [x, y + h / 2, z], c, r: o.r, round: o.round });
export const cyl = (r: number, h: number, x: number, y: number, z: number, c: ColorKey, o: Opt & { top?: number; seg?: number } = {}): Part =>
  ({ s: 'cyl', r0: r, r1: o.top ?? r, h, p: [x, y + h / 2, z], c, r: o.r, seg: o.seg });
export const sph = (rad: number, x: number, y: number, z: number, c: ColorKey, o: Opt & { scale?: Vec3 } = {}): Part =>
  ({ s: 'sph', rad, p: [x, y, z], c, r: o.r, scale: o.scale });
export const cone = (rad: number, h: number, x: number, y: number, z: number, c: ColorKey, o: Opt & { seg?: number } = {}): Part =>
  ({ s: 'cone', rad, h, p: [x, y + h / 2, z], c, r: o.r, seg: o.seg });
