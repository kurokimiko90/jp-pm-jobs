// 把 layout.ts 的平面圖蓋成 three.js 場景：地板、牆、門框、天花板燈、窗、家具、展示板、投影幕、時鐘。
import * as THREE from 'three';
import { PALETTE, type ColorKey } from './dsl';
import {
  BOARDS, DOOR_HEIGHT, DOOR_Z, FURNITURE, INTERVIEWER, RECEPTIONIST, ROOMS, SCREEN, WALL_HEIGHT, WALL_T,
  lintelRects, wallRects, type Board, type Rect,
} from './layout';
import { furnitureModel } from './models';
import { Interviewer } from './interviewer';
import { Receptionist } from './receptionist';
import { buildModel, colorOf, softMaterial } from './toon';
import { artTexture, floorTexture, rugTexture, screenCanvas, skylineTexture } from './textures';

export type Pick = { type: 'board'; id: string } | { type: 'screen' } | { type: 'seat' };

export interface BuiltFloor {
  boardFaces: Map<string, THREE.Mesh>;
  screen: { face: THREE.Mesh; ctx: CanvasRenderingContext2D; tex: THREE.CanvasTexture };
  doorSigns: { mesh: THREE.Mesh; target: 'east' | 'west'; x: number }[];
  counterSign: THREE.Mesh;
  interviewer: Interviewer;
  receptionist: Receptionist;
  clockHands: { hour: THREE.Object3D; minute: THREE.Object3D };
  pickables: THREE.Object3D[];
}

const mat = (c: ColorKey, roughness = 0.85) => softMaterial({ color: colorOf(c), roughness });

function boxAt(rect: Rect, y0: number, y1: number, material: THREE.Material): THREE.Mesh {
  const [x0, z0, x1, z1] = rect;
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0, y1 - y0, z1 - z0), material);
  mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  mesh.receiveShadow = true;
  return mesh;
}

/** 牆面上的平面物件：中心 (x,y,z)、法線 (nx,nz) */
function wallPlane(w: number, h: number, x: number, y: number, z: number, nx: number, nz: number, material: THREE.Material): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), material);
  mesh.position.set(x, y, z);
  mesh.rotation.y = Math.atan2(nx, nz);
  return mesh;
}

const flatMat = (map: THREE.Texture) => new THREE.MeshBasicMaterial({ map, toneMapped: false });

function buildShell(scene: THREE.Scene): void {
  const floorBase: Record<string, [string, 'plank' | 'tile']> = {
    floor: [PALETTE.floor, 'plank'], carpet: ['#c99b6d', 'plank'],
    carpetWarm: [PALETTE.carpetWarm, 'tile'], light: ['#e2c9a6', 'plank'],
  };
  for (const room of ROOMS) {
    const [x0, z0, x1, z1] = room.rect;
    const [base, kind] = floorBase[room.floor];
    const tex = floorTexture(base, kind);
    tex.repeat.set((x1 - x0) / (kind === 'plank' ? 2 : 1), (z1 - z0) / (kind === 'plank' ? 2 : 1));
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, z1 - z0), softMaterial({ map: tex, roughness: kind === 'plank' ? 0.55 : 0.95 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.set((x0 + x1) / 2, 0, (z0 + z1) / 2);
    floor.receiveShadow = true;
    scene.add(floor);
    // 天花板 + 燈板
    const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, z1 - z0), new THREE.MeshBasicMaterial({ color: '#efe4d3' }));
    ceiling.rotation.x = Math.PI / 2;
    ceiling.position.set((x0 + x1) / 2, WALL_HEIGHT, (z0 + z1) / 2);
    scene.add(ceiling);
    const panel = new THREE.MeshBasicMaterial({ color: '#fffef6' });
    for (let px = x0 + 2; px < x1 - 1; px += 3) {
      for (const pz of [2.2, 5.8]) {
        const light = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.02, 0.6), panel);
        light.position.set(px, WALL_HEIGHT - 0.012, pz);
        scene.add(light);
      }
    }
  }
  const wallMat = mat('wall');
  const baseMat = mat('woodDark', 0.6);
  const wainscot = mat('wallWarm', 0.7);
  for (const rect of wallRects()) {
    scene.add(boxAt(rect, 0, WALL_HEIGHT, wallMat));
    const [x0, z0, x1, z1] = rect;
    const grow = (d: number): Rect => [x0 - d, z0 - d, x1 + d, z1 + d];
    // 腰壁＋扶手線＋踢腳板：牆面分段，近看才有「室內」的密度
    scene.add(boxAt(grow(0.01), 0, 0.92, wainscot));
    scene.add(boxAt(grow(0.022), 0.9, 0.95, baseMat));
    scene.add(boxAt(grow(0.016), 0, 0.1, baseMat));
    // 天花板收邊
    scene.add(boxAt(grow(0.03), WALL_HEIGHT - 0.08, WALL_HEIGHT, mat('white', 0.9)));
  }
  const trim = mat('woodDark');
  for (const { rect, x } of lintelRects()) {
    scene.add(boxAt(rect, DOOR_HEIGHT, WALL_HEIGHT, wallMat));
    // 門框
    for (const z of DOOR_Z) scene.add(boxAt([x - 0.11, z - 0.04, x + 0.11, z + 0.04], 0, DOOR_HEIGHT, trim));
    scene.add(boxAt([x - 0.11, DOOR_Z[0] - 0.04, x + 0.11, DOOR_Z[1] + 0.04], DOOR_HEIGHT - 0.06, DOOR_HEIGHT + 0.02, trim));
  }
  // 面接室：面試官背後的木紋牆（投影幕掛在上面）
  scene.add(boxAt([21.9, 0.1, 21.92, 5.15], 0.09, WALL_HEIGHT, mat('wood')));
}

/** 地毯：面接室桌下、待合室沙發前 */
function buildRugs(scene: THREE.Scene): void {
  const rugs: [number, number, number, number, string, string][] = [
    [18.9, 3.4, 3.4, 2.4, PALETTE.carpet, '#6f8a7c'],
    [10.9, 5.0, 2.6, 1.8, PALETTE.carpetWarm, '#b07a52'],
  ];
  for (const [x, z, w, d, base, border] of rugs) {
    const rug = new THREE.Mesh(new THREE.PlaneGeometry(w, d), softMaterial({ map: rugTexture(base, border), roughness: 1 }));
    rug.rotation.x = -Math.PI / 2;
    rug.position.set(x, 0.006, z);
    rug.receiveShadow = true;
    scene.add(rug);
  }
}

/** 南窗斜射進來的日光：投在地板上的暖色平行四邊形（加法混合）。
 *  牆不投影（否則整間被南牆遮成陰天），所以用貼地光斑補出「陽光從窗戶照進來」。 */
function buildSunPatches(scene: THREE.Scene, windows: [number, number][], sunDir: THREE.Vector3): void {
  const material = new THREE.MeshBasicMaterial({
    color: '#ffcf8f', transparent: true, opacity: 0.32, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false,
  });
  const zWin = 8 - WALL_T / 2;
  const hit = (x: number, y: number): [number, number] => {
    const t = y / -sunDir.y;
    return [x + sunDir.x * t, zWin + sunDir.z * t];
  };
  for (const [a, b] of windows) {
    const panes = Math.max(1, Math.round((b - a) / 1.2));
    const pw = (b - a) / panes;
    for (let i = 0; i < panes; i++) {
      const x0 = a + i * pw + 0.04, x1 = a + (i + 1) * pw - 0.04;
      const pts = [hit(x0, 0.98), hit(x1, 0.98), hit(x1, 2.32), hit(x0, 2.32)];
      const shape = new THREE.Shape(pts.map(([x, z]) => new THREE.Vector2(x, -z)));
      const patch = new THREE.Mesh(new THREE.ShapeGeometry(shape), material);
      patch.rotation.x = -Math.PI / 2;
      patch.position.y = 0.012;
      scene.add(patch);
    }
  }
}

export const SUN_DIR = new THREE.Vector3(8, -9, -17).normalize();
const WINDOWS: [number, number][] = [[12.0, 13.7], [15.4, 20.6]];

function buildWindows(scene: THREE.Scene): void {
  const sky = flatMat(skylineTexture());
  const frame = mat('black');
  const zIn = 8 - WALL_T / 2 - 0.006;
  for (const [a, b] of WINDOWS) {
    const w = b - a, cx = (a + b) / 2;
    scene.add(wallPlane(w, 1.4, cx, 1.65, zIn, 0, -1, sky));
    scene.add(boxAt([a - 0.04, zIn - 0.05, b + 0.04, zIn], 0.92, 0.98, frame));
    scene.add(boxAt([a - 0.04, zIn - 0.05, b + 0.04, zIn], 2.32, 2.38, frame));
    for (let x = a; x <= b + 1e-6; x += w / Math.max(1, Math.round(w / 1.2))) {
      scene.add(boxAt([x - 0.025, zIn - 0.05, x + 0.025, zIn], 0.95, 2.35, frame));
    }
  }
  // 受付西側：玻璃入口（看得到外面街景）
  const door = wallPlane(2.0, 2.2, WALL_T / 2 + 0.006, 1.1, (DOOR_Z[0] + DOOR_Z[1]) / 2, 1, 0, sky);
  scene.add(door);
  for (const z of [DOOR_Z[0], (DOOR_Z[0] + DOOR_Z[1]) / 2, DOOR_Z[1]]) {
    scene.add(boxAt([WALL_T / 2, z - 0.03, WALL_T / 2 + 0.05, z + 0.03], 0, 2.2, frame));
  }
  scene.add(boxAt([WALL_T / 2, DOOR_Z[0], WALL_T / 2 + 0.05, DOOR_Z[1]], 2.2, 2.26, frame));
}

function buildBoard(b: Board, scene: THREE.Scene, pickables: THREE.Object3D[]): THREE.Mesh {
  const frame = new THREE.Mesh(new THREE.BoxGeometry(b.w + 0.1, b.h + 0.1, 0.04), mat('woodDark'));
  frame.position.set(b.x + b.nx * 0.0, b.y, b.z + b.nz * 0.0);
  frame.rotation.y = Math.atan2(b.nx, b.nz);
  scene.add(frame);
  const face = wallPlane(b.w, b.h, b.x + b.nx * 0.025, b.y, b.z + b.nz * 0.025, b.nx, b.nz, new THREE.MeshBasicMaterial({ color: '#eeeeee', toneMapped: false }));
  const pick: Pick = b.id === 'screen' ? { type: 'screen' } : { type: 'board', id: b.id };
  face.userData.pick = pick;
  frame.userData.pick = pick;
  scene.add(face);
  pickables.push(face, frame);
  return face;
}

function buildClock(scene: THREE.Scene): BuiltFloor['clockHands'] {
  const g = new THREE.Group();
  g.position.set(17.6, 2.3, WALL_T / 2 + 0.03);
  const rim = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.04, 32), mat('black'));
  rim.rotation.x = Math.PI / 2;
  const dial = new THREE.Mesh(new THREE.CircleGeometry(0.18, 32), new THREE.MeshBasicMaterial({ color: '#fbfbf7' }));
  dial.position.z = 0.021;
  g.add(rim, dial);
  for (let i = 0; i < 12; i++) {
    const tick = new THREE.Mesh(new THREE.PlaneGeometry(0.012, i % 3 ? 0.025 : 0.045), new THREE.MeshBasicMaterial({ color: PALETTE.ink }));
    const a = (i / 12) * Math.PI * 2;
    tick.position.set(Math.sin(a) * 0.15, Math.cos(a) * 0.15, 0.022);
    tick.rotation.z = -a;
    g.add(tick);
  }
  const hand = (len: number, w: number) => {
    const pivot = new THREE.Object3D();
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, len), new THREE.MeshBasicMaterial({ color: PALETTE.ink }));
    m.position.y = len / 2 - 0.015;
    pivot.add(m);
    pivot.position.z = 0.024;
    g.add(pivot);
    return pivot;
  };
  const hour = hand(0.1, 0.018);
  const minute = hand(0.15, 0.011);
  scene.add(g);
  return { hour, minute };
}

export function buildFloor(scene: THREE.Scene): BuiltFloor {
  const pickables: THREE.Object3D[] = [];
  buildShell(scene);
  buildWindows(scene);
  buildRugs(scene);
  buildSunPatches(scene, WINDOWS, SUN_DIR);

  for (const f of FURNITURE) {
    const model = buildModel(furnitureModel(f.kind));
    model.position.set(f.x, 0, f.z);
    model.rotation.y = f.rot;
    if (f.kind === 'chairCandidate') {
      model.userData.pick = { type: 'seat' } satisfies Pick;
      pickables.push(model);
    }
    scene.add(model);
  }

  const interviewer = new Interviewer();
  interviewer.group.position.set(INTERVIEWER.x - 0.05, 0, INTERVIEWER.z);
  interviewer.group.rotation.y = -Math.PI / 2;
  interviewer.group.userData.pick = { type: 'seat' } satisfies Pick;
  pickables.push(interviewer.group);
  scene.add(interviewer.group);

  const receptionist = new Receptionist();
  receptionist.group.position.set(RECEPTIONIST.x, 0, RECEPTIONIST.z);
  scene.add(receptionist.group);

  const boardFaces = new Map<string, THREE.Mesh>();
  for (const b of BOARDS) boardFaces.set(b.id, buildBoard(b, scene, pickables));

  const screenFace = buildBoard(SCREEN, scene, pickables);
  const [, ctx, tex] = screenCanvas();
  (screenFace.material as THREE.MeshBasicMaterial).map = tex;
  (screenFace.material as THREE.MeshBasicMaterial).color.set('#ffffff');

  const doorSigns: BuiltFloor['doorSigns'] = [];
  const signY = (DOOR_HEIGHT + WALL_HEIGHT) / 2;
  const signZ = (DOOR_Z[0] + DOOR_Z[1]) / 2;
  for (const { x } of lintelRects()) {
    for (const side of [-1, 1] as const) {
      const mesh = wallPlane(1.5, 0.32, x + side * (WALL_T / 2 + 0.004), signY, signZ, side, 0, new THREE.MeshBasicMaterial({ toneMapped: false }));
      scene.add(mesh);
      // 西側那面（side = -1）看到的是「往東」要去的房間
      doorSigns.push({ mesh, target: side < 0 ? 'east' : 'west', x });
    }
  }

  const counter = FURNITURE.find((f) => f.kind === 'counter');
  const counterSign = wallPlane(2.3, 0.46, counter?.x ?? 4.2, 0.62, (counter?.z ?? 3.8) + 0.37, 0, 1, new THREE.MeshBasicMaterial({ toneMapped: false }));
  scene.add(counterSign);

  // 掛畫（裝飾，不可點）：面接室北牆、待合室西牆、振り返り室南牆門邊
  const arts: [number, number, number, number, number][] = [
    [15.7, 1.65, WALL_T / 2 + 0.02, 0, 1],
    [8 + WALL_T / 2 + 0.02, 1.65, 2.4, 1, 0],
    [22 + WALL_T / 2 + 0.02, 1.65, 2.6, 1, 0],
  ];
  arts.forEach(([x, y, z, nx, nz], i) => {
    const frame = new THREE.Mesh(new THREE.BoxGeometry(0.78, 0.96, 0.035), mat('gold'));
    frame.position.set(x, y, z);
    frame.rotation.y = Math.atan2(nx, nz);
    scene.add(frame);
    scene.add(wallPlane(0.66, 0.84, x + nx * 0.02, y, z + nz * 0.02, nx, nz, flatMat(artTexture(i + 3))));
  });

  const clockHands = buildClock(scene);
  return { boardFaces, screen: { face: screenFace, ctx, tex }, doorSigns, counterSign, interviewer, receptionist, clockHands, pickables };
}
