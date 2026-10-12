// 室內行走碰撞（純函式）。概念移植自 Ginza web/collision.js：圓形玩家 × 障礙物、
// 撞牆沿牆滑動（先試完整位移，再試只走 x 或只走 z）。室內障礙全是軸對齊矩形，故簡化。
import { type Rect } from './layout';

export function circleHitsRect(x: number, z: number, radius: number, r: Rect): boolean {
  const cx = Math.max(r[0], Math.min(x, r[2]));
  const cz = Math.max(r[1], Math.min(z, r[3]));
  return (x - cx) ** 2 + (z - cz) ** 2 < radius * radius;
}

export interface Collider {
  blocked(x: number, z: number, radius: number): boolean;
  move(x: number, z: number, dx: number, dz: number, radius: number): [number, number];
}

export function createCollider(rects: readonly Rect[]): Collider {
  const blocked = (x: number, z: number, radius: number) => rects.some((r) => circleHitsRect(x, z, radius, r));
  return {
    blocked,
    move(x, z, dx, dz, radius) {
      if (!blocked(x + dx, z + dz, radius)) return [x + dx, z + dz];
      if (!blocked(x + dx, z, radius)) return [x + dx, z];
      if (!blocked(x, z + dz, radius)) return [x, z + dz];
      return [x, z];
    },
  };
}
