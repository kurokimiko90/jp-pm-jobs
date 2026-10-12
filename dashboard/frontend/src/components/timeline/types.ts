import type { RateItem } from '../RateRow';
import type { SalaryAsk } from '../SalaryAskCard';
import type { ApplyRequestStats } from '../ApplyRequestCard';
import type { DailyCount } from '../ApplyCalendar';
import type { TimeStatsData } from '../TimeStats';

// /api/applications/timeline の型（dashboard/backend/app_analytics.py と対応）

export interface AgendaItem {
  job_id: number; company: string; status: string; date: string; time: string | null; stage: string;
}
export interface StageDuration { median_days: number; n: number }
/** ドットプロット用の 1 目盛り＝1 整数値の件数。overflow=true は「軸の最終目盛りに
 * 溜め込んだ打ち切りビン」（例：日数分布の「20+」）を表す。 */
export interface DotPoint { value: number; n: number; overflow?: boolean }
export interface ShoruiTurnaround {
  points: DotPoint[]; n: number; mean_days: number | null; median_days: number | null;
  pending: { n: number; median_days: number | null };
}
export interface ScoreDistribution {
  points: DotPoint[]; n: number; unknown_n: number;
  mean: number | null; median: number | null;
}
export interface RecommendDistribution { applied: ScoreDistribution; passed: ScoreDistribution }
export interface RejectionDistItem { stage: string; label: string; n: number }
export interface SelfWithdrawnItem {
  job_id: number; company: string; status: string;
  rejection_stage: string | null; rejection_stage_label: string | null;
  reached_label: string; before_result: boolean; note: string | null;
}
export interface Summary {
  total: number; passed: number; failed: number; unknown_stage: number; pending: number;
  withdrawn_early: number; withdrawn_after_pass: number;
  decided: number; pass_rate: number | null; ci_low: number | null; ci_high: number | null;
  bound_low: number | null; bound_high: number | null;
  reached_interview: number; offers: number; in_progress: number;
}
export interface FunnelStep {
  stage: string; n: number;
  conv_from_prev: number | null; conv_from_start: number | null;
  dropped: number | null; withdrawn: number;
}
export interface Segment {
  dimension: string; multi_label: boolean; informative: boolean;
  max_effect: number; items: RateItem[];
}
export interface Cohort extends RateItem { month: string }
export interface Quality {
  tier_coverage: number; recommend_coverage: number; employee_coverage: number;
  unknown_stage_pct: number; min_n: number;
}
export interface TimelineData {
  daily_counts: DailyCount[];
  agenda: { upcoming: AgendaItem[] };
  stage_durations: Record<string, StageDuration>;
  shorui_turnaround: ShoruiTurnaround;
  recommend_distribution: RecommendDistribution;
  rejection_dist: RejectionDistItem[];
  summary: Summary;
  funnel: FunnelStep[];
  segments: Segment[];
  cohorts: Cohort[];
  quality: Quality;
  self_withdrawn: SelfWithdrawnItem[];
  salary_ask?: SalaryAsk;
  apply_request?: ApplyRequestStats;
  time?: TimeStatsData;
}
