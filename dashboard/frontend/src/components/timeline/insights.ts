import type { TKey } from '../../locales/ja';
import type { RateItem } from '../RateRow';
import type { InterviewLoad, WaitCurve, WeekRow } from '../TimeStats';
import type { AgendaItem, FunnelStep, Quality, RejectionDistItem, Segment, ShoruiTurnaround, Summary } from './types';

/**
 * 各区の「結論」1 行を規則で作る（LLM 不使用・API の数字だけ）。
 * 標本が足りない／CI が基準線を跨ぐときは「差がある」と言わない——
 * このページで一番避けたい誤読は、n=5 の差を傾向として読むこと。
 */
export type Tone = 'good' | 'bad' | 'neutral';
export interface Insight { key: TKey; params: Record<string, string | number>; tone: Tone }

const MIN_DECIDED = 10;
const COVERAGE_WARN = 50;

const ins = (key: TKey, params: Insight['params'] = {}, tone: Tone = 'neutral'): Insight => ({ key, params, tone });

/** CI が基準線の片側に収まっているときだけ方向を返す。 */
export const ciVerdict = (item: Pick<RateItem, 'ci_low' | 'ci_high' | 'insufficient'>, base: number | null):
  'up' | 'down' | 'flat' => {
  if (base == null || item.insufficient || item.ci_low == null || item.ci_high == null) return 'flat';
  if (item.ci_low > base) return 'up';
  if (item.ci_high < base) return 'down';
  return 'flat';
};

export const upcomingInsights = (
  items: AgendaItem[], load: InterviewLoad | undefined, fmtDate: (iso: string) => string,
): Insight[] => {
  if (items.length === 0) return [ins('tl_ins_no_upcoming')];
  const next = items[0];
  const out = [ins('tl_ins_upcoming', { n: items.length, date: fmtDate(next.date), company: next.company })];
  const busy = load?.busy_threshold ?? 3;
  load?.weeks
    .filter((w) => w.when !== 'past' && w.n >= busy)
    .forEach((w) => out.push(ins('tl_ins_busy_week', { week: fmtDate(w.week), n: w.n }, 'bad')));
  return out;
};

/** 前段の母数が十分な段の中で、通過率が最も低い段＝最大の関門。 */
export const findBottleneck = (funnel: FunnelStep[]): { f: FunnelStep; prev: FunnelStep } | null => {
  const candidates = funnel
    .map((f, i) => ({ f, prev: funnel[i - 1] }))
    .filter(({ f, prev }) => prev && prev.n >= MIN_DECIDED && f.conv_from_prev != null);
  if (candidates.length === 0) return null;
  return candidates.reduce((a, b) => ((b.f.conv_from_prev ?? 100) < (a.f.conv_from_prev ?? 100) ? b : a));
};

export const overviewInsights = (
  funnel: FunnelStep[], rejection: RejectionDistItem[], stageLabel: (s: string) => string,
): Insight[] => {
  const out: Insight[] = [];
  const worst = findBottleneck(funnel);
  if (worst) {
    out.push(ins('tl_ins_bottleneck', {
      from: stageLabel(worst.prev.stage), stage: stageLabel(worst.f.stage),
      prev: worst.prev.n, n: worst.f.n, rate: worst.f.conv_from_prev ?? 0,
    }, 'bad'));
  }
  const totalRej = rejection.reduce((s, r) => s + r.n, 0);
  if (totalRej > 0) {
    const top = rejection.reduce((a, b) => (b.n > a.n ? b : a));
    out.push(ins('tl_ins_rejection_top', { pct: Math.round((top.n / totalRej) * 100), stage: top.label, n: top.n }));
  }
  return out;
};

/** 最新週の「近 4 週滾動通過率」を全体と比べる。 */
export const trendInsights = (weekly: WeekRow[] | undefined, base: number | null): Insight[] => {
  const last = weekly?.[weekly.length - 1];
  if (!last || last.rolling_rate == null || last.rolling_decided < MIN_DECIDED) {
    return [ins('tl_ins_trend_na', { n: last?.rolling_decided ?? 0 })];
  }
  const v = ciVerdict({ ci_low: last.rolling_ci_low, ci_high: last.rolling_ci_high, insufficient: false }, base);
  const verdictKey: Record<typeof v, TKey> = { up: 'tl_verdict_up', down: 'tl_verdict_down', flat: 'tl_verdict_flat' };
  return [ins('tl_ins_trend', {
    rate: last.rolling_rate, lo: last.rolling_ci_low ?? '—', hi: last.rolling_ci_high ?? '—',
    base: base ?? '—', n: last.rolling_decided, verdict: verdictKey[v],
  }, v === 'up' ? 'good' : v === 'down' ? 'bad' : 'neutral')];
};

export const waitInsights = (turn: ShoruiTurnaround, curve: WaitCurve | undefined): Insight[] => {
  const out: Insight[] = [];
  if (turn.n > 0) {
    out.push(ins('tl_ins_turnaround', {
      days: turn.median_days ?? '—',
      pass: curve?.median_pass_days ?? '—', fail: curve?.median_fail_days ?? '—',
    }));
  }
  // 標本が足りる中で最も長く待った点＝「ここまで待ったら、残りの見込みはこれくらい」
  const tail = curve?.points.filter((p) => p.day > 0 && !p.insufficient && p.rate != null).pop();
  const all = curve?.points.find((p) => p.day === 0);
  if (tail) {
    const v = ciVerdict(tail, all?.rate ?? null);
    out.push(ins('tl_ins_wait_tail', {
      day: tail.day, rate: tail.rate ?? 0, passed: tail.passed, decided: tail.decided,
    }, v === 'down' ? 'bad' : 'neutral'));
  }
  return out;
};

/** CI が基準線を外れた分群だけを挙げる（最大 3 件）。無ければ「差は判断できない」と明言。 */
export const factorInsights = (
  segments: Segment[], base: number | null,
  dimLabel: (dim: string) => string, keyLabel: (dim: string, key: string) => string,
): Insight[] => {
  const hits = segments.flatMap((s) => s.items
    .map((it) => ({ s, it, v: ciVerdict(it, base) }))
    .filter((h) => h.v !== 'flat'));
  if (hits.length === 0) return [ins('tl_ins_seg_none')];
  hits.sort((a, b) => Math.abs((b.it.lift ?? 1) - 1) - Math.abs((a.it.lift ?? 1) - 1));
  return hits.slice(0, 3).map(({ s, it, v }) => ins(v === 'up' ? 'tl_ins_seg_up' : 'tl_ins_seg_down', {
    dim: dimLabel(s.dimension), label: keyLabel(s.dimension, it.key),
    rate: it.rate ?? 0, lo: it.ci_low ?? '—', hi: it.ci_high ?? '—', base: base ?? '—',
    passed: it.passed, decided: it.decided,
  }, v === 'up' ? 'good' : 'bad'));
};

export const coverageWarnings = (q: Quality): number =>
  [q.tier_coverage, q.recommend_coverage, q.employee_coverage].filter((v) => v < COVERAGE_WARN).length
  + (q.unknown_stage_pct > 10 ? 1 : 0);

export const qualityInsights = (s: Summary, q: Quality): Insight[] => {
  const warn = coverageWarnings(q);
  return [
    ins('tl_ins_bounds', { low: s.bound_low ?? '—', high: s.bound_high ?? '—', n: s.unknown_stage }),
    warn > 0 ? ins('tl_ins_quality_warn', { n: warn }, 'bad') : ins('tl_ins_quality_ok', {}, 'good'),
  ];
};
