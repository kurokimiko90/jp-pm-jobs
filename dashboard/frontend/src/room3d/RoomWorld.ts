// three.js 引擎（與 React 分離，架構同 la-game-ginza3d 的 CityWorld）：
// 第一人稱走動（WASD / 拖曳轉頭 / 碰撞沿牆滑）、坐上面接席（視角受限）、raycast 點選、鏡頭飛行。
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/examples/jsm/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { SUN_DIR, buildFloor, type BuiltFloor, type Pick } from './building';
import { createCollider, type Collider } from './collision';
import { BOARDS, ROOMS, SEAT, furnitureRects, roomAt, viewpoint, wallRects, type RoomId } from './layout';
import { type InterviewerState } from './interviewer';
import { boardTexture, paintAgenda, paintScreen, signTexture, type AgendaPaint, type BoardPaint } from './textures';

export type { Pick };
export type WorldMode = 'walk' | 'seat';

export interface WorldCallbacks {
  onPick(pick: Pick): void;
  onHover(pick: Pick | null): void;
  onRoom(room: RoomId): void;
  onMode(mode: WorldMode): void;
}

const EYE = 1.6;
const RADIUS = 0.28;
const WALK = 2.2;
const RUN = 4.0;
const LOOK = 0.0042;
const SEAT_YAW = 1.15;
const SEAT_PITCH = 0.5;
const MOVE_KEYS: Record<string, [number, number]> = {
  KeyW: [0, 1], ArrowUp: [0, 1], KeyS: [0, -1], ArrowDown: [0, -1],
  KeyA: [-1, 0], ArrowLeft: [-1, 0], KeyD: [1, 0], ArrowRight: [1, 0],
};

interface Pose { x: number; y: number; z: number; yaw: number; pitch: number }
interface Flight { from: Pose; to: Pose; t: number; dur: number; then?: () => void }

const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
const wrapAngle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
}

export class RoomWorld {
  private renderer: THREE.WebGLRenderer;
  private composer: EffectComposer;
  private ao: GTAOPass;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(62, 1, 0.05, 80);
  private floor: BuiltFloor;
  private collider: Collider;
  private pose: Pose;
  private mode: WorldMode = 'walk';
  private flight: Flight | null = null;
  private keys = new Set<string>();
  private touchMove: [number, number] = [0, 0];
  private inputEnabled = true;
  private room: RoomId = 'lobby';
  private raf = 0;
  private last = performance.now();
  private start = performance.now();
  private drag: { x: number; y: number; moved: number; id: number } | null = null;
  private hoverAt: [number, number] | null = null;
  private hovered: Pick | null = null;
  private raycaster = new THREE.Raycaster();
  private resizeObserver: ResizeObserver;
  private disposers: (() => void)[] = [];

  constructor(private container: HTMLElement, private cb: WorldCallbacks) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.95;
    this.renderer.domElement.style.display = 'block';
    this.renderer.domElement.style.touchAction = 'none';
    container.prepend(this.renderer.domElement);

    this.scene.background = new THREE.Color('#f3e6d4');
    this.addLights();
    // 環境光遮蔽：牆角、家具底下、衣領縫隙的柔和暗部 — 立體感主要來源（Ginza 同理）
    // 多重取樣 render target：走後製也保留抗鋸齒
    this.composer = new EffectComposer(this.renderer, new THREE.WebGLRenderTarget(1, 1, { samples: 4, type: THREE.HalfFloatType }));
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.ao = new GTAOPass(this.scene, this.camera, 1, 1);
    this.ao.updateGtaoMaterial({ radius: 0.45, distanceExponent: 1.5, thickness: 1, scale: 1 });
    this.ao.blendIntensity = 0.85;
    this.composer.addPass(this.ao);
    this.composer.addPass(new OutputPass());
    this.floor = buildFloor(this.scene);
    this.collider = createCollider([...wallRects(), ...furnitureRects()]);
    const spawn = ROOMS[0].spawn;
    this.pose = { x: spawn.x, y: EYE, z: spawn.z, yaw: spawn.yaw, pitch: -0.05 };
    this.camera.rotation.order = 'YXZ';

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    this.bindInput();
    this.tick = this.tick.bind(this);
    this.raf = requestAnimationFrame(this.tick);
  }

  // Ginza 同款：環境反射打底（柔和、有立體感），半球光壓低避免整片泛白，
  // 主光模擬南窗斜射的午後陽光（暖色、軟陰影），每間房再補一盞暖色頂燈。
  private addLights(): void {
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    this.scene.environment = env;
    this.scene.environmentIntensity = 0.45;
    this.scene.add(new THREE.HemisphereLight('#fff6e8', '#b88a62', 0.55));
    const sun = new THREE.DirectionalLight('#ffe2b8', 2.6);
    sun.target.position.set(14, 0, 3);
    sun.position.copy(sun.target.position).addScaledVector(SUN_DIR, -20);
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 2048);
    Object.assign(sun.shadow.camera, { left: -17, right: 17, top: 10, bottom: -10, near: 1, far: 60 });
    sun.shadow.intensity = 0.7;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.02;
    this.scene.add(sun, sun.target);
    for (const room of ROOMS) {
      const [x0, z0, x1, z1] = room.rect;
      const lamp = new THREE.PointLight('#ffd9a8', 5, 9, 1.6);
      lamp.position.set((x0 + x1) / 2, 2.6, (z0 + z1) / 2);
      this.scene.add(lamp);
    }
  }

  private resize(): void {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.composer.setSize(w, h);
    this.composer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.camera.aspect = w / h;
    // 直向（手機）時加大垂直視角，橫向視野才不會只剩一條
    this.camera.fov = w / h < 1 ? 78 : 62;
    this.camera.updateProjectionMatrix();
  }

  private bindInput(): void {
    const el = this.container;
    const canvas = this.renderer.domElement;
    const on = <K extends keyof HTMLElementEventMap>(t: HTMLElement, type: K, fn: (e: HTMLElementEventMap[K]) => void) => {
      t.addEventListener(type, fn as EventListener);
      this.disposers.push(() => t.removeEventListener(type, fn as EventListener));
    };
    on(el, 'keydown', (e) => {
      if (isTyping(e.target) || !this.inputEnabled) return;
      if (MOVE_KEYS[e.code] || e.code === 'ShiftLeft' || e.code === 'ShiftRight') {
        if (this.mode === 'walk') e.preventDefault();
        this.keys.add(e.code);
      }
    });
    on(el, 'keyup', (e) => this.keys.delete(e.code));
    on(el, 'blur', () => this.keys.clear());
    on(canvas, 'pointerdown', (e) => {
      el.focus({ preventScroll: true });
      this.drag = { x: e.clientX, y: e.clientY, moved: 0, id: e.pointerId };
      canvas.setPointerCapture(e.pointerId);
    });
    on(canvas, 'pointermove', (e) => {
      if (this.drag && this.drag.id === e.pointerId) {
        const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
        this.drag.x = e.clientX;
        this.drag.y = e.clientY;
        this.drag.moved += Math.abs(dx) + Math.abs(dy);
        if (!this.flight && this.drag.moved > 4) this.look(dx, dy);
      } else {
        this.hoverAt = [e.clientX, e.clientY];
      }
    });
    on(canvas, 'pointerup', (e) => {
      const d = this.drag;
      this.drag = null;
      if (d && d.moved <= 4 && !this.flight) {
        const pick = this.pickAt(e.clientX, e.clientY);
        if (pick) this.cb.onPick(pick);
      }
    });
    on(canvas, 'pointerleave', () => { this.hoverAt = null; this.setHover(null); });
  }

  private look(dx: number, dy: number): void {
    this.pose.yaw -= dx * LOOK;
    this.pose.pitch = THREE.MathUtils.clamp(this.pose.pitch - dy * LOOK, -1.1, 1.1);
    if (this.mode === 'seat') {
      this.pose.yaw = SEAT.yaw + THREE.MathUtils.clamp(wrapAngle(this.pose.yaw - SEAT.yaw), -SEAT_YAW, SEAT_YAW);
      this.pose.pitch = THREE.MathUtils.clamp(this.pose.pitch, -SEAT_PITCH, SEAT_PITCH);
    }
  }

  private pickAt(clientX: number, clientY: number): Pick | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    this.raycaster.far = 14;
    for (const hit of this.raycaster.intersectObjects(this.floor.pickables, true)) {
      let o: THREE.Object3D | null = hit.object;
      while (o && !o.userData.pick) o = o.parent;
      if (o) {
        const pick = o.userData.pick as Pick;
        // 坐著時不再觸發「坐下」
        if (pick.type === 'seat' && this.mode === 'seat') return null;
        return pick;
      }
    }
    return null;
  }

  private setHover(pick: Pick | null): void {
    const same = JSON.stringify(pick) === JSON.stringify(this.hovered);
    if (same) return;
    this.hovered = pick;
    this.renderer.domElement.style.cursor = pick ? 'pointer' : 'grab';
    this.cb.onHover(pick);
  }

  private flyTo(to: Pose, dur = 0.8, then?: () => void): void {
    const from = { ...this.pose };
    const target = { ...to, yaw: from.yaw + wrapAngle(to.yaw - from.yaw) };
    this.flight = { from, to: target, t: 0, dur, then };
  }

  private tick(now: number): void {
    this.raf = requestAnimationFrame(this.tick);
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    const t = (now - this.start) / 1000;

    if (this.flight) {
      const f = this.flight;
      f.t = Math.min(1, f.t + dt / f.dur);
      const k = ease(f.t);
      for (const key of ['x', 'y', 'z', 'yaw', 'pitch'] as const) this.pose[key] = f.from[key] + (f.to[key] - f.from[key]) * k;
      if (f.t >= 1) { this.flight = null; f.then?.(); }
    } else if (this.mode === 'walk' && this.inputEnabled) {
      this.walk(dt);
    }

    const { x, y, z, yaw, pitch } = this.pose;
    this.camera.position.set(x, y, z);
    this.camera.rotation.set(pitch, yaw, 0);

    const room = roomAt(x);
    if (room !== this.room) { this.room = room; this.cb.onRoom(room); }

    if (this.hoverAt && !this.drag) {
      this.setHover(this.pickAt(...this.hoverAt));
      this.hoverAt = null;
    }

    this.floor.interviewer.lookAt(this.camera.position);
    this.floor.interviewer.update(t, dt);
    this.floor.receptionist.lookAt(this.camera.position);
    this.floor.receptionist.update(t, dt);
    const d = new Date();
    this.floor.clockHands.minute.rotation.z = -((d.getMinutes() + d.getSeconds() / 60) / 60) * Math.PI * 2;
    this.floor.clockHands.hour.rotation.z = -(((d.getHours() % 12) + d.getMinutes() / 60) / 12) * Math.PI * 2;
    this.composer.render();
  }

  private walk(dt: number): void {
    let fx = this.touchMove[0], fz = this.touchMove[1];
    for (const k of this.keys) {
      const v = MOVE_KEYS[k];
      if (v) { fx += v[0]; fz += v[1]; }
    }
    const len = Math.hypot(fx, fz);
    if (!len) return;
    const speed = (this.keys.has('ShiftLeft') || this.keys.has('ShiftRight') ? RUN : WALK) * dt / len;
    const { yaw } = this.pose;
    // 前 = (-sin yaw, -cos yaw)、右 = (cos yaw, -sin yaw)
    const dx = (-Math.sin(yaw) * fz + Math.cos(yaw) * fx) * speed;
    const dz = (-Math.cos(yaw) * fz - Math.sin(yaw) * fx) * speed;
    [this.pose.x, this.pose.z] = this.collider.move(this.pose.x, this.pose.z, dx, dz, RADIUS);
  }

  // ---- public API（React 端呼叫） ----

  setBoard(id: string, paint: BoardPaint): void {
    const face = this.floor.boardFaces.get(id);
    if (!face) return;
    const m = face.material as THREE.MeshBasicMaterial;
    m.map?.dispose();
    m.map = boardTexture(paint);
    m.color.set('#ffffff');
    m.needsUpdate = true;
  }

  setSigns(roomLabel: (id: RoomId) => string, counterText: string): void {
    for (const s of this.floor.doorSigns) {
      const target = ROOMS.find((r) => (s.target === 'east' ? r.rect[0] === s.x : r.rect[2] === s.x));
      if (!target) continue;
      const m = s.mesh.material as THREE.MeshBasicMaterial;
      m.map?.dispose();
      m.map = signTexture(s.target === 'east' ? `${roomLabel(target.id)}  →` : `←  ${roomLabel(target.id)}`, { size: 64 });
      m.needsUpdate = true;
    }
    const cm = this.floor.counterSign.material as THREE.MeshBasicMaterial;
    cm.map?.dispose();
    cm.map = signTexture(counterText, { w: 1024, h: 205, bg: '#2e3f5c', fg: '#f5d77a', size: 64 });
    cm.needsUpdate = true;
  }

  setSlide(img: HTMLImageElement | null, caption: string): void {
    paintScreen(this.floor.screen.ctx, img, caption);
    this.floor.screen.tex.needsUpdate = true;
  }

  setAgenda(paint: AgendaPaint): void {
    paintAgenda(this.floor.screen.ctx, paint);
    this.floor.screen.tex.needsUpdate = true;
  }

  setInterviewer(state: InterviewerState): void {
    this.floor.interviewer.setState(state);
  }

  setInputEnabled(enabled: boolean): void {
    this.inputEnabled = enabled;
    if (!enabled) this.keys.clear();
  }

  setTouchMove(fx: number, fz: number): void {
    this.touchMove = [fx, fz];
  }

  getMode(): WorldMode {
    return this.mode;
  }

  sit(then?: () => void): void {
    if (this.mode === 'seat') { then?.(); return; }
    this.mode = 'seat';
    this.cb.onMode('seat');
    // 略微俯視：面試官落在畫面上半，下方留給字幕
    this.flyTo({ x: SEAT.x, y: SEAT.y, z: SEAT.z, yaw: SEAT.yaw, pitch: -0.07 }, 1.1, then);
  }

  stand(): void {
    if (this.mode === 'walk') return;
    this.mode = 'walk';
    this.cb.onMode('walk');
    this.flyTo({ x: SEAT.x - 0.9, y: EYE, z: SEAT.z, yaw: this.pose.yaw, pitch: 0 }, 0.6);
  }

  goToRoom(id: RoomId): void {
    const room = ROOMS.find((r) => r.id === id);
    if (!room) return;
    this.mode = 'walk';
    this.cb.onMode('walk');
    this.flyTo({ x: room.spawn.x, y: EYE, z: room.spawn.z, yaw: room.spawn.yaw, pitch: -0.05 }, 1.0);
  }

  focusBoard(id: string, then?: () => void): void {
    const b = BOARDS.find((x) => x.id === id);
    if (!b) return;
    const v = viewpoint(b);
    this.mode = 'walk';
    this.cb.onMode('walk');
    this.flyTo({ x: v.x, y: EYE, z: v.z, yaw: v.yaw, pitch: 0 }, 0.9, then);
  }

  /** 目前位置（小地圖用） */
  getPose(): Readonly<Pose> {
    return this.pose;
  }

  snapshot(): string {
    return this.renderer.domElement.toDataURL('image/png');
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.resizeObserver.disconnect();
    for (const d of this.disposers) d();
    this.scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      mesh.geometry?.dispose();
      const mats = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
      for (const m of mats) {
        (m as THREE.MeshBasicMaterial).map?.dispose();
        m.dispose();
      }
    });
    this.composer.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
