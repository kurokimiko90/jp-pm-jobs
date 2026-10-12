// Canvas 繪製的貼圖：展示板、門牌、窗外街景、地板紋理、投影畫面。
// 長文不畫進 3D（日文縮小會糊）——板面只放標題＋前幾行摘要，全文開 2D 面板讀。
import * as THREE from 'three';
import { PALETTE } from './dsl';

const FONT = '"Noto Sans JP", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif';

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d');
  if (!ctx) throw new Error('2D canvas 不可用');
  return [c, ctx];
}

function toTexture(c: HTMLCanvasElement): THREE.CanvasTexture {
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

/** 逐字換行（日文無空白，按字寬切） */
export function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const out: string[] = [];
  let line = '';
  for (const ch of text) {
    if (ctx.measureText(line + ch).width > maxWidth && line) {
      out.push(line);
      line = ch;
    } else {
      line += ch;
    }
  }
  if (line) out.push(line);
  return out;
}

/** markdown → 板面摘要行（去語法、去空行與表格分隔線） */
export function mdPreview(md: string, maxLines = 12): string[] {
  const lines: string[] = [];
  let inCode = false;
  for (const raw of md.split('\n')) {
    if (raw.trim().startsWith('```')) { inCode = !inCode; continue; }
    if (inCode) continue;
    let s = raw.trim();
    if (!s || /^[-|: ]+$/.test(s) || /^<!--/.test(s)) continue;
    const heading = /^#{1,6}\s/.test(s);
    s = s
      .replace(/^#{1,6}\s+/, '')
      .replace(/^>\s?/, '')
      .replace(/^[-*+]\s+/, '・')
      .replace(/^\|(.*)\|$/, '$1').replace(/\s*\|\s*/g, ' / ')
      .replace(/\*\*(.+?)\*\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
    if (!s) continue;
    lines.push(heading ? `■ ${s}` : s);
    if (lines.length >= maxLines) break;
  }
  return lines;
}

export interface BoardPaint {
  title: string;
  kicker: string;
  lines: string[];
  available: boolean;
  emptyLabel: string;
  hint: string;
}

/** 展示板：上方色帶標題、下方摘要；未生成時灰底 */
export function boardTexture(p: BoardPaint): THREE.CanvasTexture {
  const W = 1024, H = 700;
  const [c, ctx] = canvas(W, H);
  ctx.fillStyle = p.available ? '#fffdf8' : '#e9ecee';
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = p.available ? PALETTE.navy : '#9aa5ad';
  ctx.fillRect(0, 0, W, 150);
  ctx.fillStyle = PALETTE.gold;
  ctx.fillRect(0, 150, W, 8);
  ctx.fillStyle = 'rgba(255,255,255,0.75)';
  ctx.font = `600 30px ${FONT}`;
  ctx.fillText(p.kicker, 48, 56);
  ctx.fillStyle = '#ffffff';
  ctx.font = `700 60px ${FONT}`;
  ctx.fillText(p.title, 48, 124);
  if (!p.available) {
    ctx.fillStyle = '#6b7780';
    ctx.font = `600 44px ${FONT}`;
    ctx.fillText(p.emptyLabel, 48, 300);
  } else {
    ctx.fillStyle = '#2f3b45';
    ctx.font = `400 36px ${FONT}`;
    let y = 226;
    for (const line of p.lines) {
      ctx.font = `400 36px ${FONT}`;
      for (const seg of wrapText(ctx, line, W - 96).slice(0, 2)) {
        if (y > H - 80) break;
        ctx.fillStyle = line.startsWith('■') ? PALETTE.navy : '#2f3b45';
        ctx.font = line.startsWith('■') ? `700 38px ${FONT}` : `400 36px ${FONT}`;
        ctx.fillText(seg, 48, y);
        y += 52;
      }
    }
    // 底部淡出 + 操作提示
    const fade = ctx.createLinearGradient(0, H - 150, 0, H - 50);
    fade.addColorStop(0, 'rgba(255,253,248,0)');
    fade.addColorStop(1, 'rgba(255,253,248,1)');
    ctx.fillStyle = fade;
    ctx.fillRect(0, H - 150, W, 100);
  }
  ctx.fillStyle = p.available ? PALETTE.gold : '#9aa5ad';
  ctx.fillRect(0, H - 56, W, 56);
  ctx.fillStyle = p.available ? '#2f3b45' : '#ffffff';
  ctx.font = `700 28px ${FONT}`;
  ctx.fillText(p.hint, 48, H - 18);
  return toTexture(c);
}

/** 門牌 / 招牌：單行字，深底金字 */
export function signTexture(text: string, opts: { w?: number; h?: number; bg?: string; fg?: string; size?: number } = {}): THREE.CanvasTexture {
  const W = opts.w ?? 768, H = opts.h ?? 160;
  const [c, ctx] = canvas(W, H);
  ctx.fillStyle = opts.bg ?? PALETTE.navy;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = opts.fg ?? '#ffffff';
  ctx.font = `700 ${opts.size ?? 72}px ${FONT}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, W / 2, H / 2 + 4, W - 40);
  return toTexture(c);
}

/** 窗外：銀座風的傍晚街景剪影（致敬 Ginza 專案），固定亂數種子 */
export function skylineTexture(seed = 7): THREE.CanvasTexture {
  const W = 1024, H = 512;
  const [c, ctx] = canvas(W, H);
  const sky = ctx.createLinearGradient(0, 0, 0, H);
  sky.addColorStop(0, '#8fb8de');
  sky.addColorStop(0.65, '#d9e7f2');
  sky.addColorStop(1, '#f3e6d0');
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, W, H);
  let s = seed;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  for (const [layer, tone] of [[0, '#a9b8c6'], [1, '#7f93a6']] as const) {
    let x = -20;
    while (x < W) {
      const w = 60 + rand() * 120;
      const h = (layer ? 160 : 230) + rand() * (layer ? 160 : 180);
      ctx.fillStyle = tone;
      ctx.fillRect(x, H - h, w, h);
      if (layer) {
        ctx.fillStyle = 'rgba(255,248,225,0.55)';
        for (let wy = H - h + 14; wy < H - 10; wy += 22) {
          for (let wx = x + 8; wx < x + w - 12; wx += 18) if (rand() > 0.45) ctx.fillRect(wx, wy, 9, 11);
        }
      }
      x += w + 6 + rand() * 18;
    }
  }
  return toTexture(c);
}

/** 地板紋理：木地板（長條）或地毯（方塊） */
export function floorTexture(base: string, kind: 'plank' | 'tile'): THREE.CanvasTexture {
  const S = 256;
  const [c, ctx] = canvas(S, S);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, S, S);
  ctx.strokeStyle = 'rgba(0,0,0,0.09)';
  ctx.lineWidth = 2;
  if (kind === 'plank') {
    // 每片木板深淺略不同＋細木紋，近看才不像一張紙
    for (let y = 0, i = 0; y < S; y += 32, i++) {
      ctx.fillStyle = `rgba(${i % 3 ? '255,240,220' : '90,50,20'},${0.04 + (i % 4) * 0.015})`;
      ctx.fillRect(0, y, S, 32);
      ctx.strokeStyle = 'rgba(80,45,15,0.06)';
      ctx.lineWidth = 1;
      for (let g = 0; g < 4; g++) {
        ctx.beginPath();
        ctx.moveTo(0, y + 6 + g * 7);
        ctx.bezierCurveTo(S * 0.3, y + 4 + g * 7 + (i % 3), S * 0.6, y + 9 + g * 7, S, y + 6 + g * 7);
        ctx.stroke();
      }
    }
    ctx.strokeStyle = 'rgba(60,30,10,0.16)';
    ctx.lineWidth = 2;
    for (let y = 0; y <= S; y += 32) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(S, y); ctx.stroke();
      const off = (y / 32) % 2 ? 96 : 200;
      ctx.beginPath(); ctx.moveTo(off, y); ctx.lineTo(off, y + 32); ctx.stroke();
    }
  } else {
    ctx.strokeRect(1, 1, S - 2, S - 2);
    ctx.fillStyle = 'rgba(255,255,255,0.035)';
    for (let i = 0; i < 400; i++) ctx.fillRect((i * 97) % S, (i * 57) % S, 2, 2);
  }
  const tex = toTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  return tex;
}

/** 地毯：底色＋內框兩道邊＋淡淡的織紋 */
export function rugTexture(base: string, border: string): THREE.CanvasTexture {
  const W = 512, H = 384;
  const [c, ctx] = canvas(W, H);
  ctx.fillStyle = border;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = base;
  ctx.fillRect(22, 22, W - 44, H - 44);
  ctx.strokeStyle = border;
  ctx.lineWidth = 6;
  ctx.strokeRect(46, 46, W - 92, H - 92);
  ctx.fillStyle = 'rgba(255,255,255,0.05)';
  for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
  return toTexture(c);
}

/** 投影畫面：圖片等比置中 + 頁碼；無圖時顯示待機畫面 */
export function screenCanvas(): [HTMLCanvasElement, CanvasRenderingContext2D, THREE.CanvasTexture] {
  const [c, ctx] = canvas(1280, 720);
  return [c, ctx, toTexture(c)];
}

export function paintScreen(ctx: CanvasRenderingContext2D, img: HTMLImageElement | null, caption: string): void {
  const W = ctx.canvas.width, H = ctx.canvas.height;
  ctx.fillStyle = img ? '#ffffff' : '#1d2733';
  ctx.fillRect(0, 0, W, H);
  if (img) {
    const k = Math.min(W / img.width, (H - 50) / img.height);
    const w = img.width * k, h = img.height * k;
    ctx.drawImage(img, (W - w) / 2, (H - 50 - h) / 2, w, h);
  }
  ctx.fillStyle = img ? 'rgba(29,39,51,0.85)' : 'rgba(255,255,255,0.8)';
  ctx.font = `600 ${img ? 26 : 44}px ${FONT}`;
  ctx.textAlign = 'center';
  ctx.fillText(caption, W / 2, img ? H - 16 : H / 2);
  ctx.textAlign = 'left';
}

export interface AgendaPaint {
  kicker: string;
  company: string;
  jobTitle: string;
  acts: string[];
  /** 進行中の幕（着席前は -1） */
  current: number;
  footer: string;
}

/** 提案スライドが無いときの投影画面：会社名＋職種＋幕構成（進行中の幕を強調） */
export function paintAgenda(ctx: CanvasRenderingContext2D, p: AgendaPaint): void {
  const W = ctx.canvas.width, H = ctx.canvas.height;
  ctx.fillStyle = '#fbf8f2';
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = PALETTE.navy;
  ctx.fillRect(0, 0, 18, H);
  ctx.fillStyle = PALETTE.gold;
  ctx.fillRect(18, 0, 6, H);
  const x = 84;
  ctx.fillStyle = '#8a7a5c';
  ctx.font = `700 28px ${FONT}`;
  ctx.fillText(p.kicker, x, 86);
  ctx.fillStyle = PALETTE.navy;
  ctx.font = `800 58px ${FONT}`;
  ctx.fillText(wrapText(ctx, p.company, W - x * 2)[0] ?? '', x, 160);
  ctx.fillStyle = '#4a5560';
  ctx.font = `500 30px ${FONT}`;
  ctx.fillText(wrapText(ctx, p.jobTitle, W - x * 2)[0] ?? '', x, 210);
  ctx.fillStyle = 'rgba(46,63,92,0.18)';
  ctx.fillRect(x, 244, W - x * 2, 2);
  const rows = p.acts.slice(0, 10);
  const rowH = Math.min(62, (H - 330) / Math.max(1, rows.length));
  rows.forEach((title, i) => {
    const y = 296 + i * rowH;
    const active = i === p.current;
    const done = p.current >= 0 && i < p.current;
    if (active) {
      ctx.fillStyle = 'rgba(201,162,39,0.18)';
      ctx.fillRect(x - 20, y - rowH * 0.62, W - x * 2 + 40, rowH * 0.9);
    }
    ctx.fillStyle = active ? PALETTE.gold : done ? '#b8bfc6' : PALETTE.navy;
    ctx.beginPath();
    ctx.arc(x + 14, y - 11, active ? 11 : 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = active ? '#1d2733' : done ? '#9aa3ab' : '#2f3b45';
    ctx.font = `${active ? 700 : 500} 32px ${FONT}`;
    ctx.fillText(wrapText(ctx, title, W - x * 2 - 60)[0] ?? '', x + 50, y);
  });
  ctx.fillStyle = '#a39a8a';
  ctx.font = `500 22px ${FONT}`;
  ctx.textAlign = 'right';
  ctx.fillText(p.footer, W - 48, H - 30);
  ctx.textAlign = 'left';
}

/** 牆上掛畫：柔和色塊的抽象畫（固定種子，每幅不同） */
export function artTexture(seed: number): THREE.CanvasTexture {
  const W = 512, H = 640;
  const [c, ctx] = canvas(W, H);
  let s = seed * 9973 + 17;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const tones = ['#e8d5b7', '#c9a227', '#5c6bc0', '#d06a5f', '#5b9a63', '#2e3f5c', '#f4ede2'];
  ctx.fillStyle = '#f7f2e8';
  ctx.fillRect(0, 0, W, H);
  for (let i = 0; i < 7; i++) {
    ctx.globalAlpha = 0.55 + rand() * 0.4;
    ctx.fillStyle = tones[Math.floor(rand() * tones.length)];
    if (rand() > 0.5) {
      ctx.beginPath();
      ctx.arc(rand() * W, rand() * H, 50 + rand() * 140, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.fillRect(rand() * W * 0.7, rand() * H * 0.7, 80 + rand() * 260, 60 + rand() * 300);
    }
  }
  ctx.globalAlpha = 1;
  return toTexture(c);
}
