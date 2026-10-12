import React, { useEffect, useRef, useState } from 'react';
import { C, alpha } from '../theme';
import { Card, SectionTitle, Empty } from './ui';
import { useT } from '../i18n';
import { RateRow, type RateItem } from './RateRow';
import type { SalaryAskItem } from './SalaryAskCard';

// ───────────────────────── 型（app_time_stats.py と対応） ─────────────────────────

export interface WeekRow {
  week: string; applied: number;
  pass: number; fail: number; pending: number; withdrawn: number; unknown: number;
  rolling_rate: number | null; rolling_ci_low: number | null; rolling_ci_high: number | null;
  rolling_decided: number; rolling_insufficient: boolean;
}
export interface WaitCurve {
  points: (RateItem & { day: number; waiting_now: number })[];
  median_pass_days: number | null; median_fail_days: number | null; missing_dates: number;
}
export interface InterviewLoad {
  weeks: { week: string; n: number; when: 'past' | 'current' | 'future' }[];
  max: number; busy_weeks: number; busy_threshold?: number;
}
export interface TimeStatsData { weekly: WeekRow[]; wait_curve: WaitCurve; interview_load: InterviewLoad }

// 結果の色。通過/未過は意味を持つので状態色（通過を強調、未過は中立の灰）。
// dataviz validate_palette.js で隣接ペアの CVD/通常視 ΔE を確認済み（2026-09）。
// 灰は意図的な中立色なので lightness/chroma の FAIL は想定内。コントラスト不足は凡例＋表で補う
const OUT = { pass: '#3C6440', fail: '#B3AAA0', pending: '#5B82C8', other: '#EDE6DC' } as const;
type OutKey = keyof typeof OUT;
const outcomeKey = (o: string): OutKey =>
  o === 'pass' || o === 'fail' || o === 'pending' ? o : 'other';

const GRID = '#EFE7DC';
const PAD_L = 34;
const PAD_R = 8;
const AXIS_H = 18;

/** 実寸の幅で描く（viewBox で伸縮させると、全幅カードで文字 15px・半幅で 7px になる）。 */
function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => setW(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}
const plotW = (w: number): number => Math.max(0, w - PAD_L - PAD_R);

const md = (iso: string): string => { const [, m, d] = iso.split('-'); return `${+m}/${+d}`; };
const niceMax = (v: number, step: number): number => Math.max(step, Math.ceil(v / step) * step);

/** 上端だけ 4px 角丸・基線側は直角の柱（dataviz mark spec）。 */
const colPath = (x: number, y: number, w: number, h: number, r = 4): string => {
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
};

/** 凡例は印の形をなぞる（柱＝四角、線＝短い線）。 */
const Legend: React.FC<{ items: [string, string][]; line?: [string, string] }> = ({ items, line }) => (
  <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 11, color: C.sub }}>
    {items.map(([color, label]) => (
      <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
        <span style={{ width: 10, height: 10, borderRadius: 2, background: color }} />{label}
      </span>
    ))}
    {line && (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
        <span style={{ width: 14, height: 0, borderTop: `1.5px solid ${line[0]}` }} />{line[1]}
      </span>
    )}
  </div>
);

const ChartLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 11, fontWeight: 700, color: C.sub, letterSpacing: '0.04em', margin: '14px 0 4px' }}>{children}</div>
);

/** y 軸の目盛り線（実線ヘアライン）と数値。 */
const YGrid: React.FC<{ w: number; ticks: number[]; y: (v: number) => number; fmt?: (v: number) => string }> = ({ w, ticks, y, fmt }) => (
  <>
    {ticks.map((v) => (
      <g key={v}>
        <line x1={PAD_L} x2={w - PAD_R} y1={y(v)} y2={y(v)} stroke={GRID} strokeWidth={1} />
        <text x={PAD_L - 6} y={y(v) + 3.5} textAnchor="end" fontSize={11} fill={C.sub}
          style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt ? fmt(v) : v}</text>
      </g>
    ))}
  </>
);

/** 週の帯：hover / focus で同じ週を 3 枚のチャート全部でハイライトする当たり判定。 */
const WeekBands: React.FC<{
  w: number; n: number; top: number; height: number; hover: number | null;
  onHover: (i: number | null) => void; label: (i: number) => string;
}> = ({ w, n, top, height, hover, onHover, label }) => {
  const band = plotW(w) / n;
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <rect key={i} x={PAD_L + band * i} y={top} width={band} height={height}
          fill={hover === i ? alpha(C.ink, 0.05) : 'transparent'}
          tabIndex={0} aria-label={label(i)} style={{ outline: 'none', cursor: 'default' }}
          onMouseEnter={() => onHover(i)} onFocus={() => onHover(i)} onBlur={() => onHover(null)} />
      ))}
    </>
  );
};

const XAxis: React.FC<{ w: number; weeks: string[]; top: number }> = ({ w, weeks, top }) => {
  const band = plotW(w) / weeks.length;
  const every = Math.max(1, Math.ceil(34 / band)); // ラベル 1 つに 34px 未満なら間引く
  return (
    <>
      {weeks.map((wk, i) => (i % every === 0 ? (
        <text key={wk} x={PAD_L + band * (i + 0.5)} y={top + 13} textAnchor="middle" fontSize={11} fill={C.sub}
          style={{ fontVariantNumeric: 'tabular-nums' }}>{md(wk)}</text>
      ) : null))}
    </>
  );
};

// ───────────────────────── 週次（件数 / 滾動通過率 / 年収回答） ─────────────────────────

export const WeeklyCard: React.FC<{
  weekly: WeekRow[]; baseline: number | null; salary: SalaryAskItem[];
}> = ({ weekly, baseline, salary }) => {
  const t = useT();
  const [hover, setHover] = useState<number | null>(null);
  const [boxRef, W] = useWidth();
  if (weekly.length === 0) return <Card><SectionTitle>{t.time_weekly_title}</SectionTitle><Empty>{t.app_timeline_no_data}</Empty></Card>;

  const n = weekly.length;
  const PLOT_W = plotW(W);
  const band = PLOT_W / n;
  const colW = Math.min(24, band * 0.62);
  const cx = (i: number) => PAD_L + band * (i + 0.5);
  const other = (w: WeekRow) => w.withdrawn + w.unknown;

  // ① 件数（積み上げ柱）
  const H1 = 130;
  const maxApplied = niceMax(Math.max(...weekly.map((w) => w.applied)), 10);
  const y1 = (v: number) => 6 + H1 * (1 - v / maxApplied);
  // ② 滾動通過率（線 + 95%CI の薄い帯）。軸は標本十分な点の CI 上端で決める
  const H2 = 110;
  const sufficient = weekly.filter((w) => !w.rolling_insufficient && w.rolling_ci_high != null);
  const maxRate = niceMax(Math.max(baseline ?? 0, ...(sufficient.length ? sufficient : weekly).map((w) => w.rolling_ci_high ?? 0)), 10);
  const y2 = (v: number) => 6 + H2 * (1 - Math.min(v, maxRate) / maxRate);
  const ratePts = weekly.map((w, i) => (w.rolling_rate == null ? null : { i, w }));
  const linePts = ratePts.filter((p): p is { i: number; w: WeekRow } => p !== null);
  const linePath = linePts.map((p, k) => `${k ? 'L' : 'M'}${cx(p.i)},${y2(p.w.rolling_rate!)}`).join(' ');
  const bandPath = linePts.length > 1
    ? `M${linePts.map((p) => `${cx(p.i)},${y2(p.w.rolling_ci_high ?? p.w.rolling_rate!)}`).join(' L')}`
      + ` L${[...linePts].reverse().map((p) => `${cx(p.i)},${y2(p.w.rolling_ci_low ?? p.w.rolling_rate!)}`).join(' L')} Z`
    : '';
  // ③ 年収回答（日付 → 週軸上の位置、y = 希望年収）
  const H3 = 80;
  const first = new Date(`${weekly[0].week}T00:00:00`).getTime();
  const span = n * 7 * 86400000;
  const dots = salary.filter((s) => s.desired_man != null && s.replied_at >= weekly[0].week);
  const yLo = 750; const yHi = 1050;
  const y3 = (v: number) => 6 + H3 * (1 - (Math.min(Math.max(v, yLo), yHi) - yLo) / (yHi - yLo));
  const dx = (iso: string) => PAD_L + ((new Date(`${iso}T00:00:00`).getTime() - first) / span) * PLOT_W;
  // 同じ日・同じ希望額の点は完全に重なって件数が見えなくなるので、横へ 9px ずつ広げる
  const dupIndex = new Map<string, number>();
  const dupTotal = new Map<string, number>();
  dots.forEach((d) => { const k = `${d.replied_at}|${d.desired_man}`; dupTotal.set(k, (dupTotal.get(k) ?? 0) + 1); });
  const dotX = (d: SalaryAskItem): number => {
    const k = `${d.replied_at}|${d.desired_man}`;
    const idx = dupIndex.get(k) ?? 0;
    dupIndex.set(k, idx + 1);
    return dx(d.replied_at) + (idx - ((dupTotal.get(k) ?? 1) - 1) / 2) * 9;
  };

  const outLabel: Record<OutKey, string> = {
    pass: t.time_outcome_pass, fail: t.time_outcome_fail, pending: t.time_outcome_pending, other: t.time_outcome_other,
  };
  const shown = hover ?? n - 1;
  const cur = weekly[shown];
  const weekLabel = (i: number) => t.time_week_of.replace('{date}', md(weekly[i].week));

  return (
    <Card>
      <SectionTitle right={<Legend items={(Object.keys(OUT) as OutKey[]).map((k) => [OUT[k], outLabel[k]])} />}>
        {t.time_weekly_title}
      </SectionTitle>

      {/* 読み取り行：hover した週（無ければ最新週）の全系列。値が先、ラベルが後 */}
      <div style={{ fontSize: 12, color: C.sub, minHeight: 20, fontVariantNumeric: 'tabular-nums' }} aria-live="polite">
        <strong style={{ color: C.ink }}>{weekLabel(shown)}</strong>
        {' · '}{t.time_readout
          .replace('{applied}', String(cur.applied)).replace('{pass}', String(cur.pass))
          .replace('{fail}', String(cur.fail)).replace('{pending}', String(cur.pending))
          .replace('{other}', String(other(cur)))}
        {' · '}{cur.rolling_rate == null || cur.rolling_insufficient
          ? t.time_readout_rate_na.replace('{n}', String(cur.rolling_decided))
          : t.time_readout_rate.replace('{rate}', String(cur.rolling_rate))
            .replace('{lo}', String(cur.rolling_ci_low)).replace('{hi}', String(cur.rolling_ci_high))
            .replace('{n}', String(cur.rolling_decided))}
      </div>

      <div ref={boxRef} onMouseLeave={() => setHover(null)}>
        {W > 0 && (<>
        <ChartLabel>{t.time_weekly_counts}</ChartLabel>
        <svg width={W} height={H1 + 6 + AXIS_H} role="img" aria-label={t.time_weekly_counts}>
          <YGrid w={W} ticks={[0, maxApplied / 2, maxApplied]} y={y1} />
          <WeekBands w={W} n={n} top={0} height={H1 + 6} hover={hover} onHover={setHover} label={weekLabel} />
          {weekly.map((w, i) => {
            const segs: [OutKey, number][] = [['pass', w.pass], ['fail', w.fail], ['pending', w.pending], ['other', other(w)]];
            const visible = segs.filter(([, v]) => v > 0);
            let base = 0;
            return (
              <g key={w.week} pointerEvents="none">
                {visible.map(([k, v], si) => {
                  const yTop = y1(base + v);
                  const h = y1(base) - yTop - (si > 0 ? 2 : 0); // 2px の地色の隙間で区切る（枠線は描かない）
                  base += v;
                  const x = cx(i) - colW / 2;
                  return si === visible.length - 1
                    ? <path key={k} d={colPath(x, yTop, colW, Math.max(0, h))} fill={OUT[k]} />
                    : <rect key={k} x={x} y={yTop} width={colW} height={Math.max(0, h)} fill={OUT[k]} />;
                })}
              </g>
            );
          })}
          <XAxis w={W} weeks={weekly.map((w) => w.week)} top={H1 + 6} />
        </svg>

        <ChartLabel>{t.time_weekly_rate}</ChartLabel>
        <svg width={W} height={H2 + 6 + AXIS_H} role="img" aria-label={t.time_weekly_rate}>
          <YGrid w={W} ticks={[0, maxRate / 2, maxRate]} y={y2} fmt={(v) => `${v}%`} />
          <WeekBands w={W} n={n} top={0} height={H2 + 6} hover={hover} onHover={setHover} label={weekLabel} />
          {bandPath && <path d={bandPath} fill={alpha(OUT.pass, 0.1)} pointerEvents="none" />}
          {baseline != null && (
            <g pointerEvents="none">
              <line x1={PAD_L} x2={W - PAD_R} y1={y2(baseline)} y2={y2(baseline)} stroke={alpha(C.ink, 0.45)} strokeWidth={1} />
              {/* 右端は最新点と重なるので左端に置く */}
              <text x={PAD_L + 4} y={y2(baseline) + 12} textAnchor="start" fontSize={11} fill={C.sub}>
                {t.time_baseline.replace('{rate}', String(baseline))}
              </text>
            </g>
          )}
          <path d={linePath} fill="none" stroke={OUT.pass} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" pointerEvents="none" />
          {linePts.map(({ i, w }) => (
            // 空心＝直近 4 週の結果が 10 件未満（参考値）
            <circle key={w.week} cx={cx(i)} cy={y2(w.rolling_rate!)} r={4} pointerEvents="none"
              fill={w.rolling_insufficient ? C.card : OUT.pass} stroke={w.rolling_insufficient ? OUT.pass : C.card}
              strokeWidth={2} />
          ))}
          <XAxis w={W} weeks={weekly.map((w) => w.week)} top={H2 + 6} />
        </svg>
        <div style={{ fontSize: 11, color: C.sub }}>{t.time_insufficient_note}</div>

        {dots.length > 0 && (
          <>
            <ChartLabel>{t.time_weekly_salary}</ChartLabel>
            <svg width={W} height={H3 + 6 + AXIS_H} role="img" aria-label={t.time_weekly_salary}>
              <YGrid w={W} ticks={[800, 900, 1000]} y={y3} />
              <WeekBands w={W} n={n} top={0} height={H3 + 6} hover={hover} onHover={setHover} label={weekLabel} />
              {dots.map((s) => {
                const k = outcomeKey(s.outcome);
                const x = dotX(s);
                return (
                  <g key={`${s.job_id}-${s.replied_at}`}>
                    <circle cx={x} cy={y3(s.desired_man!)} r={5} fill={OUT[k]}
                      stroke={k === 'other' ? C.sub : C.card} strokeWidth={k === 'other' ? 1 : 2} pointerEvents="none" />
                    {/* 当たり判定は 24px 以上（点そのものは 10px） */}
                    <circle cx={x} cy={y3(s.desired_man!)} r={12} fill="transparent">
                      <title>{`${s.replied_at} ${s.company}\n${t.app_salary_desired_label.replace('{n}', String(s.desired_man))}`
                        + `${s.minimum_man != null ? ` / ${t.app_salary_minimum_label.replace('{n}', String(s.minimum_man))}` : ''}`
                        + ` · ${outLabel[k]}`}</title>
                    </circle>
                  </g>
                );
              })}
              <XAxis w={W} weeks={weekly.map((w) => w.week)} top={H3 + 6} />
            </svg>
          </>
        )}
        </>)}
      </div>

      {/* 表ビュー：ツールチップだけで値を読ませない */}
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.ink }}>{t.time_table}</summary>
        <table style={{ width: '100%', marginTop: 8, fontSize: 12, borderCollapse: 'collapse', fontVariantNumeric: 'tabular-nums' }}>
          <thead>
            <tr style={{ color: C.sub, textAlign: 'right' }}>
              <th style={{ textAlign: 'left', fontWeight: 600 }}>{t.time_col_week}</th>
              <th style={{ fontWeight: 600 }}>{t.time_col_applied}</th>
              {(Object.keys(OUT) as OutKey[]).map((k) => <th key={k} style={{ fontWeight: 600 }}>{outLabel[k]}</th>)}
              <th style={{ fontWeight: 600 }}>{t.time_col_rate}</th>
            </tr>
          </thead>
          <tbody>
            {weekly.map((w) => (
              <tr key={w.week} style={{ textAlign: 'right', borderTop: `1px solid ${alpha(C.border, 0.6)}` }}>
                <td style={{ textAlign: 'left', padding: '3px 0' }}>{md(w.week)}</td>
                <td>{w.applied}</td><td>{w.pass}</td><td>{w.fail}</td><td>{w.pending}</td><td>{other(w)}</td>
                <td>{w.rolling_rate == null ? '—' : `${w.rolling_rate}% (${w.rolling_decided})`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </Card>
  );
};

// ───────────────────────── 待ち日数 × 通過率 ─────────────────────────

export const WaitCurveCard: React.FC<{ curve: WaitCurve }> = ({ curve }) => {
  const t = useT();
  const pts = curve.points.filter((p) => p.decided > 0);
  if (pts.length === 0) return <Card><SectionTitle>{t.time_wait_title}</SectionTitle><Empty>{t.app_timeline_no_data}</Empty></Card>;
  const baseline = pts[0].rate; // 0 日＝結果が出た全件の通過率
  const scaleRows = pts.filter((p) => !p.insufficient);
  const scaleMax = Math.min(100, Math.max(20, ...(scaleRows.length ? scaleRows : pts).map((p) => p.ci_high ?? 0)) * 1.05);
  const waiting = curve.points.filter((p) => p.day > 0 && p.waiting_now > 0);
  return (
    <Card>
      <SectionTitle>{t.time_wait_title}</SectionTitle>
      <div style={{ fontSize: 12, color: C.sub, marginBottom: 10, lineHeight: 1.6 }}>{t.time_wait_note}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        {pts.map((p) => (
          <RateRow key={p.day} label={p.day === 0 ? t.time_wait_all : t.time_wait_label.replace('{n}', String(p.day))}
            item={p} baseline={baseline} scaleMax={scaleMax} insufficientText={t.app_stats_insufficient} />
        ))}
      </div>
      <div style={{ fontSize: 12, color: C.sub, marginTop: 12, lineHeight: 1.7 }}>
        {t.time_wait_medians
          .replace('{pass}', String(curve.median_pass_days ?? '—'))
          .replace('{fail}', String(curve.median_fail_days ?? '—'))}
        {waiting.length > 0 && (
          <div>{t.time_wait_now.replace('{list}', waiting
            .map((p) => t.time_wait_now_item.replace('{n}', String(p.day)).replace('{c}', String(p.waiting_now)))
            .join(' · '))}</div>
        )}
      </div>
    </Card>
  );
};

// ───────────────────────── 面接密度 ─────────────────────────

export const InterviewLoadCard: React.FC<{ load: InterviewLoad }> = ({ load }) => {
  const t = useT();
  const [hover, setHover] = useState<number | null>(null);
  const [boxRef, W] = useWidth();
  const weeks = load.weeks;
  if (weeks.length === 0) return <Card><SectionTitle>{t.time_interview_title}</SectionTitle><Empty>{t.app_timeline_no_data}</Empty></Card>;

  const n = weeks.length;
  const band = plotW(W) / n;
  const colW = Math.min(24, band * 0.62);
  const H = 110;
  const busy = load.busy_threshold ?? 3;
  const maxN = Math.max(busy + 1, load.max);
  const y = (v: number) => 6 + H * (1 - v / maxN);
  const ticks = Array.from(new Set([0, Math.round(maxN / 2), maxN]));
  // 単一系列なので色は 1 つ。過去/今週/予定は同じ色の濃淡で分ける（凡例で説明）
  const fillFor = (when: string) => (when === 'future' ? alpha(C.amber, 0.35) : C.amber);
  const whenLabel: Record<string, string> = {
    past: t.time_interview_past, current: t.time_interview_current, future: t.time_interview_future,
  };
  const shown = hover ?? weeks.findIndex((w) => w.when === 'current');
  const cur = weeks[shown >= 0 ? shown : n - 1];

  return (
    <Card>
      <SectionTitle right={<Legend items={[[C.amber, t.time_interview_past], [alpha(C.amber, 0.35), t.time_interview_future]]}
        line={[alpha(C.errorRed, 0.55), t.time_interview_busy_line.replace('{n}', String(busy))]} />}>
        {t.time_interview_title}
      </SectionTitle>
      <div style={{ fontSize: 12, color: C.sub, minHeight: 20 }} aria-live="polite">
        <strong style={{ color: C.ink }}>{t.time_week_of.replace('{date}', md(cur.week))}</strong>
        {' · '}{t.time_interview_readout.replace('{n}', String(cur.n))}{` (${whenLabel[cur.when]})`}
      </div>
      <div ref={boxRef}>
      {W > 0 && (
      <svg width={W} height={H + 6 + AXIS_H} role="img" aria-label={t.time_interview_title}
        onMouseLeave={() => setHover(null)}>
        <YGrid w={W} ticks={ticks} y={y} />
        <line x1={PAD_L} x2={W - PAD_R} y1={y(busy)} y2={y(busy)} stroke={alpha(C.errorRed, 0.55)} strokeWidth={1} />
        <WeekBands w={W} n={n} top={0} height={H + 6} hover={hover} onHover={setHover}
          label={(i) => `${t.time_week_of.replace('{date}', md(weeks[i].week))} ${weeks[i].n}`} />
        {weeks.map((w, i) => (w.n > 0 ? (
          <path key={w.week} d={colPath(PAD_L + band * (i + 0.5) - colW / 2, y(w.n), colW, y(0) - y(w.n))}
            fill={fillFor(w.when)} pointerEvents="none" />
        ) : null))}
        <XAxis w={W} weeks={weeks.map((w) => w.week)} top={H + 6} />
      </svg>
      )}
      </div>
      <div style={{ fontSize: 11, color: C.sub, marginTop: 6, lineHeight: 1.6 }}>
        {t.time_interview_busy.replace('{n}', String(busy)).replace('{w}', String(load.busy_weeks))}
        {' · '}{t.time_interview_note}
      </div>
    </Card>
  );
};
