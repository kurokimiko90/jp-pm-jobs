import { type VNAct, type VNLine } from '../types';

// 復習頁專用的純邏輯（時間估算 / localStorage 書籤・KPI / 關鍵詞高亮切字）。
// 全部 client-side、單人工具零後端狀態 — localStorage 足夠，不建後端表。

const CHARS_PER_SEC = 6.5; // 日語 TTS 概算語速（無音檔時的 fallback，非實測）
const PAUSE_MS_PER_LINE = 350; // 句間停頓概算

export const lineSeconds = (line: VNLine): number =>
  line.durationMs != null
    ? line.durationMs / 1000
    : line.ja.length / CHARS_PER_SEC;

/** 幕所需秒數：有實測音檔用實測，沒有的句子退回字數估算（混用不影響加總）。 */
export const actSeconds = (act: VNAct): number =>
  act.lines.reduce((acc, l) => acc + lineSeconds(l) + PAUSE_MS_PER_LINE / 1000, 0);

export const formatMinutes = (seconds: number): string => {
  const min = Math.max(1, Math.round(seconds / 60));
  return `${min} min`;
};

// ---------------------------------------------------------------- 書籤（localStorage）

const BOOKMARK_KEY = 'theaterReviewBookmarks';

export const loadBookmarks = (): Set<string> => {
  try {
    const raw = localStorage.getItem(BOOKMARK_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
};

export const saveBookmarks = (ids: Set<string>): void => {
  try {
    localStorage.setItem(BOOKMARK_KEY, JSON.stringify([...ids]));
  } catch {
    // localStorage 不可用（隱私模式等）— 靜默放棄持久化，當次 session 仍可用 state
  }
};

// ---------------------------------------------------------------- KPI（localStorage）

const KPI_KEY = 'theaterReviewKpi';

interface KpiData {
  viewCount: number;
  byDate: Record<string, number>; // date(YYYY-MM-DD) → 累積閱讀秒數
}

const emptyKpi = (): KpiData => ({ viewCount: 0, byDate: {} });

export const loadKpi = (): KpiData => {
  try {
    const raw = localStorage.getItem(KPI_KEY);
    return raw ? (JSON.parse(raw) as KpiData) : emptyKpi();
  } catch {
    return emptyKpi();
  }
};

const saveKpi = (kpi: KpiData): void => {
  try {
    localStorage.setItem(KPI_KEY, JSON.stringify(kpi));
  } catch {
    // 同上，放棄持久化
  }
};

/** 進頁記一次瀏覽次數，回傳目前 KPI 快照。 */
export const recordView = (today: string): KpiData => {
  const kpi = loadKpi();
  kpi.viewCount += 1;
  kpi.byDate[today] = kpi.byDate[today] ?? 0;
  saveKpi(kpi);
  return kpi;
};

/** 累加今日閱讀秒數（計時器 tick 呼叫），回傳更新後秒數。 */
export const addReadSeconds = (today: string, delta: number): number => {
  const kpi = loadKpi();
  kpi.byDate[today] = (kpi.byDate[today] ?? 0) + delta;
  saveKpi(kpi);
  return kpi.byDate[today];
};

// ---------------------------------------------------------------- 關鍵詞高亮

const TERM_LIST = [
  'API', 'SaaS', 'PdM', 'PjM', 'KPI', 'KPT', 'LLM', 'MVP', 'ROI', 'B2B', 'B2C',
  'SCM', 'PoC', 'AI', '生成AI', 'CEO', 'CPO', 'CTO', 'STAR',
];
const TERM_RE = new RegExp(`(${TERM_LIST.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g');
const DIGIT_TOKEN_RE = /(\d+(?:\.\d+)?%?[万億]?円?年?)/g;

export interface HighlightToken {
  text: string;
  kind: 'plain' | 'digit' | 'term';
}

/** 表示用の切字のみ（Gate B の事実錨定とは無関係 — 純粋な視覚スキャン補助）。 */
export const highlightTokens = (text: string): HighlightToken[] => {
  const byDigit = text.split(DIGIT_TOKEN_RE);
  const tokens: HighlightToken[] = [];
  byDigit.forEach((chunk, i) => {
    if (!chunk) return;
    if (i % 2 === 1) {
      tokens.push({ text: chunk, kind: 'digit' });
      return;
    }
    const byTerm = chunk.split(TERM_RE);
    byTerm.forEach((part, j) => {
      if (!part) return;
      tokens.push({ text: part, kind: j % 2 === 1 ? 'term' : 'plain' });
    });
  });
  return tokens;
};

export const todayStr = (): string => new Date().toISOString().slice(0, 10);

// ---------------------------------------------------------------- 上次選擇的劇本（localStorage）

const LAST_SCRIPT_KEY = 'theaterReviewLastScript';

export const loadLastScriptKey = (): string | null => {
  try {
    return localStorage.getItem(LAST_SCRIPT_KEY);
  } catch {
    return null;
  }
};

export const saveLastScriptKey = (key: string): void => {
  try {
    localStorage.setItem(LAST_SCRIPT_KEY, key);
  } catch {
    // 同上，放棄持久化
  }
};
