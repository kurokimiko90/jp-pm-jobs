import type { TKey } from '../locales/ja';
import { C } from '../theme';

/**
 * 篩選欄位字典 — 所有列表頁共用的欄位定義（key / i18n key / 顏色 / 固定順序）。
 * 各頁禁止自建 options 陣列；同一概念的選項集合與順序以此為唯一來源。
 */

export interface DictOption {
  key: string;
  tKey: TKey;
}

export interface DictTab {
  key: string;
  tKey: TKey;
  color?: string;
}

const AMBER = '#C79A1B';
const MUTED = '#A0968A';

// ── 品質分數層（評分 / 推薦度共用檔位） ──
export const SCORE_THRESHOLDS = [0, 60, 70, 80];

// ── 範圍限定層 ──
export const REGION_OPTIONS: DictOption[] = [
  { key: '', tKey: 'jobs_filter_all_regions' },
  { key: 'japan', tKey: 'jobs_filter_japan' },
  { key: 'overseas', tKey: 'jobs_filter_overseas' },
];

export const DAYS_OPTIONS: DictOption[] = [
  { key: '0', tKey: 'recommend_range_all' },
  { key: '1', tKey: 'recommend_range_24h' },
  { key: '3', tKey: 'recommend_range_3d' },
  { key: '7', tKey: 'recommend_range_7d' },
];

export const LANG_OPTIONS: DictOption[] = [
  { key: 'all', tKey: 'tailored_filter_all' },
  { key: 'jp', tKey: 'tailored_filter_jp' },
  { key: 'en', tKey: 'tailored_filter_en' },
];

export const TIER_KEYS = ['ai_startup', 'mega_venture', 'traditional_sier'];

// ── 職位類型（jobs.job_type — 入庫時純規則分類，見 analyzer/jd_enrich.py） ──
export const ROLE_OPTIONS: DictOption[] = [
  { key: '', tKey: 'jobs_filter_all_roles' },
  { key: 'pdm', tKey: 'role_type_pdm' },
  { key: 'pjm', tKey: 'role_type_pjm' },
  { key: 'consulting', tKey: 'role_type_consulting' },
  { key: 'other', tKey: 'role_type_other' },
];

// ── 應募狀態（統一 5 選項；後端 _APPLIED_SQL 同名支援） ──
export const APPLIED_OPTIONS: DictOption[] = [
  { key: '', tKey: 'jobs_all' },
  { key: 'not_applied', tKey: 'recommend_not_applied_only' },
  { key: 'applied', tKey: 'jobs_filter_applied' },
  { key: 'company', tKey: 'jobs_filter_company_applied' },
  { key: 'not_company', tKey: 'recommend_exclude_company' },
];

// ── 狀態層 tabs（各頁的「看哪個子集合」） ──
export const VERDICT_TABS: DictTab[] = [
  { key: 'go', tKey: 'verdict_go', color: C.successGreen },
  { key: 'improve', tKey: 'verdict_improve', color: AMBER },
  { key: 'skip', tKey: 'verdict_skip', color: C.sub },
];

export const STAGE_TABS: DictTab[] = [
  { key: 'applied', tKey: 'status_applied' },
  { key: 'casual', tKey: 'status_casual' },
  { key: 'recruiter', tKey: 'status_recruiter' },
  { key: 'tech', tKey: 'status_tech' },
  { key: 'onsite', tKey: 'status_onsite' },
  { key: 'offer', tKey: 'status_offer', color: C.doneGold },
  { key: 'rejected', tKey: 'status_rejected', color: C.sub },
];

export const PACK_STATUS_TABS: DictTab[] = [
  { key: 'custom', tKey: 'tailored_custom' },
  { key: 'brief', tKey: 'tailored_company_brief' },
  { key: 'shibou', tKey: 'tailored_shibou' },
  { key: 'done', tKey: 'tailored_stat_done' },
];

export const DA_STATUS_TABS: DictTab[] = [
  { key: 'pending', tKey: 'da_status_pending', color: MUTED },
  { key: 'detected', tKey: 'da_status_detected', color: AMBER },
  { key: 'not_found', tKey: 'da_status_not_found', color: MUTED },
  { key: 'pack_ready', tKey: 'da_status_pack_ready', color: AMBER },
  { key: 'drafted', tKey: 'da_status_drafted', color: '#6C7FD8' },
  { key: 'sent', tKey: 'da_status_sent', color: '#3D9A6C' },
  { key: 'skipped', tKey: 'da_status_skipped', color: MUTED },
  { key: 'failed', tKey: 'da_status_failed', color: '#C0564A' },
];

export const DA_STATUS_COLOR: Record<string, string> = Object.fromEntries(
  DA_STATUS_TABS.map((tb) => [tb.key, tb.color ?? C.sub]),
);
