// 辦公室家具的積木模型（純資料）。正面朝本地 +z，底部 y = 0。
import { box, cone, cyl, sph, type Model3D, type Part } from './dsl';
import { type FurnitureKind } from './layout';

const legs4 = (w: number, d: number, h: number, c: 'steel' | 'woodDark' | 'black', t = 0.04): Part[] =>
  [[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([sx, sz]) =>
    box(t, h, t, sx * (w / 2 - t), 0, sz * (d / 2 - t), c));

const officeChair = (): Model3D => ({
  parts: [
    cyl(0.28, 0.04, 0, 0.05, 0, 'black', { seg: 5 }),
    cyl(0.03, 0.36, 0, 0.08, 0, 'steel'),
    box(0.5, 0.08, 0.48, 0, 0.44, 0, 'black', { round: 0.03 }),
    box(0.46, 0.55, 0.07, 0, 0.55, -0.24, 'black', { round: 0.03, r: [-6, 0, 0] }),
    box(0.04, 0.2, 0.3, -0.25, 0.5, -0.02, 'steel'),
    box(0.04, 0.2, 0.3, 0.25, 0.5, -0.02, 'steel'),
  ],
});

const MODELS: Record<FurnitureKind, () => Model3D> = {
  counter: () => ({
    parts: [
      box(2.8, 1.0, 0.7, 0, 0, 0, 'wood', { round: 0.02 }),
      box(2.9, 0.05, 0.78, 0, 1.0, 0, 'white', { round: 0.015 }),
      box(2.8, 0.06, 0.02, 0, 0.1, 0.36, 'woodDark'),
      box(0.4, 0.28, 0.03, 0.8, 1.05, -0.15, 'black', { r: [-12, 0, 0] }),
      box(0.12, 0.02, 0.08, 0.8, 1.05, -0.08, 'black'),
      cyl(0.05, 0.05, -0.9, 1.05, 0.1, 'gold'),
      sph(0.035, -0.9, 1.12, 0.1, 'gold'),
    ],
  }),
  plant: () => ({
    parts: [
      cyl(0.2, 0.42, 0, 0, 0, 'pot', { top: 0.24 }),
      box(0.06, 0.4, 0.06, 0, 0.4, 0, 'woodDark'),
      sph(0.3, 0, 1.0, 0, 'green', { scale: [1, 1.25, 1] }),
      sph(0.22, 0.14, 1.32, 0.05, 'dgreen'),
      sph(0.2, -0.12, 1.25, -0.08, 'green'),
    ],
  }),
  sofa: () => ({
    parts: [
      box(1.9, 0.32, 0.82, 0, 0.08, 0, 'navy', { round: 0.06 }),
      box(0.86, 0.12, 0.62, -0.45, 0.4, 0.08, 'indigo', { round: 0.05 }),
      box(0.86, 0.12, 0.62, 0.45, 0.4, 0.08, 'indigo', { round: 0.05 }),
      box(1.9, 0.5, 0.2, 0, 0.36, -0.32, 'navy', { round: 0.07 }),
      box(0.16, 0.3, 0.8, -0.95, 0.36, 0, 'navy', { round: 0.06 }),
      box(0.16, 0.3, 0.8, 0.95, 0.36, 0, 'navy', { round: 0.06 }),
      ...legs4(1.8, 0.7, 0.08, 'woodDark'),
    ],
  }),
  lowTable: () => ({
    parts: [
      box(1.1, 0.05, 0.62, 0, 0.42, 0, 'wood', { round: 0.02 }),
      ...legs4(1.0, 0.54, 0.42, 'steel', 0.035),
      box(0.22, 0.02, 0.3, -0.2, 0.47, 0.05, 'white', { r: [0, 12, 0] }),
      box(0.2, 0.015, 0.28, 0.15, 0.47, -0.05, 'cream', { r: [0, -8, 0] }),
    ],
  }),
  waterServer: () => ({
    parts: [
      box(0.36, 1.0, 0.36, 0, 0, 0, 'white', { round: 0.03 }),
      cyl(0.15, 0.4, 0, 1.0, 0, 'glass', { top: 0.12 }),
      box(0.05, 0.05, 0.05, -0.07, 0.72, 0.19, 'indigo'),
      box(0.05, 0.05, 0.05, 0.07, 0.72, 0.19, 'red'),
    ],
  }),
  chairSide: () => ({
    parts: [
      ...legs4(0.48, 0.48, 0.44, 'steel', 0.03),
      box(0.5, 0.06, 0.5, 0, 0.44, 0, 'carpetWarm', { round: 0.02 }),
      box(0.5, 0.42, 0.06, 0, 0.5, -0.22, 'carpetWarm', { round: 0.02 }),
    ],
  }),
  desk: () => ({
    parts: [
      box(2.0, 0.05, 0.9, 0, 0.7, 0, 'white', { round: 0.015 }),
      box(0.05, 0.7, 0.8, -0.95, 0, 0, 'steel'),
      box(0.05, 0.7, 0.8, 0.95, 0, 0, 'steel'),
      box(1.85, 0.4, 0.03, 0, 0.3, 0.2, 'light'),
      // 面試官側：筆電（背面朝候選人）、資料、水杯
      box(0.34, 0.02, 0.24, -0.72, 0.75, 0.24, 'steel'),
      box(0.34, 0.23, 0.015, -0.72, 0.76, 0.12, 'steel', { r: [-18, 0, 0] }),
      box(0.24, 0.006, 0.32, 0.25, 0.75, 0.22, 'white', { r: [0, 6, 0] }),
      box(0.24, 0.006, 0.32, 0.29, 0.756, 0.2, 'cream', { r: [0, -4, 0] }),
      cyl(0.035, 0.11, 0.62, 0.75, 0.18, 'glass'),
      // 候選人側：自己的筆記與筆
      box(0.22, 0.006, 0.3, 0.1, 0.75, -0.26, 'white', { r: [0, -5, 0] }),
      box(0.012, 0.012, 0.14, 0.27, 0.757, -0.26, 'black', { r: [0, 20, 0] }),
      cyl(0.035, 0.11, -0.45, 0.75, -0.25, 'glass'),
    ],
  }),
  chairCandidate: officeChair,
  chairInterviewer: officeChair,
  bench: () => ({
    parts: [
      box(1.5, 0.08, 0.45, 0, 0.42, 0, 'wood', { round: 0.02 }),
      ...legs4(1.4, 0.4, 0.42, 'black', 0.04),
    ],
  }),
  shelf: () => ({
    parts: [
      // 背板 + 側板 + 頂板（中空，書才看得到）
      box(1.1, 1.8, 0.03, 0, 0, -0.165, 'wood'),
      box(0.04, 1.8, 0.36, -0.53, 0, 0, 'wood'),
      box(0.04, 1.8, 0.36, 0.53, 0, 0, 'wood'),
      box(1.1, 0.04, 0.36, 0, 1.76, 0, 'wood'),
      box(1.1, 0.08, 0.36, 0, 0, 0, 'woodDark'),
      ...[0.35, 0.8, 1.25].flatMap((y, i) => [
        box(1.0, 0.03, 0.32, 0, y, 0.02, 'woodDark'),
        ...[-0.35, -0.22, -0.1, 0.05, 0.18].map((x, j) =>
          box(0.1, 0.3 - ((i + j) % 3) * 0.04, 0.24, x, y + 0.03, 0.04, (['indigo', 'red', 'cream', 'navy', 'gold'] as const)[(i + j) % 5])),
      ]),
      cone(0.08, 0.15, 0.32, 1.28, 0.04, 'green'),
    ],
  }),
};

export function furnitureModel(kind: FurnitureKind): Model3D {
  return MODELS[kind]();
}
