// 面接フロアの平面図（純資料）。渲染、碰撞、小地圖都讀這一份。
// 座標：x 往東、z 往南、y 往上，單位公尺。四個房間沿 x 一字排開，隔牆偏南側開門。
//   受付（0–8）→ 待合室（8–14）→ 面接室（14–22）→ 振り返り室（22–28）

export type RoomId = 'lobby' | 'waiting' | 'interview' | 'review';
/** [minX, minZ, maxX, maxZ] */
export type Rect = [number, number, number, number];

export interface Room {
  id: RoomId;
  rect: Rect;
  floor: 'floor' | 'carpet' | 'carpetWarm' | 'light';
  labelJa: string;
  labelZh: string;
  /** 瞬移落點與朝向（yaw：0 = 看北 -z，-π/2 = 看東 +x） */
  spawn: { x: number; z: number; yaw: number };
}

export const FLOOR_DEPTH = 8;
export const WALL_HEIGHT = 2.8;
export const WALL_T = 0.16;
/** 隔牆門洞（z 範圍），四道門對齊，從受付一路走得過去 */
export const DOOR_Z: [number, number] = [5.3, 7.3];
export const DOOR_HEIGHT = 2.15;

export const ROOMS: readonly Room[] = [
  { id: 'lobby', rect: [0, 0, 8, FLOOR_DEPTH], floor: 'floor', labelJa: '受付', labelZh: '接待處', spawn: { x: 4.2, z: 6.6, yaw: 0.12 } },
  { id: 'waiting', rect: [8, 0, 14, FLOOR_DEPTH], floor: 'carpetWarm', labelJa: '待合室', labelZh: '候考室', spawn: { x: 9.2, z: 6.4, yaw: -0.35 } },
  { id: 'interview', rect: [14, 0, 22, FLOOR_DEPTH], floor: 'carpet', labelJa: '面接室', labelZh: '面試室', spawn: { x: 15.2, z: 6.4, yaw: -1.0 } },
  { id: 'review', rect: [22, 0, 28, FLOOR_DEPTH], floor: 'light', labelJa: '振り返り室', labelZh: '覆盤室', spawn: { x: 23.2, z: 6.4, yaw: -0.9 } },
];

export const FLOOR_RECT: Rect = [0, 0, 28, FLOOR_DEPTH];
const PARTITIONS_X = [8, 14, 22];

export function roomAt(x: number): RoomId {
  return (ROOMS.find((r) => x >= r.rect[0] && x < r.rect[2]) ?? ROOMS[ROOMS.length - 1]).id;
}

/** 牆的實體（含外牆，隔牆扣掉門洞）。渲染與碰撞共用 */
export function wallRects(): Rect[] {
  const [x0, z0, x1, z1] = FLOOR_RECT;
  const h = WALL_T / 2;
  const rects: Rect[] = [
    [x0 - h, z0 - h, x1 + h, z0 + h],
    [x0 - h, z1 - h, x1 + h, z1 + h],
    [x0 - h, z0 - h, x0 + h, z1 + h],
    [x1 - h, z0 - h, x1 + h, z1 + h],
  ];
  for (const x of PARTITIONS_X) {
    rects.push([x - h, z0, x + h, DOOR_Z[0]], [x - h, DOOR_Z[1], x + h, z1]);
  }
  return rects;
}

/** 門楣（門洞上方的牆）—— 只渲染不碰撞 */
export function lintelRects(): { rect: Rect; x: number }[] {
  const h = WALL_T / 2;
  return PARTITIONS_X.map((x) => ({ rect: [x - h, DOOR_Z[0], x + h, DOOR_Z[1]] as Rect, x }));
}

export type FurnitureKind =
  | 'counter' | 'plant' | 'sofa' | 'lowTable' | 'waterServer' | 'chairSide'
  | 'desk' | 'chairCandidate' | 'chairInterviewer' | 'bench' | 'shelf';

export interface Furniture {
  kind: FurnitureKind;
  x: number;
  z: number;
  /** 正面朝向（模型本地 +z 轉到的 yaw，弧度） */
  rot: number;
  /** 碰撞半尺寸（x, z；已考慮旋轉後的外接） */
  half: [number, number];
}

const R90 = Math.PI / 2;

export const FURNITURE: readonly Furniture[] = [
  // 受付
  { kind: 'counter', x: 4.2, z: 3.8, rot: 0, half: [1.4, 0.4] },
  { kind: 'plant', x: 0.55, z: 7.4, rot: 0, half: [0.3, 0.3] },
  { kind: 'plant', x: 7.45, z: 0.55, rot: 0, half: [0.3, 0.3] },
  { kind: 'bench', x: 1.0, z: 5.0, rot: R90, half: [0.3, 0.8] },
  // 待合室
  { kind: 'sofa', x: 11, z: 2.9, rot: 0, half: [1.0, 0.45] },
  { kind: 'lowTable', x: 11, z: 4.1, rot: 0, half: [0.55, 0.32] },
  { kind: 'chairSide', x: 12.5, z: 4.3, rot: -R90, half: [0.3, 0.3] },
  { kind: 'waterServer', x: 13.55, z: 0.5, rot: 0, half: [0.22, 0.22] },
  { kind: 'plant', x: 8.5, z: 0.5, rot: 0, half: [0.3, 0.3] },
  // 面接室（面試官坐東側面向西，候選人坐西側面向東）
  { kind: 'desk', x: 19.0, z: 3.4, rot: R90, half: [0.45, 1.0] },
  { kind: 'chairInterviewer', x: 19.95, z: 3.4, rot: -R90, half: [0.3, 0.3] },
  { kind: 'chairCandidate', x: 17.75, z: 3.4, rot: R90, half: [0.3, 0.3] },
  { kind: 'plant', x: 21.5, z: 7.5, rot: 0, half: [0.3, 0.3] },
  { kind: 'plant', x: 14.5, z: 0.5, rot: 0, half: [0.3, 0.3] },
  // 振り返り室
  { kind: 'lowTable', x: 25, z: 4.0, rot: 0, half: [0.55, 0.32] },
  { kind: 'shelf', x: 27.75, z: 1.2, rot: -R90, half: [0.25, 0.6] },
];

/** 受付係：カウンターの奥（北側）に立ち、来訪者（+z 側）を向く */
export const RECEPTIONIST = { x: 3.7, z: 3.0 } as const;

export function furnitureRects(): Rect[] {
  const rects: Rect[] = FURNITURE.map((f) => [f.x - f.half[0], f.z - f.half[1], f.x + f.half[0], f.z + f.half[1]]);
  return [...rects, [RECEPTIONIST.x - 0.25, RECEPTIONIST.z - 0.2, RECEPTIONIST.x + 0.25, RECEPTIONIST.z + 0.2]];
}

/** 面接席：坐下後的眼睛位置與面試官頭部位置 */
export const SEAT = { x: 17.72, y: 1.2, z: 3.4, yaw: -R90 } as const;
export const INTERVIEWER = { x: 19.95, z: 3.4 } as const;

/** 牆面展示板（station id 對應後端 room3d_api.STATIONS + 前端的 summary） */
export interface Board {
  id: string;
  room: RoomId;
  /** 板面中心 */
  x: number;
  y: number;
  z: number;
  /** 板面法線（朝房間內側） */
  nx: number;
  nz: number;
  w: number;
  h: number;
}

const north = (id: string, room: RoomId, x: number): Board => ({ id, room, x, y: 1.6, z: WALL_T / 2 + 0.03, nx: 0, nz: 1, w: 1.9, h: 1.3 });
const south = (id: string, room: RoomId, x: number): Board => ({ id, room, x, y: 1.6, z: FLOOR_DEPTH - WALL_T / 2 - 0.03, nx: 0, nz: -1, w: 1.9, h: 1.3 });

export const BOARDS: readonly Board[] = [
  north('company', 'lobby', 1.6),
  north('product', 'lobby', 4.0),
  north('persona', 'lobby', 6.4),
  { id: 'readme', room: 'lobby', x: WALL_T / 2 + 0.03, y: 1.6, z: 2.6, nx: 1, nz: 0, w: 1.9, h: 1.3 },
  north('checklist', 'waiting', 11),
  south('jikoshoukai', 'waiting', 10.6),
  north('qa_audit', 'review', 23.6),
  north('hire_audit', 'review', 26.4),
  south('theater_review', 'review', 24.4),
  south('proposal_review', 'review', 26.6),
  { id: 'summary', room: 'review', x: 28 - WALL_T / 2 - 0.03, y: 1.6, z: 3.4, nx: -1, nz: 0, w: 1.9, h: 1.3 },
];

/** 面接室東牆的投影螢幕（提案スライド） */
export const SCREEN: Board = { id: 'screen', room: 'interview', x: 22 - WALL_T / 2 - 0.03, y: 1.85, z: 1.55, nx: -1, nz: 0, w: 2.2, h: 1.24 };

/** 看板前的站立點（走過去看的時候用） */
export function viewpoint(b: Board, distance = 1.7): { x: number; z: number; yaw: number } {
  return { x: b.x + b.nx * distance, z: b.z + b.nz * distance, yaw: Math.atan2(b.nx, b.nz) };
}
