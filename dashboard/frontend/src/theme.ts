import { tStatic } from './i18n';

// btrain design tokens（沿用 ~/Documents/project/btrain/src/constants.ts）
// 色票唯一真實來源。C 物件輸出 CSS custom properties（var() 引用），
// 未來 dark mode 只需覆蓋 :root 變數，不用碰任何 inline style。
const PALETTE = {
  bg: '#FFF8F0',
  card: '#FFFFFF',
  ink: '#5A5248',
  sub: '#6E655B', // WCAG AA 4.9:1（原 #9A9189 只有 2.96:1，次要文字不及格）
  gold: '#F5C842',
  amber: '#C79A1B', // gold 加深，white 卡上可讀（推薦度 60-74 檔）
  milkTea: '#E8D5B7',
  lavender: '#C4A8E8',
  rosePink: '#F2B5C0',
  // 狀態色
  draftGray: '#9A9189',
  progressIndigo: '#7DA1E0',
  appliedInk: '#3E5C8A', // 深靛：應募實心 badge 底色（白字可讀）
  successGreen: '#3C6440',
  doneGold: '#F5C842',
  errorRed: '#E03535',
  border: '#E8D5B7',
} as const;

type ColorToken = keyof typeof PALETTE;

const cssVarName = (key: string): string =>
  `--c-${key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`)}`;

// :root に CSS custom properties を注入（module 載入時一次）
if (typeof document !== 'undefined') {
  const style = document.createElement('style');
  style.setAttribute('data-theme-vars', '');
  style.textContent = `:root{${(Object.keys(PALETTE) as ColorToken[])
    .map((k) => `${cssVarName(k)}:${PALETTE[k]};`).join('')}}`;
  document.head.appendChild(style);
}

export const C = Object.fromEntries(
  (Object.keys(PALETTE) as ColorToken[]).map((k) => [k, `var(${cssVarName(k)})`]),
) as Record<ColorToken, string>;

/** 半透明色：var() token 不能再用 hex 後綴拼接，統一走 color-mix。
 *  alpha(C.gold, 0.13) ≒ 舊寫法 `${C.gold}22`。 */
export const alpha = (color: string, a: number): string =>
  `color-mix(in srgb, ${color} ${Math.round(a * 100)}%, transparent)`;

export const FONT = '"Noto Sans JP", sans-serif';

export const scoreColor = (s: number | null | undefined): string => {
  if (s == null) return C.sub;
  if (s >= 80) return C.successGreen;
  if (s >= 60) return C.amber;
  return C.sub;
};

export const recommendColor = (s: number | null | undefined): string => {
  if (s == null) return C.sub;
  if (s >= 75) return C.successGreen;
  if (s >= 60) return C.amber;
  return C.sub;
};

// 嚴重度色票（共同 Gap 用）
export const severityColor = (sev: string | null | undefined): string => {
  if (!sev) return C.sub;
  const low = sev.toLowerCase();
  if (sev.includes('高') || sev.includes('強') || low.includes('high')) return C.errorRed;
  if (sev.includes('中') || low.includes('medium') || low.includes('med')) return C.amber;
  return C.sub;
};

// gap 性質色票
export const natureColor = (nature: string | null | undefined): string => {
  if (!nature) return C.sub;
  const low = nature.toLowerCase();
  if (nature.includes('真') || low.includes('real') || low.includes('core')) return C.errorRed;
  if (nature.includes('敘事') || low.includes('narrative') || low.includes('story')) return C.progressIndigo;
  return C.amber; // 混合
};

export const getTierLabel = (tier: string | null | undefined): string => {
  switch (tier) {
    case 'ai_startup': return tStatic('tier_ai_startup');
    case 'mega_venture': return tStatic('tier_mega_venture');
    case 'traditional_sier': return tStatic('tier_traditional_sier');
    default: return tStatic('tier_unknown');
  }
};

export const statusColor: Record<string, string> = {
  applied: C.progressIndigo,
  recruiter: C.progressIndigo,
  tech: C.progressIndigo,
  onsite: C.progressIndigo,
  offer: C.doneGold,
  rejected: C.errorRed,
};

export const card: React.CSSProperties = {
  background: C.card,
  borderRadius: 8,
  boxShadow: '0 2px 8px rgba(90,82,72,.08)',
  padding: 16,
};

/** 日期字串 → 相對時間（日文）。輸入 "2025-06-10" 之類的 ISO date。 */
export const relativeDate = (dateStr: string | null | undefined): string => {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / 86_400_000);
  if (diffDays < 0) return dateStr;
  if (diffDays === 0) return tStatic('date_today');
  if (diffDays === 1) return tStatic('date_yesterday');
  if (diffDays < 7) return tStatic('date_days_ago').replace('{n}', String(diffDays));
  if (diffDays < 30) return tStatic('date_weeks_ago').replace('{n}', String(Math.floor(diffDays / 7)));
  if (diffDays < 365) return tStatic('date_months_ago').replace('{n}', String(Math.floor(diffDays / 30)));
  return tStatic('date_years_ago').replace('{n}', String(Math.floor(diffDays / 365)));
};

export const pill = (active: boolean): React.CSSProperties => ({
  borderRadius: 999,
  padding: '5px 14px',
  border: `1px solid ${active ? C.gold : C.border}`,
  background: active ? C.gold : C.card,
  color: C.ink,
  fontSize: 13,
  fontWeight: active ? 700 : 400,
});

// ───────────────────────── 共用設計 token（統一各頁手刻樣式） ─────────────────────────

/** 圓角刻度：sm 元件、md 卡片、lg 大面板、pill 膠囊 */
export const RADIUS = { sm: 8, md: 16, lg: 24, pill: 999 } as const;

/** 陰影刻度：sm 列表卡、md 浮起卡、lg 大面板 */
export const SHADOW = {
  sm: '0 4px 16px rgba(90,82,72,0.10)',
  md: '0 6px 22px rgba(90,82,72,0.12)',
  lg: '0 10px 40px rgba(90,82,72,0.13)',
} as const;

/** 暖白「軟卡」：各列表頁原本手刻的 bg + 24px 圓角 + lg 陰影，統一於此。
 *  overflowX auto：窄螢幕時寬表格在卡片內橫向捲動（響應式斷點）。 */
export const softCard: React.CSSProperties = {
  background: C.bg,
  borderRadius: RADIUS.lg,
  boxShadow: SHADOW.lg,
  overflow: 'hidden',
  overflowX: 'auto',
};

/** 響應式斷點：≤900px 視為窄螢幕（側邊欄收合為漢堡選單）。 */
export const NARROW_BP = 900;

/** 軟卡（小型，給 KPI / 區塊用，含內距） */
export const softPanel: React.CSSProperties = {
  background: C.bg,
  borderRadius: RADIUS.lg,
  boxShadow: SHADOW.lg,
  padding: '24px 26px',
};

/** 進場動畫：列表頁載入時的淡入+輕位移。180ms ease-out，無 overshoot 彈跳
 *（生產力工具的動效紀律：克制勝於花俏，尊重 prefers-reduced-motion 見 index.html）。 */
export const riseIn: React.CSSProperties = {
  animation: 'riseIn 180ms ease-out both',
};

/** 公司名 → 首字大寫縮寫（avatar 用）。容錯空值。 */
export const getInitials = (company: string | null | undefined): string =>
  (company || '?').split(/[\s\-・（(]/)[0].charAt(0).toUpperCase();

// ───────────────────────── 字級 / 間距刻度（統一各頁 magic number） ─────────────────────────

/** 字級刻度：label 標籤、caption 輔助、table 表格、body 正文、h3 區塊題、h1 頁題。 */
export const TYPE = {
  label: 11,
  caption: 12,
  table: 13,
  body: 14,
  h3: 17,
  h1: 24,
} as const;

/** 間距刻度：統一各頁 padding/gap 的 magic number。 */
export const SPACE = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;
