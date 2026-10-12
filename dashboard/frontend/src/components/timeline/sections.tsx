import React from 'react';
import { C, alpha } from '../../theme';
import { Empty, Badge, STAGE_ORDER } from '../ui';
import { useT } from '../../i18n';
import { RateRow } from '../RateRow';
import { ApplyCalendar } from '../ApplyCalendar';
import { WeeklyCard, WaitCurveCard, InterviewLoadCard } from '../TimeStats';
import { InfoCard, Grid, Collapsible, DotHistogram, MiniStats, BarRow } from './parts';
import { fmtDate, useTimelineLabels } from './labels';
import { findBottleneck, coverageWarnings } from './insights';
import type { AgendaItem, TimelineData } from './types';

const DAY_MS = 86_400_000;

/** 今日から何日後か（0=今日）。ローカル日付で比べる。 */
const daysUntil = (iso: string): number => {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((new Date(`${iso}T00:00:00`).getTime() - today.getTime()) / DAY_MS);
};

// ───────────────────────── ① 近期行程 ─────────────────────────

const AgendaList: React.FC<{ items: AgendaItem[] }> = ({ items }) => {
  const t = useT();
  const { statusLabels } = useTimelineLabels();
  if (items.length === 0) return <Empty>{t.app_timeline_no_upcoming}</Empty>;
  const relLabel = (n: number): string =>
    n <= 0 ? t.tl_today : n === 1 ? t.tl_tomorrow : t.tl_days_until.replace('{n}', String(n));
  const [next, ...rest] = items;
  const nextIn = daysUntil(next.date);
  return (
    <div className="tl-agenda">
      {/* 直近の 1 件だけ大きく：今日・明日なら赤で目を止める */}
      <div className={`tl-next${nextIn <= 1 ? ' tl-next--soon' : ''}`}>
        <span className="tl-eyebrow">{t.tl_next_label}</span>
        <div className="tl-next-when">
          <span className="tl-next-rel num-display">{relLabel(nextIn)}</span>
          <span className="tl-next-date">{fmtDate(next.date)}{next.time ? ` ${next.time}` : ''}</span>
        </div>
        <div className="tl-next-company" title={next.company}>{next.company}</div>
        <div className="tl-next-meta">
          <Badge color={C.progressIndigo}>{next.stage}</Badge>
          <span>{statusLabels[next.status] ?? next.status}</span>
        </div>
      </div>
      {rest.map((it) => {
        const d = daysUntil(it.date);
        return (
          <div key={`${it.job_id}-${it.date}`} className="tl-agenda-row">
            <div className="tl-agenda-date">
              <strong>{fmtDate(it.date)}</strong>
              <span className={d <= 1 ? 'is-soon' : undefined}>{relLabel(d)}{it.time ? ` ${it.time}` : ''}</span>
            </div>
            <span className="tl-agenda-company" title={it.company}>{it.company}</span>
            <span className="tl-agenda-meta">
              <Badge color={C.progressIndigo}>{it.stage}</Badge>
              <span>{statusLabels[it.status] ?? it.status}</span>
            </span>
          </div>
        );
      })}
    </div>
  );
};

export const UpcomingBody: React.FC<{ data: TimelineData }> = ({ data }) => {
  const t = useT();
  return (
    <Grid min={400} stretch>
      <InfoCard title={t.app_timeline_agenda} right={`${data.agenda.upcoming.length}`}>
        <AgendaList items={data.agenda.upcoming} />
      </InfoCard>
      {data.time && <InterviewLoadCard load={data.time.interview_load} />}
    </Grid>
  );
};

// ───────────────────────── ② 整体成績 ─────────────────────────

export const OverviewBody: React.FC<{ data: TimelineData }> = ({ data }) => {
  const t = useT();
  const { stage, statusLabels } = useTimelineLabels();
  const maxFunnel = data.funnel[0]?.n || 1;
  const bottleneck = findBottleneck(data.funnel)?.f.stage;
  const totalRej = data.rejection_dist.reduce((s, r) => s + r.n, 0);
  const maxRej = Math.max(1, ...data.rejection_dist.map((r) => r.n));

  return (
    <>
      <Grid min={420}>
        <InfoCard title={t.app_stats_funnel} hint={t.tl_hint_funnel}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            {data.funnel.map((f) => {
              const worst = f.stage === bottleneck;
              return (
                <BarRow key={f.stage} label={stage(f.stage)} value={f.n} max={maxFunnel} emphasis={worst}
                  color={f.stage === 'offer' ? C.successGreen : worst ? C.errorRed : C.gold}
                  note={<>
                    <span style={{ color: worst ? C.errorRed : C.sub, fontWeight: worst ? 700 : 400 }}>
                      {f.conv_from_prev != null ? `→ ${f.conv_from_prev}%` : ''}
                    </span>
                    {/* 本人が降りた件数。企業に切られた数と混ぜない */}
                    {f.withdrawn > 0 && (
                      <span style={{ color: C.lavender, fontWeight: 600, marginLeft: 6 }}>
                        {t.app_stats_funnel_withdrawn.replace('{n}', String(f.withdrawn))}
                      </span>
                    )}
                  </>}
                />
              );
            })}
          </div>
        </InfoCard>

        <InfoCard title={t.app_timeline_rejection_dist} hint={t.tl_hint_rejection} right={`n=${totalRej}`}>
          {data.rejection_dist.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
              {data.rejection_dist.map((r) => (
                <BarRow key={r.stage} label={r.label} value={r.n} max={maxRej}
                  color={r.stage === 'unspecified' ? alpha(C.sub, 0.45) : alpha(C.errorRed, 0.75)}
                  note={`${Math.round((r.n / Math.max(1, totalRej)) * 100)}%`} />
              ))}
            </div>
          )}
        </InfoCard>
      </Grid>

      {data.self_withdrawn.length > 0 && (
        <Collapsible summary={t.tl_withdrawn_toggle.replace('{n}', String(data.self_withdrawn.length))}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {data.self_withdrawn.map((r) => (
              <div key={r.job_id} style={{
                display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, flexWrap: 'wrap',
                padding: '6px 0', borderBottom: `1px solid ${alpha(C.border, 0.6)}`,
              }}>
                <span style={{ flex: '1 1 160px', color: C.ink, fontWeight: 600 }}>{r.company}</span>
                {/* 結果が出る前に降りた案件だけ分母から外れている。その旨を明示する */}
                {r.before_result && <Badge color={C.lavender}>{t.app_stats_withdrawn_excluded}</Badge>}
                <Badge>{r.reached_label || statusLabels[r.status] || r.status}</Badge>
                <span style={{
                  flex: '1 1 200px', fontSize: 11, color: C.sub,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }} title={r.note ?? ''}>{r.note}</span>
              </div>
            ))}
          </div>
        </Collapsible>
      )}
    </>
  );
};

// ───────────────────────── ③ 趨勢 ─────────────────────────

export const TrendBody: React.FC<{ data: TimelineData }> = ({ data }) => {
  const t = useT();
  const baseline = data.summary.pass_rate;
  const scaleSrc = data.cohorts.filter((c) => !c.insufficient);
  const cohortMax = Math.min(100, Math.max(
    20, ...(scaleSrc.length ? scaleSrc : data.cohorts).map((c) => c.ci_high ?? 0),
  ) * 1.05);
  return (
    <>
      {data.time && <WeeklyCard weekly={data.time.weekly} baseline={baseline} salary={data.salary_ask?.items ?? []} />}
      <Grid>
        <InfoCard title={t.app_stats_cohorts} hint={t.tl_hint_cohorts}>
          {data.cohorts.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
              {data.cohorts.map((c) => (
                <RateRow key={c.month} label={c.month} item={c} baseline={baseline}
                  scaleMax={cohortMax} insufficientText={t.app_stats_insufficient} />
              ))}
            </div>
          )}
        </InfoCard>
        <InfoCard title={t.app_timeline_calendar} hint={t.tl_hint_calendar}>
          {data.daily_counts.length === 0
            ? <Empty>{t.app_timeline_no_data}</Empty>
            : <ApplyCalendar dailyCounts={data.daily_counts} />}
        </InfoCard>
      </Grid>
    </>
  );
};

// ───────────────────────── ④ 等待 ─────────────────────────

export const WaitBody: React.FC<{ data: TimelineData }> = ({ data }) => {
  const t = useT();
  const { statusLabels } = useTimelineLabels();
  const turn = data.shorui_turnaround;
  const days = t.app_timeline_days_suffix;
  const durationKeys = [...STAGE_ORDER, 'rejected'].filter((k) => data.stage_durations[k]);
  const maxDuration = Math.max(1, ...durationKeys.map((k) => data.stage_durations[k].median_days));
  // 左列に分布 2 枚を積み、右に生存曲線。3 枚を auto-fit に流すと 3 枚目が片側だけ残る
  return (
    <div className="tl-split">
      <div className="tl-stack">
        <InfoCard title={t.app_timeline_shorui_turnaround} hint={t.tl_hint_turnaround} right={`n=${turn.n}`}>
          {turn.n === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
            <>
              <MiniStats items={[
                [t.app_timeline_median_days, `${turn.median_days}${days}`],
                [t.app_timeline_mean_days, `${turn.mean_days}${days}`],
              ]} />
              <DotHistogram points={turn.points} color={C.progressIndigo} tickEvery={5} suffix={days}
                marker={turn.median_days} label={t.app_timeline_shorui_turnaround} />
              {turn.pending.n > 0 && (
                <div style={{ fontSize: 11, color: C.sub, marginTop: 10 }}>
                  {t.app_timeline_pending_note
                    .replace('{n}', String(turn.pending.n))
                    .replace('{days}', String(turn.pending.median_days))}
                </div>
              )}
            </>
          )}
        </InfoCard>

        <InfoCard title={t.app_timeline_stage_durations} hint={t.tl_hint_durations}>
          {durationKeys.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
              {durationKeys.map((k) => {
                const d = data.stage_durations[k];
                return (
                  <BarRow key={k} label={statusLabels[k] ?? k} value={d.median_days} max={maxDuration}
                    color={d.n < 10 ? alpha(C.gold, 0.4) : C.gold}
                    display={`${d.median_days}${days}`} note={`n=${d.n}${d.n < 10 ? ' ⚠' : ''}`} />
                );
              })}
            </div>
          )}
        </InfoCard>
      </div>
      {data.time && <WaitCurveCard curve={data.time.wait_curve} />}
    </div>
  );
};

// ───────────────────────── ⑥ 可信度 ─────────────────────────

export const QualityBody: React.FC<{ data: TimelineData }> = ({ data }) => {
  const t = useT();
  const { summary, quality } = data;
  const rows = [
    { label: t.app_stats_quality_unknown, v: quality.unknown_stage_pct, warn: quality.unknown_stage_pct > 10 },
    { label: t.app_stats_quality_tier, v: quality.tier_coverage, warn: quality.tier_coverage < 50 },
    { label: t.app_stats_quality_recommend, v: quality.recommend_coverage, warn: quality.recommend_coverage < 50 },
    { label: t.app_stats_quality_employee, v: quality.employee_coverage, warn: quality.employee_coverage < 50 },
  ];
  return (
    <InfoCard title={t.app_stats_quality} hint={t.tl_hint_quality}
      right={coverageWarnings(quality) > 0 ? '⚠' : undefined}>
      <div className="tl-quality-grid">
        {rows.map((q) => (
          <BarRow key={q.label} label={q.label} value={Math.min(100, q.v)} max={100}
            color={q.warn ? C.errorRed : C.successGreen} display={`${q.v}%`} labelWidth={150} />
        ))}
      </div>
      <div style={{ fontSize: 12, color: C.sub, marginTop: 12, lineHeight: 1.7 }}>
        {t.app_stats_bounds_note
          .replace('{low}', String(summary.bound_low))
          .replace('{high}', String(summary.bound_high))
          .replace('{n}', String(summary.unknown_stage))}
        {summary.withdrawn_early > 0 && (
          <> {t.app_stats_withdrawn_excluded_note.replace('{n}', String(summary.withdrawn_early))}</>
        )}
      </div>
    </InfoCard>
  );
};
