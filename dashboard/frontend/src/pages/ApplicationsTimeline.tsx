import React, { useEffect, useState } from 'react';
import { get } from '../api';
import { C, alpha } from '../theme';
import { Card, SectionTitle, Empty, ReportBar, ReportStat, Badge, useAppStatusLabels, STAGE_ORDER } from '../components/ui';
import { useT } from '../i18n';

interface DailyCount { date: string; count: number }
interface AgendaItem {
  job_id: number; company: string; status: string; date: string; time: string | null; stage: string;
}
interface StageDuration { median_days: number; n: number }
interface RejectionDistItem { stage: string; label: string; n: number }
interface SelfWithdrawnItem {
  job_id: number; company: string; status: string;
  rejection_stage: string | null; rejection_stage_label: string | null;
  reached_label: string; before_result: boolean; note: string | null;
}
interface Summary {
  total: number; passed: number; failed: number; unknown_stage: number; pending: number;
  withdrawn_early: number; withdrawn_after_pass: number;
  decided: number; pass_rate: number | null; ci_low: number | null; ci_high: number | null;
  bound_low: number | null; bound_high: number | null;
  reached_interview: number; offers: number; in_progress: number;
}
interface FunnelStep {
  stage: string; n: number;
  conv_from_prev: number | null; conv_from_start: number | null;
  dropped: number | null; withdrawn: number;
}
/** 條件通過率的一行：點估計 + Wilson 95% CI + 相對基準的 lift。 */
interface RateItem {
  key: string; passed: number; decided: number; pending: number; unknown: number;
  rate: number | null; ci_low: number | null; ci_high: number | null;
  lift: number | null; insufficient: boolean;
}
interface Segment {
  dimension: string; multi_label: boolean; informative: boolean;
  max_effect: number; items: RateItem[];
}
interface Cohort extends RateItem { month: string }
interface Quality {
  tier_coverage: number; recommend_coverage: number; employee_coverage: number;
  unknown_stage_pct: number; min_n: number;
}
interface TimelineData {
  daily_counts: DailyCount[];
  agenda: { upcoming: AgendaItem[]; past: AgendaItem[] };
  stage_durations: Record<string, StageDuration>;
  rejection_dist: RejectionDistItem[];
  summary: Summary;
  funnel: FunnelStep[];
  segments: Segment[];
  cohorts: Cohort[];
  quality: Quality;
  self_withdrawn: SelfWithdrawnItem[];
}

const WEEKDAY_JA = ['日', '月', '火', '水', '木', '金', '土'];

const toISODate = (d: Date): string => d.toISOString().slice(0, 10);

const fmtDate = (iso: string): string => {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAY_JA[d.getDay()]})`;
};

/** 投遞日曆熱圖用：把 daily_counts 攤成「週日起始」的整週格線，缺資料的天數補 0。 */
function buildCalendarWeeks(dailyCounts: DailyCount[]): { weeks: string[][]; countByDate: Record<string, number> } {
  const countByDate: Record<string, number> = {};
  let minDate: string | null = null;
  for (const { date, count } of dailyCounts) {
    countByDate[date] = count;
    if (!minDate || date < minDate) minDate = date;
  }
  const todayIso = toISODate(new Date());
  const start = new Date(`${minDate ?? todayIso}T00:00:00`);
  start.setDate(start.getDate() - start.getDay());
  const end = new Date(`${todayIso}T00:00:00`);

  const weeks: string[][] = [];
  const cur = new Date(start);
  while (cur <= end) {
    const week: string[] = [];
    for (let i = 0; i < 7; i++) {
      week.push(toISODate(cur));
      cur.setDate(cur.getDate() + 1);
    }
    weeks.push(week);
  }
  return { weeks, countByDate };
}

const heatColor = (n: number): string => {
  if (n <= 0) return C.border;
  if (n === 1) return alpha(C.gold, 0.35);
  if (n === 2) return alpha(C.gold, 0.65);
  if (n <= 4) return C.gold;
  return C.amber;
};

const CalendarHeatmap: React.FC<{ dailyCounts: DailyCount[] }> = ({ dailyCounts }) => {
  const { weeks, countByDate } = buildCalendarWeeks(dailyCounts);
  return (
    <div style={{ overflowX: 'auto', paddingBottom: 4 }}>
      <div style={{ display: 'flex', gap: 3 }}>
        {weeks.map((week) => (
          <div key={week[0]} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {week.map((iso) => (
              <div
                key={iso}
                title={`${iso} · ${countByDate[iso] ?? 0}`}
                style={{
                  width: 11, height: 11, borderRadius: 2,
                  background: heatColor(countByDate[iso] ?? 0),
                }}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
};

const AgendaList: React.FC<{ items: AgendaItem[]; emptyText: string }> = ({ items, emptyText }) => {
  const statusLabels = useAppStatusLabels();
  if (items.length === 0) return <Empty>{emptyText}</Empty>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((it) => (
        <div key={it.job_id} style={{
          display: 'flex', alignItems: 'center', gap: 10, fontSize: 13,
          padding: '6px 0', borderBottom: `1px solid ${alpha(C.border, 0.6)}`,
        }}>
          <span style={{ fontVariantNumeric: 'tabular-nums', color: C.sub, minWidth: 78 }}>
            {fmtDate(it.date)}{it.time ? ` ${it.time}` : ''}
          </span>
          <span style={{ flex: 1, color: C.ink, fontWeight: 600 }}>{it.company}</span>
          <Badge>{it.stage}</Badge>
          <span style={{ fontSize: 11, color: C.sub }}>{statusLabels[it.status] ?? it.status}</span>
        </div>
      ))}
    </div>
  );
};

// ───────────────────────── 通過率チャート（点推定 + 95% CI） ─────────────────────────

/**
 * 1 行 = 1 分群。バー長ではなく「点 + 誤差棒」で描くのは、
 * n=1 の 100% と n=146 の 16% を同じ太さのバーで並べると誤読するため。
 * 破線 = 全体基準率。これが無いと「高い/低い」の判断ができない。
 */
const RateRow: React.FC<{
  label: string; item: RateItem; baseline: number | null; scaleMax: number; insufficientText: string;
}> = ({ label, item, baseline, scaleMax, insufficientText }) => {
  const t = useT();
  const dim = item.insufficient;
  const pos = (v: number): string => `${Math.min(100, (v / scaleMax) * 100)}%`;
  const lo = item.ci_low ?? 0;
  const hi = item.ci_high ?? 0;
  const rate = item.rate ?? 0;
  const color = dim ? C.sub : (item.lift != null && item.lift >= 1.3) ? C.successGreen
    : (item.lift != null && item.lift <= 0.7) ? C.errorRed : C.ink;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5 }}>
      <span style={{
        minWidth: 92, color: dim ? C.sub : C.ink, fontWeight: dim ? 400 : 600,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }} title={label}>{label}</span>

      <div style={{ position: 'relative', flex: 1, height: 18, minWidth: 90 }}>
        {/* 目盛りの底線 */}
        <div style={{
          position: 'absolute', top: 8.5, left: 0, right: 0, height: 1, background: alpha(C.border, 0.8),
        }} />
        {/* 全体基準率 */}
        {baseline != null && (
          <div title={`${t.app_stats_baseline} ${baseline}%`} style={{
            position: 'absolute', top: 1, bottom: 1, left: pos(baseline), width: 0,
            borderLeft: `1px dashed ${alpha(C.ink, 0.55)}`,
          }} />
        )}
        {/* 95% 信頼区間 */}
        <div style={{
          position: 'absolute', top: 6, height: 6, borderRadius: 3,
          left: pos(lo), width: `calc(${pos(hi)} - ${pos(lo)})`,
          background: alpha(color, dim ? 0.18 : 0.28),
        }} />
        {/* 点推定 */}
        <div title={`${rate}% (95%CI ${lo}–${hi})`} style={{
          position: 'absolute', top: 4.5, left: pos(rate), width: 10, height: 10,
          marginLeft: -5, borderRadius: '50%', background: color,
          border: `2px solid ${C.card}`, boxSizing: 'content-box',
        }} />
      </div>

      <span style={{
        minWidth: 46, textAlign: 'right', fontVariantNumeric: 'tabular-nums',
        fontWeight: 700, color,
      }}>{item.rate}%</span>
      <span style={{ minWidth: 62, fontSize: 11, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>
        {item.passed}/{item.decided}
      </span>
      {dim
        ? <span style={{ fontSize: 10, color: C.sub, whiteSpace: 'nowrap' }} title={insufficientText}>⚠</span>
        : <span style={{ fontSize: 10, color: C.sub, minWidth: 8 }} />}
    </div>
  );
};

const SegmentCard: React.FC<{
  segment: Segment; baseline: number | null; labelOf: (dim: string, key: string) => string;
}> = ({ segment, baseline, labelOf }) => {
  const t = useT();
  const DIM_TITLE: Record<string, string> = {
    channel: t.app_stats_dim_channel, job_type: t.app_stats_dim_job_type,
    recommend_band: t.app_stats_dim_recommend, score_band: t.app_stats_dim_score,
    mentions_ai: t.app_stats_dim_ai, domain: t.app_stats_dim_domain,
    employee_band: t.app_stats_dim_employee,
  };
  // 目盛り上限は CI 上端に合わせる（常に 0-100 だと差が潰れて読めない）。
  // ただし n=1 の CI は 100% まで伸びるので、標本十分な行だけで軸を決め、
  // 参考値の行はクリップして表示する（1 件の外れ値に主力の差を潰させない）。
  const scaleSource = segment.items.filter((i) => !i.insufficient);
  const scaleMax = Math.min(100, Math.max(
    20, ...(scaleSource.length ? scaleSource : segment.items).map((i) => i.ci_high ?? 0),
  ) * 1.05);
  return (
    <Card>
      <SectionTitle right={segment.multi_label
        ? <span style={{ fontSize: 10, color: C.sub }}>{t.app_stats_multi_label}</span>
        : undefined}>
        {DIM_TITLE[segment.dimension] ?? segment.dimension}
      </SectionTitle>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        {segment.items.map((it) => (
          <RateRow
            key={it.key} label={labelOf(segment.dimension, it.key)} item={it}
            baseline={baseline} scaleMax={scaleMax} insufficientText={t.app_stats_insufficient}
          />
        ))}
      </div>
    </Card>
  );
};

export const ApplicationsTimeline: React.FC = () => {
  const t = useT();
  const statusLabels = useAppStatusLabels();
  const [data, setData] = useState<TimelineData | null>(null);

  useEffect(() => {
    get('/api/applications/timeline').then(setData).catch(() => setData(null));
  }, []);

  if (!data) return <Empty>{t.app_timeline_no_data}</Empty>;

  const { summary, quality } = data;
  const baseline = summary.pass_rate;
  const durationKeys = [...STAGE_ORDER, 'rejected'].filter((k) => data.stage_durations[k]);
  const maxDuration = Math.max(1, ...durationKeys.map((k) => data.stage_durations[k].median_days));
  const maxRejection = Math.max(1, ...data.rejection_dist.map((r) => r.n));
  const cohortScale = data.cohorts.filter((c) => !c.insufficient);
  const cohortScaleMax = Math.min(100, Math.max(
    20, ...(cohortScale.length ? cohortScale : data.cohorts).map((c) => c.ci_high ?? 0),
  ) * 1.05);

  const funnelLabel = (stage: string): string =>
    stage === 'shorui_pass' ? t.app_stats_funnel_shorui_pass : (statusLabels[stage] ?? stage);

  /** 分群 key → 表示名。維度ごとに辞書が違うので 1 箇所に集約する。 */
  const labelOf = (dim: string, key: string): string => {
    if (dim === 'job_type') {
      switch (key) {
        case 'pdm': return t.role_type_pdm;
        case 'pjm': return t.role_type_pjm;
        case 'consulting': return t.role_type_consulting;
        case 'other': return t.role_type_other;
        default: return t.app_stats_unclassified;
      }
    }
    if (dim === 'mentions_ai') {
      return key === 'yes' ? t.jobs_ai_mentioned : key === 'no' ? t.app_stats_ai_no : t.app_stats_unclassified;
    }
    if (dim === 'domain') {
      switch (key) {
        case 'fintech': return t.app_stats_domain_fintech;
        case 'saas': return t.app_stats_domain_saas;
        case 'ai': return t.app_stats_domain_ai;
        default: return t.app_stats_domain_none;
      }
    }
    if (dim === 'employee_band') {
      return key === 'unknown' ? t.app_stats_unclassified : `${key}${t.app_stats_employee_suffix}`;
    }
    if (dim === 'channel') return key === 'direct' ? t.channel_direct : key;
    if (key === 'unknown') return t.app_stats_unclassified;  // score/recommend 帯の未分析
    return key;
  };

  const maxFunnel = data.funnel[0]?.n || 1;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* ── 頂端 KPI：通過率は必ず CI と n を併記（点推定だけだと過信する） ── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        <ReportStat value={summary.total} label={t.app_stats_kpi_total}
          sub={`${t.app_stats_kpi_pending}${summary.pending}`} />
        <ReportStat
          value={summary.pass_rate != null ? `${summary.pass_rate}%` : '—'}
          label={t.app_stats_kpi_pass_rate}
          sub={`95%CI ${summary.ci_low}–${summary.ci_high} · n=${summary.decided}`}
          color={C.amber}
        />
        <ReportStat value={summary.reached_interview} label={t.app_stats_kpi_interview}
          sub={`${summary.passed}/${summary.decided}`} />
        <ReportStat value={summary.in_progress} label={t.app_stats_kpi_in_progress} color={C.progressIndigo} />
        <ReportStat
          value={summary.offers} label={t.app_stats_kpi_offer} color={C.successGreen}
          sub={summary.withdrawn_after_pass > 0
            ? t.app_stats_kpi_withdrawn_sub.replace('{n}', String(summary.withdrawn_after_pass))
            : undefined}
        />
      </div>

      {/* ── 選考漏斗 ── */}
      <Card>
        <SectionTitle right={<span style={{ fontSize: 11, color: C.sub }}>{t.app_stats_funnel_note}</span>}>
          {t.app_stats_funnel}
        </SectionTitle>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {data.funnel.map((f) => (
            <div key={f.stage} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
              <span style={{ minWidth: 84, color: C.ink }}>{funnelLabel(f.stage)}</span>
              <div style={{ flex: 1, minWidth: 80, height: 16, background: alpha(C.border, 0.5), borderRadius: 4, overflow: 'hidden' }}>
                <div style={{
                  height: '100%', width: `${(f.n / maxFunnel) * 100}%`,
                  background: f.stage === 'offer' ? C.successGreen : C.gold,
                  borderRadius: 4, transition: 'width .3s',
                }} />
              </div>
              <span style={{ minWidth: 38, textAlign: 'right', fontWeight: 700, color: C.ink, fontVariantNumeric: 'tabular-nums' }}>
                {f.n}
              </span>
              <span style={{ minWidth: 92, fontSize: 11, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>
                {f.conv_from_prev != null ? `→ ${f.conv_from_prev}%` : ''}
                {f.dropped ? ` (−${f.dropped})` : ''}
              </span>
              {/* この段階で本人が降りた件数。企業に切られた数と混ぜない */}
              <span style={{ minWidth: 74, fontSize: 11, color: C.lavender, fontWeight: 600 }}>
                {f.withdrawn > 0
                  ? t.app_stats_funnel_withdrawn.replace('{n}', String(f.withdrawn))
                  : ''}
              </span>
            </div>
          ))}
        </div>
      </Card>

      {/* ── セグメント別 条件付き通過率（このページの本体） ── */}
      <div>
        <SectionTitle right={
          <span style={{ fontSize: 11, color: C.sub }}>
            {t.app_stats_baseline} {baseline}% · {t.app_stats_ci_note}
          </span>
        }>{t.app_stats_segments}</SectionTitle>
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 380px), 1fr))', gap: 16,
        }}>
          {data.segments.map((seg) => (
            <SegmentCard key={seg.dimension} segment={seg} baseline={baseline} labelOf={labelOf} />
          ))}
        </div>
      </div>

      {/* ── 月別トレンド + データ品質 ── */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 20,
      }}>
        <Card>
          <SectionTitle>{t.app_stats_cohorts}</SectionTitle>
          {data.cohorts.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
              {data.cohorts.map((c) => (
                <RateRow key={c.month} label={c.month} item={c} baseline={baseline}
                  scaleMax={cohortScaleMax} insufficientText={t.app_stats_insufficient} />
              ))}
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle>{t.app_stats_quality}</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12.5 }}>
            {[
              { label: t.app_stats_quality_unknown, v: quality.unknown_stage_pct, warn: quality.unknown_stage_pct > 10, invert: true },
              { label: t.app_stats_quality_tier, v: quality.tier_coverage, warn: quality.tier_coverage < 50 },
              { label: t.app_stats_quality_recommend, v: quality.recommend_coverage, warn: quality.recommend_coverage < 50 },
              { label: t.app_stats_quality_employee, v: quality.employee_coverage, warn: quality.employee_coverage < 50 },
            ].map((q) => (
              <div key={q.label} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ flex: 1, color: C.ink }}>{q.label}</span>
                <div style={{ width: 90, height: 6, borderRadius: 3, background: alpha(C.border, 0.8), overflow: 'hidden' }}>
                  <div style={{
                    height: '100%', width: `${Math.min(100, q.v)}%`, borderRadius: 3,
                    background: q.warn ? C.errorRed : C.successGreen,
                  }} />
                </div>
                <span style={{
                  minWidth: 44, textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                  fontWeight: 700, color: q.warn ? C.errorRed : C.ink,
                }}>{q.v}%</span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: C.sub, marginTop: 10, lineHeight: 1.6 }}>
            {t.app_stats_bounds_note
              .replace('{low}', String(summary.bound_low))
              .replace('{high}', String(summary.bound_high))
              .replace('{n}', String(summary.unknown_stage))}
            {summary.withdrawn_early > 0 && (
              <> {t.app_stats_withdrawn_excluded_note.replace('{n}', String(summary.withdrawn_early))}</>
            )}
          </div>
        </Card>
      </div>

      {/* ── 時間軸（既存） ── */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 20,
      }}>
        <Card>
          <SectionTitle>{t.app_timeline_calendar}</SectionTitle>
          {data.daily_counts.length === 0
            ? <Empty>{t.app_timeline_no_data}</Empty>
            : <CalendarHeatmap dailyCounts={data.daily_counts} />}
        </Card>

        <Card>
          <SectionTitle>{t.app_timeline_agenda}</SectionTitle>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.sub, margin: '2px 0 6px' }}>
            {t.app_timeline_agenda_upcoming}
          </div>
          <AgendaList items={data.agenda.upcoming} emptyText={t.app_timeline_no_data} />
          <div style={{ fontSize: 12, fontWeight: 700, color: C.sub, margin: '14px 0 6px' }}>
            {t.app_timeline_agenda_past}
          </div>
          <AgendaList items={data.agenda.past.slice(0, 10)} emptyText={t.app_timeline_no_data} />
        </Card>
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 20,
      }}>
        <Card>
          <SectionTitle>{t.app_timeline_stage_durations}</SectionTitle>
          {durationKeys.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {durationKeys.map((k) => (
                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
                  <span style={{ minWidth: 72, color: C.ink }}>{statusLabels[k] ?? k}</span>
                  <ReportBar value={data.stage_durations[k].median_days} max={maxDuration} color={C.gold} width={120} />
                  <span style={{ fontSize: 11, color: C.sub }}>
                    {data.stage_durations[k].median_days}{t.app_timeline_days_suffix} · n={data.stage_durations[k].n}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle>{t.app_timeline_rejection_dist}</SectionTitle>
          {data.rejection_dist.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.rejection_dist.map((r) => (
                <div key={r.stage} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
                  <span style={{ minWidth: 72, color: C.ink }}>{r.label}</span>
                  <ReportBar value={r.n} max={maxRejection} color={C.errorRed} width={120} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card>
        <SectionTitle right={
          <span style={{ fontSize: 11, color: C.sub }}>{t.app_stats_withdrawn_note}</span>
        }>{t.app_timeline_self_withdrawn}</SectionTitle>
        {data.self_withdrawn.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.self_withdrawn.map((r) => (
              <div key={r.job_id} style={{
                display: 'flex', alignItems: 'center', gap: 10, fontSize: 13,
                padding: '6px 0', borderBottom: `1px solid ${alpha(C.border, 0.6)}`,
              }}>
                <span style={{ flex: 1, color: C.ink, fontWeight: 600 }}>{r.company}</span>
                {/* 結果が出る前に降りた案件だけ分母から外れている。その旨を明示する */}
                {r.before_result && (
                  <Badge color={C.lavender}>{t.app_stats_withdrawn_excluded}</Badge>
                )}
                <Badge>{r.reached_label || statusLabels[r.status] || r.status}</Badge>
                <span style={{
                  fontSize: 11, color: C.sub, maxWidth: 420,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }} title={r.note ?? ''}>{r.note}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};
