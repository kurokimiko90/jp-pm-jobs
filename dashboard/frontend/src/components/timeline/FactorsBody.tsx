import React from 'react';
import { C } from '../../theme';
import { Empty } from '../ui';
import { useT } from '../../i18n';
import { RateRow } from '../RateRow';
import { SalaryAskCard } from '../SalaryAskCard';
import { ApplyRequestCard } from '../ApplyRequestCard';
import { InfoCard, Grid, Collapsible, DotHistogram, MiniStats, padPoints } from './parts';
import { useTimelineLabels } from './labels';
import type { RecommendDistribution, Segment, TimelineData } from './types';

const effectPct = (e: number): number => Math.round(e * 100);

const SegmentCard: React.FC<{ segment: Segment; baseline: number | null }> = ({ segment, baseline }) => {
  const t = useT();
  const { dim, key } = useTimelineLabels();
  // 目盛り上限は CI 上端に合わせる（常に 0-100 だと差が潰れて読めない）。
  // ただし n=1 の CI は 100% まで伸びるので、標本十分な行だけで軸を決め、
  // 参考値の行はクリップして表示する（1 件の外れ値に主力の差を潰させない）。
  const scaleSource = segment.items.filter((i) => !i.insufficient);
  const scaleMax = Math.min(100, Math.max(
    20, ...(scaleSource.length ? scaleSource : segment.items).map((i) => i.ci_high ?? 0),
  ) * 1.05);
  const right = [
    segment.multi_label ? t.app_stats_multi_label : null,
    t.tl_effect_badge.replace('{pct}', String(effectPct(segment.max_effect))),
  ].filter(Boolean).join(' · ');
  return (
    <InfoCard title={dim(segment.dimension)} right={right}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        {segment.items.map((it) => (
          <RateRow key={it.key} label={key(segment.dimension, it.key)} item={it}
            baseline={baseline} scaleMax={scaleMax} insufficientText={t.app_stats_insufficient} />
        ))}
      </div>
    </InfoCard>
  );
};

/** 投遞 vs 通過を同じ横軸で上下に並べる。形が似ていれば、その点数は選考結果を分けていない。 */
const ScoreCompareCard: React.FC<{ dist: RecommendDistribution }> = ({ dist }) => {
  const t = useT();
  const rows = [
    { d: dist.applied, label: t.tl_score_applied_short, color: C.progressIndigo },
    { d: dist.passed, label: t.tl_score_passed_short, color: C.successGreen },
  ];
  const all = rows.flatMap((r) => r.d.points.map((p) => p.value));
  if (all.length === 0) {
    return <InfoCard title={t.tl_score_title}><Empty>{t.app_timeline_no_data}</Empty></InfoCard>;
  }
  const min = Math.min(...all);
  const max = Math.max(...all);
  const unknown = dist.applied.unknown_n;
  return (
    <InfoCard title={t.tl_score_title} hint={t.tl_hint_score}
      right={unknown > 0 ? t.app_timeline_score_unknown.replace('{n}', String(unknown)) : undefined}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {rows.map(({ d, label, color }) => (
          <div key={label}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color }}>{label}</span>
              <span style={{ fontSize: 11, color: C.sub }}>n={d.n}</span>
            </div>
            <MiniStats items={[[t.app_timeline_median_days, d.median ?? '—'], [t.app_timeline_mean_days, d.mean ?? '—']]} />
            <DotHistogram points={padPoints(d.points, min, max)} color={color} tickEvery={5}
              marker={d.median} label={label} />
          </div>
        ))}
      </div>
    </InfoCard>
  );
};

export const FactorsBody: React.FC<{ data: TimelineData }> = ({ data }) => {
  const t = useT();
  const { dim } = useTimelineLabels();
  const baseline = data.summary.pass_rate;
  const strong = data.segments.filter((s) => s.informative);
  const weak = data.segments.filter((s) => !s.informative);
  const hasControlled = data.salary_ask || data.apply_request;
  return (
    <>
      <div style={{ fontSize: 12, color: C.sub }}>
        {t.app_stats_baseline} <strong style={{ color: C.ink }}>{baseline}%</strong>
      </div>
      {strong.length === 0 ? <Empty>{t.app_timeline_no_data}</Empty> : (
        <Grid min={380}>
          {strong.map((seg) => <SegmentCard key={seg.dimension} segment={seg} baseline={baseline} />)}
        </Grid>
      )}

      {weak.length > 0 && (
        <Collapsible summary={t.tl_weak_dims
          .replace('{n}', String(weak.length))
          .replace('{list}', weak.map((s) => `${dim(s.dimension)} ±${effectPct(s.max_effect)}%`).join(' · '))}>
          <Grid min={380}>
            {weak.map((seg) => <SegmentCard key={seg.dimension} segment={seg} baseline={baseline} />)}
          </Grid>
        </Collapsible>
      )}

      <ScoreCompareCard dist={data.recommend_distribution} />

      {hasControlled && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15, color: C.ink }}>{t.tl_controlled_title}</h3>
            <div style={{ fontSize: 12, color: C.sub, marginTop: 2, lineHeight: 1.6 }}>{t.tl_controlled_hint}</div>
          </div>
          {data.salary_ask && <SalaryAskCard data={data.salary_ask} />}
          {data.apply_request && <ApplyRequestCard data={data.apply_request} />}
        </div>
      )}
    </>
  );
};
