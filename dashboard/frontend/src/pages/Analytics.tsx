import React, { useEffect, useRef, useState } from 'react';
import { get } from '../api';
import { C, alpha, getTierLabel } from '../theme';
import { Card, SectionTitle, Empty, PageHeader, FilterGroup } from '../components/ui';
import { useHashFilters } from '../filters/useHashFilters';
import { useT } from '../i18n';

const GRANULARITIES = ['run', 'week', 'month'] as const;
type Granularity = typeof GRANULARITIES[number];
const PERIODS = 8;

const SRC_COLOR: Record<string, string> = {
  bizreach: '#E02020', linkedin_jp: '#0A66C2', indeed_jp: '#2164F3',
  'greenhouse-api': '#24A148', 'ashby-api': '#E8813A', 'lever-api': '#5B4CE2',
  // 非 ATS 直連來源：舊碼原本沒配色，全部落回同一灰，趨勢折線疊在一起分不出誰是誰
  recruiter_agent: '#C79A1B', green: '#D46A9E', jac_recruitment: '#3E5C8A', manual: '#8B6F47',
};
const SRC_LABEL: Record<string, string> = {
  bizreach: 'Bizreach', linkedin_jp: 'LinkedIn', indeed_jp: 'Indeed',
  'greenhouse-api': 'Greenhouse', 'ashby-api': 'Ashby', 'lever-api': 'Lever',
};

const SRC_DOMAIN: Record<string, string> = {
  bizreach: 'bizreach.jp', linkedin_jp: 'linkedin.com', indeed_jp: 'indeed.com',
  'greenhouse-api': 'greenhouse.io', 'ashby-api': 'ashbyhq.com', 'lever-api': 'lever.co',
};

const PT_COLOR: Record<string, string> = { direct: '#4A9076', shokai: '#D4883A', haken: '#8B6AAE' };
const PT_KEYS = ['direct', 'shokai', 'haken'] as const;

const TIER_COLOR: Record<string, string> = {
  ai_startup: C.gold, mega_venture: C.progressIndigo, traditional_sier: '#C9B99A', unknown: C.sub,
};
const VERDICT_COLOR = { go: C.successGreen, improve: '#C79A1B', skip: C.errorRed, unanalyzed: '#E0DAD2' };

interface AnalyticsData {
  sources: string[];
  summary: Record<string, {
    total: number; median_score: number | null; gap_covered: number;
    gap_pct: number; go_count: number; go_rate: number | null;
    salary_median: number | null; salary_count: number; japan_pct: number;
  }>;
  score_dist: Record<string, { bin: string; count: number; pct: number }[]>;
  verdict: Record<string, {
    go: number; improve: number; skip: number;
    unanalyzed: number; analyzed: number; total: number;
  }>;
  tier: Record<string, Record<string, number>>;
  geo: Record<string, { japan: number; overseas: number }>;
  posting_type: Record<string, Record<string, number>>;
  granularity: Granularity;
  trend: {
    buckets: string[];
    by_source: Record<string, { count: number[]; go_n: number[]; go_rate: (number | null)[] }>;
  };
}

// ── Sub-components ──

const Kpi: React.FC<{ value: React.ReactNode; label: string; accent?: string }> = ({ value, label, accent = C.ink }) => (
  <div style={{
    flex: '1 1 130px', minWidth: 110, background: C.card, borderRadius: 12,
    padding: '16px 18px', boxShadow: '0 1px 4px rgba(90,82,72,.06)',
    borderBottom: `3px solid ${alpha(accent, 0.19)}`,
  }}>
    <div style={{ fontSize: 28, fontWeight: 800, color: accent, fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 }}>
      {value}
    </div>
    <div style={{ fontSize: 11, color: C.sub, fontWeight: 500, marginTop: 8, letterSpacing: 0.3 }}>{label}</div>
  </div>
);

const StatCell: React.FC<{ value: React.ReactNode; label: string; accent?: string; trend?: React.ReactNode }> =
  ({ value, label, accent, trend }) => (
    <div style={{ flex: 1, background: C.bg, padding: '10px 6px', textAlign: 'center', minWidth: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
        fontSize: 15, fontWeight: 700, color: accent ?? C.ink,
        fontVariantNumeric: 'tabular-nums', lineHeight: 1.2,
      }}>{value}</div>
      {trend && <div style={{ display: 'flex', justifyContent: 'center', marginTop: 5 }}>{trend}</div>}
      <div style={{
        fontSize: 10, color: C.sub, marginTop: 3,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{label}</div>
    </div>
  );

/** 迷你趨勢折線。null（樣本不足）處斷開，不虛構連線。 */
const Sparkline: React.FC<{ values: (number | null)[]; color: string; width?: number; height?: number }> =
  ({ values, color, width = 64, height = 18 }) => {
    const nums = values.filter((v): v is number => v != null);
    if (nums.length < 2) return null;
    const lastIdx = values.length - 1;
    const maxV = Math.max(...nums);
    const minV = Math.min(...nums, 0);
    const range = maxV - minV || 1;
    const segments: string[] = [];
    let cur: string[] = [];
    values.forEach((v, i) => {
      if (v == null) {
        if (cur.length > 1) segments.push(cur.join(' '));
        cur = [];
        return;
      }
      const x = lastIdx > 0 ? (i / lastIdx) * width : 0;
      const y = height - ((v - minV) / range) * height;
      cur.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    });
    if (cur.length > 1) segments.push(cur.join(' '));
    return (
      <svg width={width} height={height} style={{ display: 'block', overflow: 'visible' }}>
        {segments.map((pts, i) => (
          <polyline key={i} points={pts} fill="none" stroke={color} strokeWidth={1.6}
            strokeLinecap="round" strokeLinejoin="round" opacity={0.8} />
        ))}
      </svg>
    );
  };

/** 對比最後兩個有效樣本的漲跌箭頭。任一端樣本不足（null）時不顯示，不做跨缺口比較。 */
const TrendArrow: React.FC<{ series: (number | null)[]; goodDirection?: 'up' | 'down' }> =
  ({ series, goodDirection = 'up' }) => {
    const curr = series[series.length - 1];
    const prev = series[series.length - 2];
    if (curr == null || prev == null) return null;
    const diff = curr - prev;
    if (Math.abs(diff) < 0.05) return <span style={{ color: C.sub, fontSize: 10 }}>–</span>;
    const up = diff > 0;
    const good = goodDirection === 'up' ? up : !up;
    return (
      <span style={{ color: good ? C.successGreen : C.errorRed, fontSize: 10, fontWeight: 700 }}>
        {up ? '▲' : '▼'}
      </span>
    );
  };

interface TrendSeries { key: string; color: string; label: string; values: (number | null)[]; }

const TREND_W = 640;
const TREND_PAD = { l: 34, r: 10, t: 10, b: 20 };

/** 完整趨勢折線圖：多來源疊線 + 格線 + 軸標籤 + 懸浮 tooltip。
 *  單一 y 軸——件數與 go率兩種量級用兩張獨立圖，不做雙軸（見 dataviz 準則）。 */
const TrendChart: React.FC<{
  buckets: string[];
  series: TrendSeries[];
  height?: number;
  valueSuffix?: string;
  emptyLabel: string;
}> = ({ buckets, series, height = 150, valueSuffix = '', emptyLabel }) => {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const active = series.filter((s) => s.values.some((v) => v != null));
  const n = buckets.length;
  const plotW = TREND_W - TREND_PAD.l - TREND_PAD.r;
  const plotH = height - TREND_PAD.t - TREND_PAD.b;
  const allVals = active.flatMap((s) => s.values.filter((v): v is number => v != null));

  if (active.length === 0 || allVals.length === 0) return <Empty>{emptyLabel}</Empty>;

  const maxV = Math.max(...allVals);
  const minV = Math.min(0, ...allVals);
  const range = maxV - minV || 1;
  const xAt = (i: number) => TREND_PAD.l + (n > 1 ? (i / (n - 1)) * plotW : plotW / 2);
  const yAt = (v: number) => TREND_PAD.t + plotH - ((v - minV) / range) * plotH;
  const gridVals = [minV, minV + range / 2, maxV];
  const showLabelIdx = new Set<number>(
    n <= 6 ? Array.from({ length: n }, (_, i) => i) : [0, Math.floor((n - 1) / 2), n - 1],
  );

  const handleMove = (e: React.MouseEvent<SVGRectElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * TREND_W;
    const idx = Math.round(((relX - TREND_PAD.l) / plotW) * (n - 1));
    setHoverIdx(Math.max(0, Math.min(n - 1, idx)));
  };

  return (
    <div style={{ position: 'relative' }}>
      <svg
        ref={svgRef} viewBox={`0 0 ${TREND_W} ${height}`} width="100%" height={height}
        style={{ display: 'block', overflow: 'visible' }}
      >
        {gridVals.map((v, i) => (
          <g key={i}>
            <line x1={TREND_PAD.l} x2={TREND_W - TREND_PAD.r} y1={yAt(v)} y2={yAt(v)}
              stroke={C.border} strokeWidth={1} opacity={0.5} />
            <text x={TREND_PAD.l - 5} y={yAt(v)} textAnchor="end" dominantBaseline="middle" fontSize={9} fill={C.sub}>
              {Math.round(v)}{valueSuffix}
            </text>
          </g>
        ))}
        {buckets.map((b, i) => showLabelIdx.has(i) && (
          <text key={i} x={xAt(i)} y={height - 4} textAnchor="middle" fontSize={9} fill={C.sub}>{b}</text>
        ))}
        {active.map((s) => {
          const segments: string[] = [];
          let cur: string[] = [];
          s.values.forEach((v, i) => {
            if (v == null) { if (cur.length > 1) segments.push(cur.join(' ')); cur = []; return; }
            cur.push(`${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`);
          });
          if (cur.length > 1) segments.push(cur.join(' '));
          const lastIdx = [...s.values.keys()].reverse().find((i) => s.values[i] != null);
          return (
            <g key={s.key}>
              {segments.map((pts, i) => (
                <polyline key={i} points={pts} fill="none" stroke={s.color} strokeWidth={2}
                  strokeLinecap="round" strokeLinejoin="round" />
              ))}
              {lastIdx != null && (
                <circle cx={xAt(lastIdx)} cy={yAt(s.values[lastIdx]!)} r={4} fill={s.color} stroke={C.card} strokeWidth={2} />
              )}
            </g>
          );
        })}
        {hoverIdx != null && (
          <g>
            <line x1={xAt(hoverIdx)} x2={xAt(hoverIdx)} y1={TREND_PAD.t} y2={TREND_PAD.t + plotH}
              stroke={C.ink} strokeWidth={1} opacity={0.25} />
            {active.map((s) => {
              const v = s.values[hoverIdx];
              if (v == null) return null;
              return <circle key={s.key} cx={xAt(hoverIdx)} cy={yAt(v)} r={3.5} fill={s.color} stroke={C.card} strokeWidth={1.5} />;
            })}
          </g>
        )}
        <rect x={TREND_PAD.l} y={0} width={plotW} height={height} fill="transparent"
          onMouseMove={handleMove} onMouseLeave={() => setHoverIdx(null)} />
      </svg>
      {hoverIdx != null && (
        <div style={{
          position: 'absolute', top: 4, right: 4, background: C.card, border: `1px solid ${C.border}`,
          borderRadius: 8, padding: '8px 10px', boxShadow: '0 2px 8px rgba(90,82,72,.12)',
          fontSize: 11, pointerEvents: 'none', minWidth: 110,
        }}>
          <div style={{ fontWeight: 700, color: C.ink, marginBottom: 4 }}>{buckets[hoverIdx]}</div>
          {active.map((s) => {
            const v = s.values[hoverIdx];
            return (
              <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 5, color: C.sub, marginTop: 2 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: s.color, flexShrink: 0 }} />
                <span style={{ flex: 1 }}>{s.label}</span>
                <span style={{ fontWeight: 600, color: C.ink }}>{v != null ? `${v}${valueSuffix}` : '—'}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

interface PlatformCardProps {
  source: string;
  s: AnalyticsData['summary'][string];
  pt: Record<string, number>;
  tierData: Record<string, number>;
  trend?: { count: number[]; go_n: number[]; go_rate: (number | null)[] };
  t: Record<string, string>;
}

const PlatformCard: React.FC<PlatformCardProps> = ({ source, s, pt, tierData, trend, t }) => {
  const brand = SRC_COLOR[source] ?? C.sub;
  const ptTotal = Object.values(pt).reduce((a, b) => a + b, 0);

  return (
    <Card style={{ borderLeft: `4px solid ${brand}`, padding: '20px 22px' }}>
      {/* Header: logo + name + total */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <img
            src={`https://www.google.com/s2/favicons?domain=${SRC_DOMAIN[source] ?? source}&sz=32`}
            width={18} height={18}
            style={{ flexShrink: 0, borderRadius: 4 }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
          <span style={{ fontSize: 15, fontWeight: 700, color: C.ink }}>{SRC_LABEL[source] ?? source}</span>
        </span>
        <div>
          <span style={{ fontSize: 26, fontWeight: 800, color: brand, fontVariantNumeric: 'tabular-nums' }}>
            {s.total}
          </span>
          <span style={{ fontSize: 12, fontWeight: 400, color: C.sub, marginLeft: 3 }}>{t.unit_job_count}</span>
        </div>
      </div>

      {/* Posting type segmented bar */}
      <div style={{ marginBottom: 16 }}>
        {ptTotal > 0 ? (
          <div style={{ display: 'flex', height: 30, borderRadius: 7, overflow: 'hidden' }}>
            {PT_KEYS.map((k) => {
              const v = pt[k] ?? 0;
              const pct = (v / ptTotal) * 100;
              if (pct < 0.5) return null;
              return (
                <div
                  key={k}
                  title={`${t[`analytics_${k}`] ?? k}: ${v} (${pct.toFixed(1)}%)`}
                  style={{
                    width: `${pct}%`, background: PT_COLOR[k],
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  {pct > 20 && (
                    <span style={{ fontSize: 11, fontWeight: 600, color: '#fff', whiteSpace: 'nowrap', opacity: 0.95 }}>
                      {t[`analytics_${k}`] ?? k} {v}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ height: 30, borderRadius: 7, background: '#F0EBE4' }} />
        )}
        <div style={{ display: 'flex', gap: 14, marginTop: 7 }}>
          {PT_KEYS.map((k) => {
            const v = pt[k] ?? 0;
            if (!v) return null;
            const pct = ptTotal > 0 ? (v / ptTotal * 100).toFixed(0) : '0';
            return (
              <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: C.sub }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: PT_COLOR[k], flexShrink: 0 }} />
                {t[`analytics_${k}`] ?? k} {pct}%
              </span>
            );
          })}
        </div>
      </div>

      {/* Stats strip */}
      <div style={{
        display: 'flex', gap: 1, background: `${alpha(C.border, 0.19)}`,
        borderRadius: 8, overflow: 'hidden', marginBottom: 14,
      }}>
        {trend && (
          <StatCell
            value={trend.count[trend.count.length - 1] ?? '—'}
            label={t.analytics_col_period_count}
            trend={<>
              <Sparkline values={trend.count} color={brand} />
              <TrendArrow series={trend.count} goodDirection="up" />
            </>}
          />
        )}
        <StatCell value={s.median_score ?? '—'} label={t.analytics_col_median} />
        {/* gap 分析樣本 <30 時比率不可靠：附 n= 並降精度提示 */}
        <StatCell
          value={s.go_rate != null ? (
            s.gap_covered < 30 ? (
              <span title={t.analytics_small_sample} style={{ cursor: 'help' }}>
                ~{Math.round(s.go_rate)}%
                <span style={{ fontSize: 10, fontWeight: 500, color: '#C79A1B', marginLeft: 3 }}>n={s.gap_covered}</span>
              </span>
            ) : `${s.go_rate}%`
          ) : '—'}
          label={t.analytics_col_go_rate}
          accent={s.go_rate != null && s.gap_covered >= 30 && s.go_rate >= 40 ? C.successGreen : undefined}
          trend={trend && (
            <>
              <Sparkline values={trend.go_rate} color={brand} />
              <TrendArrow series={trend.go_rate} goodDirection="up" />
            </>
          )}
        />
        <StatCell value={s.salary_median != null ? `${s.salary_median}${t.analytics_salary_unit}` : '—'} label={t.analytics_col_salary} />
        <StatCell value={`${s.japan_pct}%`} label={t.analytics_col_japan} />
      </div>

      {/* Tier inline */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {['ai_startup', 'mega_venture', 'traditional_sier'].map((k) => {
          const n = tierData[k] ?? 0;
          if (!n) return null;
          return (
            <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: C.sub }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: TIER_COLOR[k], flexShrink: 0 }} />
              {getTierLabel(k)} {n}
            </span>
          );
        })}
      </div>
    </Card>
  );
};

const ComparisonRow: React.FC<{
  source: string;
  segments: { key: string; value: number; color: string; label: string }[];
  total: number;
  annotation?: string;
}> = ({ source, segments, total, annotation }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
    <div style={{ width: 95, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
      <img
        src={`https://www.google.com/s2/favicons?domain=${SRC_DOMAIN[source] ?? source}&sz=32`}
        width={14} height={14}
        style={{ flexShrink: 0, borderRadius: 3 }}
        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
      />
      <span style={{ fontSize: 12, fontWeight: 600, color: SRC_COLOR[source] ?? C.sub }}>
        {SRC_LABEL[source] ?? source}
      </span>
    </div>
    <div style={{ flex: 1, display: 'flex', height: 22, borderRadius: 5, overflow: 'hidden' }}>
      {segments.map((s) => {
        const pct = total > 0 ? (s.value / total) * 100 : 0;
        if (pct < 0.5) return null;
        return (
          <div
            key={s.key}
            title={`${s.label}: ${s.value} (${pct.toFixed(1)}%)`}
            style={{
              width: `${pct}%`, background: s.color,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            {pct > 14 && (
              <span style={{
                fontSize: 10, fontWeight: 600, whiteSpace: 'nowrap',
                color: s.color === '#E0DAD2' ? C.sub : '#fff',
              }}>{s.value}</span>
            )}
          </div>
        );
      })}
    </div>
    {annotation && (
      <span style={{ fontSize: 11, color: C.sub, whiteSpace: 'nowrap', minWidth: 36, textAlign: 'right' }}>
        {annotation}
      </span>
    )}
  </div>
);

const MiniHist: React.FC<{
  source: string; bins: { bin: string; pct: number; count: number }[];
}> = ({ source, bins }) => {
  const maxPct = Math.max(...bins.map((b) => b.pct), 1);
  const color = SRC_COLOR[source] ?? C.sub;
  return (
    <div style={{ flex: '1 1 130px', minWidth: 110, maxWidth: 180 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color, marginBottom: 6, textAlign: 'center' }}>
        {SRC_LABEL[source] ?? source}
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 56 }}>
        {bins.map((b) => (
          <div key={b.bin} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div
              title={`${b.bin}: ${b.count} (${b.pct}%)`}
              style={{
                width: '100%', maxWidth: 26,
                height: `${maxPct > 0 ? (b.pct / maxPct) * 48 : 0}px`,
                background: color, borderRadius: '3px 3px 0 0',
                minHeight: b.pct > 0 ? 3 : 0, opacity: 0.8,
              }}
            />
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 3, marginTop: 3 }}>
        {bins.map((b) => (
          <div key={b.bin} style={{ flex: 1, fontSize: 9, color: C.sub, textAlign: 'center', lineHeight: 1.1 }}>
            {b.bin}
          </div>
        ))}
      </div>
    </div>
  );
};

const Legend: React.FC<{ items: { label: string; color: string }[] }> = ({ items }) => (
  <div style={{ display: 'flex', gap: 14, marginTop: 8, flexWrap: 'wrap' }}>
    {items.map((l) => (
      <span key={l.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: C.sub }}>
        <span style={{ width: 10, height: 10, borderRadius: 2, background: l.color, flexShrink: 0 }} />
        {l.label}
      </span>
    ))}
  </div>
);

// ── Main ──

export const Analytics: React.FC = () => {
  const t = useT();
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [f, updateF] = useHashFilters({ granularity: 'week' as Granularity });

  useEffect(() => {
    setData(null);
    get(`/api/analytics?granularity=${f.granularity}&periods=${PERIODS}`).then(setData);
  }, [f.granularity]);

  if (!data) return <Empty>{t.jobs_loading}</Empty>;

  const { sources, summary, score_dist, verdict, tier, posting_type, trend } = data;

  // Aggregate KPIs
  const allTotal = sources.reduce((sum, s) => sum + (summary[s]?.total ?? 0), 0);
  const allDirect = sources.reduce((sum, s) => sum + (posting_type[s]?.direct ?? 0), 0);
  const directRate = allTotal > 0 ? Math.round(allDirect / allTotal * 100) : 0;
  let scoreSum = 0, scoreWeight = 0;
  for (const s of sources) {
    const sm = summary[s];
    if (sm?.median_score != null) { scoreSum += sm.median_score * sm.total; scoreWeight += sm.total; }
  }
  const avgScore = scoreWeight > 0 ? Math.round(scoreSum / scoreWeight) : null;
  const allGapCovered = sources.reduce((sum, s) => sum + (summary[s]?.gap_covered ?? 0), 0);
  const gapRate = allTotal > 0 ? Math.round(allGapCovered / allTotal * 100) : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <PageHeader
        title={t.analytics_title}
        subtitle={t.analytics_lag_note}
        right={
          <FilterGroup
            options={GRANULARITIES.map((g) => ({ key: g, label: t[`analytics_granularity_${g}`] }))}
            value={f.granularity}
            onChange={(k) => updateF({ granularity: k as Granularity })}
          />
        }
      />

      {/* 1. KPI strip */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Kpi value={allTotal.toLocaleString()} label={t.analytics_col_total} />
        <Kpi value={`${directRate}%`} label={t.analytics_direct} accent={PT_COLOR.direct} />
        <Kpi value={avgScore ?? '—'} label={t.analytics_col_median} />
        <Kpi value={`${gapRate}%`} label={t.analytics_col_gap} accent={C.progressIndigo} />
      </div>

      {/* 2. Trend charts — 件數/go率隨 granularity 切換的真實時序，非快照 */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))',
        gap: 14,
      }}>
        <Card>
          <SectionTitle>{t.analytics_trend_count}</SectionTitle>
          <TrendChart
            buckets={trend.buckets}
            series={sources.filter((s) => summary[s]).map((s) => ({
              key: s, color: SRC_COLOR[s] ?? C.sub, label: SRC_LABEL[s] ?? s,
              values: trend.by_source[s]?.count ?? [],
            }))}
            emptyLabel={t.analytics_trend_empty}
          />
          <Legend items={sources.filter((s) => summary[s]).map((s) => ({ label: SRC_LABEL[s] ?? s, color: SRC_COLOR[s] ?? C.sub }))} />
        </Card>
        <Card>
          <SectionTitle>{t.analytics_trend_go_rate}</SectionTitle>
          <TrendChart
            buckets={trend.buckets}
            series={sources.filter((s) => summary[s]).map((s) => ({
              key: s, color: SRC_COLOR[s] ?? C.sub, label: SRC_LABEL[s] ?? s,
              values: trend.by_source[s]?.go_rate ?? [],
            }))}
            valueSuffix="%"
            emptyLabel={t.analytics_trend_empty}
          />
          <Legend items={sources.filter((s) => summary[s]).map((s) => ({ label: SRC_LABEL[s] ?? s, color: SRC_COLOR[s] ?? C.sub }))} />
        </Card>
      </div>

      {/* 3. Platform cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 360px), 1fr))',
        gap: 14,
      }}>
        {sources.map((src) => {
          const s = summary[src];
          if (!s) return null;
          return (
            <PlatformCard
              key={src}
              source={src}
              s={s}
              pt={posting_type[src] ?? {}}
              tierData={tier[src] ?? {}}
              trend={trend.by_source[src]}
              t={t}
            />
          );
        })}
      </div>

      {/* 4. Cross-platform comparisons */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))',
        gap: 14,
      }}>
        <Card>
          <SectionTitle>{t.analytics_verdict}</SectionTitle>
          {sources.map((src) => {
            const v = verdict[src];
            if (!v) return null;
            const segments = [
              { key: 'go', value: v.go, color: VERDICT_COLOR.go, label: t.analytics_go },
              { key: 'improve', value: v.improve, color: VERDICT_COLOR.improve, label: t.analytics_improve },
              { key: 'skip', value: v.skip, color: VERDICT_COLOR.skip, label: t.analytics_skip },
              { key: 'unanalyzed', value: v.unanalyzed, color: VERDICT_COLOR.unanalyzed, label: t.analytics_unanalyzed },
            ];
            const pct = v.total > 0 ? ((v.analyzed / v.total) * 100).toFixed(0) : '0';
            return <ComparisonRow key={src} source={src} segments={segments} total={v.total} annotation={`${pct}%`} />;
          })}
          <Legend items={[
            { label: t.analytics_go, color: VERDICT_COLOR.go },
            { label: t.analytics_improve, color: VERDICT_COLOR.improve },
            { label: t.analytics_skip, color: VERDICT_COLOR.skip },
            { label: t.analytics_unanalyzed, color: VERDICT_COLOR.unanalyzed },
          ]} />
        </Card>

        <Card>
          <SectionTitle>{t.analytics_score_dist}</SectionTitle>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, justifyContent: 'space-between' }}>
            {sources.map((src) => (
              <MiniHist key={src} source={src} bins={score_dist[src] ?? []} />
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
};
