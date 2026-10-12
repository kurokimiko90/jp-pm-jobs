import React, { useEffect, useRef } from 'react';
import { C, alpha } from '../theme';
import { useLang, useT, type Lang } from '../i18n';

export interface DailyCount { date: string; count: number }

/**
 * 日次件数の 5 段階（1 色相・明→暗の順序尺度）。件数は 1〜28 件と裾が長く、
 * 1/2/3-4/5+ の等幅区切りだと 7 件の日も 28 件の日も同じ最暗色に潰れる。
 * 暗い 2 段は白文字（コントラスト 3.3 / 4.6）、明るい 3 段は ink 文字。
 */
const BINS: { min: number; max: number; fill: string; text: string }[] = [
  { min: 1, max: 2, fill: `color-mix(in srgb, ${C.gold} 25%, #fff)`, text: C.ink },
  { min: 3, max: 5, fill: `color-mix(in srgb, ${C.gold} 55%, #fff)`, text: C.ink },
  { min: 6, max: 10, fill: C.gold, text: C.ink },
  { min: 11, max: 19, fill: `color-mix(in srgb, ${C.amber} 75%, ${C.ink})`, text: '#fff' },
  { min: 20, max: Infinity, fill: `color-mix(in srgb, ${C.amber} 45%, ${C.ink})`, text: '#fff' },
];
const binOf = (n: number) => BINS.find((b) => n >= b.min && n <= b.max);

const LOCALE: Record<Lang, string> = { zh: 'zh-Hant', ja: 'ja', en: 'en' };
const CELL_MIN = 18;
const CELL_MAX = 40;

const iso = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** 最初の応募週の月曜〜今週の日曜まで、月曜始まりの週（app_time_stats の週次と揃える）。 */
function buildWeeks(dailyCounts: DailyCount[], today: Date): Date[][] {
  const first = dailyCounts.reduce<string | null>((m, d) => (!m || d.date < m ? d.date : m), null);
  const start = new Date(`${first ?? iso(today)}T00:00:00`);
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  const weeks: Date[][] = [];
  for (const cur = new Date(start); cur <= today;) {
    weeks.push(Array.from({ length: 7 }, (_, i) => {
      const d = new Date(cur);
      d.setDate(cur.getDate() + i);
      return d;
    }));
    cur.setDate(cur.getDate() + 7);
  }
  return weeks;
}

/** 応募カレンダー：列＝週、行＝曜日。件数はセル内に直接表示（色だけに頼らない）。 */
export const ApplyCalendar: React.FC<{ dailyCounts: DailyCount[] }> = ({ dailyCounts }) => {
  const t = useT();
  const { lang } = useLang();
  const scroller = useRef<HTMLDivElement>(null);
  const today = new Date(`${iso(new Date())}T00:00:00`);
  const todayIso = iso(today);
  const weeks = buildWeeks(dailyCounts, today);
  const count: Record<string, number> = Object.fromEntries(dailyCounts.map((d) => [d.date, d.count]));
  const fmtMonth = new Intl.DateTimeFormat(LOCALE[lang], { month: 'short' });
  const fmtWeekday = new Intl.DateTimeFormat(LOCALE[lang], { weekday: 'narrow' });
  const fmtDay = new Intl.DateTimeFormat(LOCALE[lang], { month: 'numeric', day: 'numeric', weekday: 'short' });

  const total = dailyCounts.reduce((s, d) => s + d.count, 0);
  const peak = dailyCounts.reduce<DailyCount | null>((m, d) => (!m || d.count > m.count ? d : m), null);

  // 週が増えて横に溢れたら、最新の週（右端）を見せる
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [weeks.length]);

  return (
    <div>
      <div style={{ fontSize: 12, color: C.sub, marginBottom: 12, fontVariantNumeric: 'tabular-nums' }}>
        {t.app_cal_summary
          .replace('{total}', String(total)).replace('{days}', String(dailyCounts.length))
          .replace('{max}', String(peak?.count ?? 0))
          .replace('{date}', peak ? fmtDay.format(new Date(`${peak.date}T00:00:00`)) : '—')}
      </div>
      <div ref={scroller} style={{ overflowX: 'auto', paddingBottom: 4 }}>
        <div style={{
          display: 'grid', gap: 4, width: '100%', minWidth: 'min-content',
          gridTemplateColumns: `auto repeat(${weeks.length}, minmax(${CELL_MIN}px, ${CELL_MAX}px))`,
        }}>
          {/* 1 行目：月ラベル（月が変わる週だけ） */}
          <span />
          {weeks.map((week, i) => {
            const newMonth = i === 0 || week.some((d) => d.getDate() === 1);
            const labelDay = week.find((d) => d.getDate() === 1) ?? week[0];
            return (
              <span key={`m-${iso(week[0])}`} style={{ fontSize: 11, color: C.sub, whiteSpace: 'nowrap', height: 16 }}>
                {newMonth ? fmtMonth.format(labelDay) : ''}
              </span>
            );
          })}
          {/* 2〜8 行目：曜日ラベル＋日セル */}
          {Array.from({ length: 7 }, (_, row) => (
            <React.Fragment key={`r-${row}`}>
              <span style={{
                fontSize: 11, color: C.sub, alignSelf: 'center', paddingRight: 4,
              }}>{fmtWeekday.format(weeks[0]?.[row] ?? today)}</span>
              {weeks.map((week) => {
                const d = week[row];
                const key = iso(d);
                if (key > todayIso) return <span key={key} />;
                const n = count[key] ?? 0;
                const bin = binOf(n);
                return (
                  <div key={key} title={t.app_cal_cell.replace('{date}', fmtDay.format(d)).replace('{n}', String(n))}
                    style={{
                      aspectRatio: '1 / 1', borderRadius: 4, display: 'grid', placeItems: 'center',
                      background: bin?.fill ?? alpha(C.border, 0.45),
                      boxShadow: key === todayIso ? `inset 0 0 0 2px ${C.ink}` : undefined,
                      fontSize: 11, fontWeight: 700, color: bin?.text ?? C.sub,
                      fontVariantNumeric: 'tabular-nums', cursor: 'default',
                    }}>
                    {n > 0 ? n : ''}
                  </div>
                );
              })}
            </React.Fragment>
          ))}
        </div>
      </div>
      {/* 凡例：色の段と件数の範囲を必ず併記 */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        justifyContent: 'flex-end', marginTop: 12, fontSize: 11, color: C.sub,
      }}>
        <span>{t.app_cal_legend_less}</span>
        {BINS.map((b) => (
          <span key={b.min} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 14, height: 14, borderRadius: 3, background: b.fill }} />
            {b.max === Infinity ? `${b.min}+` : `${b.min}–${b.max}`}
          </span>
        ))}
        <span>{t.app_cal_legend_more}</span>
      </div>
    </div>
  );
};
