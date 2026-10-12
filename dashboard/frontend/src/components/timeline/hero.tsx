import React from 'react';
import { useT } from '../../i18n';
import type { TimelineData } from './types';

const fmtYmd = (iso: string): string => iso.split('-').join('/');

interface Kpi { label: string; value: React.ReactNode; sub?: string; tone?: 'accent' | 'good' | 'progress' }

/**
 * ページ冒頭。数字は 5 つだけ、カードにせず髪線で区切って紙面に直接置く
 * （統計卡は純展示・クリック不可の方針はそのまま）。
 * 通過率は必ず CI と n を併記（点推定だけだと過信する）。
 */
export const TimelineHero: React.FC<{ data: TimelineData }> = ({ data }) => {
  const t = useT();
  const { summary } = data;
  const dates = data.daily_counts.filter((d) => d.count > 0).map((d) => d.date).sort();
  const range = dates.length > 0
    ? t.tl_hero_range
      .replace('{from}', fmtYmd(dates[0]))
      .replace('{to}', fmtYmd(dates[dates.length - 1]))
      .replace('{days}', String(dates.length))
    : null;

  const kpis: Kpi[] = [
    { label: t.app_stats_kpi_total, value: summary.total, sub: `${t.app_stats_kpi_pending}${summary.pending}` },
    {
      label: t.app_stats_kpi_pass_rate, tone: 'accent',
      value: summary.pass_rate != null ? <>{summary.pass_rate}<small>%</small></> : '—',
      sub: t.tl_kpi_pass_sub
        .replace('{lo}', String(summary.ci_low)).replace('{hi}', String(summary.ci_high))
        .replace('{n}', String(summary.decided)),
    },
    { label: t.app_stats_kpi_interview, value: summary.reached_interview, sub: `${summary.passed}/${summary.decided}` },
    { label: t.app_stats_kpi_in_progress, value: summary.in_progress, tone: 'progress' },
    {
      label: t.app_stats_kpi_offer, value: summary.offers, tone: 'good',
      sub: summary.withdrawn_after_pass > 0
        ? t.app_stats_kpi_withdrawn_sub.replace('{n}', String(summary.withdrawn_after_pass))
        : undefined,
    },
  ];

  return (
    <header className="tl-hero">
      <div className="tl-hero-head">
        <span className="tl-eyebrow">{t.tl_hero_eyebrow}</span>
        <h1 className="tl-hero-title">{t.tl_hero_title}</h1>
        {range && <p className="tl-hero-range">{range}</p>}
      </div>
      <dl className="tl-kpis">
        {kpis.map((k) => (
          <div key={k.label} className={`tl-kpi${k.tone ? ` tl-kpi--${k.tone}` : ''}`}>
            <dt>{k.label}</dt>
            <dd className="tl-kpi-value num-display">{k.value}</dd>
            {k.sub && <dd className="tl-kpi-sub">{k.sub}</dd>}
          </div>
        ))}
      </dl>
    </header>
  );
};
