// 視覺小說模擬面試 — 腳本資料格式
// 替換台詞只需改 scripts.ts，格式照此定義即可。

export type VNSpeaker = 'interviewer' | 'candidate' | 'inner';
export type VNEmotion = 'neutral' | 'smile' | 'think' | 'serious' | 'nervous' | 'relieved';
export type VNLang = 'ja' | 'zh';

export interface VNNote {
  ja: string;
  zh: string;
}

export interface VNLine {
  id: string;
  speaker: VNSpeaker;
  /** inner = 候選人內心獨白（斜體、半透明、無對話框） */
  type: 'talk' | 'inner';
  ja: string;
  zh: string;
  emotion: VNEmotion;
  /** 好感度增減（顯示完該句後套用） */
  favorDelta?: number;
  /** 極輕畫面震動（危機幕追問句用） */
  fx?: 'shake';
  /** 解説モード小卡：なぜこの回答が良いのか */
  note?: VNNote;
  /** 逐句音檔檔名（{audioBase}/{audio}；後端 manifest 合併注入） */
  audio?: string;
  /** 該句音檔實測時長 ms（ffprobe 量測，後端 manifest 合併注入；無音檔時前端以字數估算） */
  durationMs?: number;
  /** true = 面試官回答無法由素材佐證，練習用模擬內容（interview/theater_script.py 生成） */
  simulated?: boolean;
  /** 追問句：引用自面試官前一句的原文子字串（生成時的反問鏈錨定，前端可用於高亮呼應） */
  quoteFrom?: string;
}

export interface VNAct {
  id: number;
  titleJa: string;
  titleZh: string;
  /** 本幕的學習目標（頂欄顯示，讓觀眾知道現在在練什麼） */
  goalJa?: string;
  goalZh?: string;
  /** 考察重點標籤（中央學習面板） */
  tags?: { ja: string; zh: string }[];
  /** 回答框架清單（中央學習面板常駐，如 STAR） */
  framework?: {
    titleJa: string;
    titleZh: string;
    items: { ja: string; zh: string }[];
  };
  lines: VNLine[];
}

export interface VNAspect {
  labelJa: string;
  labelZh: string;
  /** 五段評價 1–5 */
  score: 1 | 2 | 3 | 4 | 5;
  commentJa: string;
  commentZh: string;
}

export interface VNReport {
  aspects: VNAspect[];
  overallJa: string;
  overallZh: string;
}

export interface VNScript {
  id: string;
  titleJa: string;
  titleZh: string;
  /** 面試官署名（結算卡、名牌用） */
  interviewerJa: string;
  interviewerZh: string;
  /** true = 佔位示範腳本（選單顯示サンプル標籤） */
  sample?: boolean;
  /** true = 隱藏好感度 UI（面接パック自動生成台本 — 不編造演出評分） */
  noFavor?: boolean;
  /** 逐句音檔 URL prefix（後端注入；無 = 純文字台本） */
  audioBase?: string;
  acts: VNAct[];
  report: VNReport;
  /** 面接パック自動生成台本のメタ（company/jobTitle 等）。復習頁の見出し階層化に使う */
  _meta?: { company?: string; jobTitle?: string };
}

export const FAVOR_START = 50;

export const clampFavor = (v: number): number => Math.max(0, Math.min(100, v));

/** 攤平所有幕的台詞，附上所屬幕 index */
export interface FlatLine extends VNLine {
  actIdx: number;
}

export const flattenScript = (script: VNScript): FlatLine[] =>
  script.acts.flatMap((act, actIdx) => act.lines.map((l) => ({ ...l, actIdx })));

/** 好感度為導出值：從頭累加到 idx（含），避免回退/跳章時漂移 */
export const favorAt = (lines: FlatLine[], idx: number): number =>
  clampFavor(
    lines.slice(0, idx + 1).reduce((acc, l) => acc + (l.favorDelta ?? 0), FAVOR_START),
  );
