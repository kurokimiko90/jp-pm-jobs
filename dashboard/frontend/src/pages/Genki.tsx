import React, { useEffect, useState } from 'react';
import { get, post } from '../api';
import { C } from '../theme';
import { Empty, PageHeader, useIsNarrow } from '../components/ui';
import { useT } from '../i18n';

const LEVELS = [
  { n: 1, key: 'genki_level_1' },
  { n: 2, key: 'genki_level_2' },
  { n: 3, key: 'genki_level_3' },
  { n: 4, key: 'genki_level_4' },
  { n: 5, key: 'genki_level_5' },
];

const WALL_KEYS = [
  { key: 'scraped', labelKey: 'genki_wall_scraped_alt' },
  { key: 'scored', labelKey: 'genki_wall_scored' },
  { key: 'gap', labelKey: 'genki_wall_gap' },
  { key: 'prep', labelKey: 'genki_wall_prep' },
  { key: 'practiced', labelKey: 'genki_wall_practiced' },
  { key: 'applied', labelKey: 'genki_wall_applied' },
];

// 直近 14 日を構成（checkins から level を引く）
const build14 = (checkins: { day: string; level: number }[]) => {
  const byDay = new Map(checkins.map((c) => [c.day, c.level]));
  const out: { label: string; level: number }[] = [];
  const today = new Date();
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const ds = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    out.push({ label: `${d.getMonth() + 1}/${d.getDate()}`, level: byDay.get(ds) ?? 0 });
  }
  return out;
};

const levelColor = (lv: number) => (lv >= 4 ? C.gold : lv === 3 ? C.milkTea : lv > 0 ? C.rosePink : 'rgba(232,213,183,0.4)');

export const Genki: React.FC<{ goDojo?: () => void }> = ({ goDojo }) => {
  const t = useT();
  const isNarrow = useIsNarrow();
  const [data, setData] = useState<any>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const [win, setWin] = useState('');
  const [saved, setSaved] = useState('');

  const reload = () => {
    get('/api/genki/summary').then((d) => {
      setData(d);
      setNote(d.today_checkin?.note || '');
      setSelected(d.today_checkin?.level ?? null);
    });
  };
  useEffect(reload, []);

  if (!data) return <Empty>{t.jobs_loading}</Empty>;
  const catMap: Record<string, string> = {
    low_streak: t.genki_cat_low_streak,
    practiced_today: t.genki_cat_practiced_today,
    applied_recent: t.genki_cat_applied_recent,
    default: t.genki_cat_default,
  };
  const catLine = catMap[data.cat_key || 'default'] || data.cat_line;

  const record = async () => {
    if (selected) await post('/api/genki/checkin', { level: selected, note });
    if (win.trim()) { await post('/api/genki/win', { text: win }); setWin(''); }
    setSaved(t.genki_save_done);
    reload();
    setTimeout(() => setSaved(''), 2400);
  };

  const trend = build14(data.checkins || []);

  return (
    <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <PageHeader title={t.genki_station_title} subtitle={t.genki_station_subtitle} />

      {/* low streak alert */}
      {data.low_streak && (
        <div className="compact-on-phone" style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', background: 'rgba(242,181,192,0.18)', borderRadius: 22, padding: '18px 22px', marginBottom: 18 }}>
          <img src="/jobpilot-logo.svg" alt="" width={30} height={30} style={{ flex: 'none' }} />
          <span style={{ flex: 1, fontSize: 15, fontWeight: 500, letterSpacing: '0.5px' }}>
            {t.genki_low_streak}
          </span>
          {goDojo && (
            <span onClick={goDojo} style={{ display: 'inline-flex', alignItems: 'center', height: 42, padding: '0 20px', borderRadius: 999, background: C.gold, color: C.ink, fontSize: 14, fontWeight: 700, letterSpacing: 1, cursor: 'pointer', whiteSpace: 'nowrap' }}>
              {t.genki_dojo_one}
            </span>
          )}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? '1fr' : '1.3fr 1fr', gap: 18, marginBottom: 18 }}>
        {/* 左：チェックイン */}
        <div className="compact-on-phone" style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '24px 28px' }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 16 }}>{t.genki_today_prompt}</div>
          <div style={{ display: 'flex', gap: 10, marginBottom: 18 }}>
            {LEVELS.map((o) => {
              const on = selected === o.n;
              return (
                <div
                  key={o.n}
                  onClick={() => setSelected(o.n)}
                  style={{
                    flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                    padding: '14px 6px', borderRadius: 16, cursor: 'pointer',
                    background: on ? 'rgba(245,200,66,0.22)' : 'rgba(232,213,183,0.2)',
                    boxShadow: on ? `inset 0 0 0 2px ${C.gold}` : 'none',
                  }}
                >
                  <span style={{ fontSize: 22, fontWeight: 800, color: on ? C.gold : 'rgba(90,82,72,0.5)', fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{o.n}</span>
                  <span style={{ fontSize: 12, fontWeight: 500, letterSpacing: '0.5px', textAlign: 'center', color: C.ink }}>{t[o.key as keyof typeof t]}</span>
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, color: 'rgba(90,82,72,0.5)', margin: '0 0 5px 4px' }}>
            📝 {t.genki_note_label}
          </div>
          <input
            value={note} onChange={(e) => setNote(e.target.value)} placeholder={t.genki_note_placeholder}
            style={{ width: '100%', height: 46, border: 'none', outline: 'none', borderRadius: 14, background: 'rgba(232,213,183,0.25)', padding: '0 18px', fontSize: 14, fontWeight: 500, color: C.ink, marginBottom: 12 }}
          />
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, color: '#B8860B', margin: '0 0 5px 4px' }}>
            ⭐ {t.genki_win_label}
          </div>
          <input
            value={win} onChange={(e) => setWin(e.target.value)} placeholder={t.genki_win_placeholder}
            style={{ width: '100%', height: 46, border: 'none', outline: 'none', borderRadius: 14, background: 'rgba(245,200,66,0.14)', boxShadow: 'inset 0 0 0 1.5px rgba(245,200,66,0.4)', padding: '0 18px', fontSize: 14, fontWeight: 500, color: C.ink, marginBottom: 16 }}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <button
              onClick={record}
              style={{ display: 'inline-flex', alignItems: 'center', height: 46, padding: '0 26px', borderRadius: 999, background: C.gold, color: C.ink, border: 'none', fontSize: 14, fontWeight: 700, letterSpacing: '1.5px', cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0 }}
            >{t.genki_record}</button>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'rgba(90,82,72,0.6)', letterSpacing: '0.5px' }}>
              {saved || catLine}
            </span>
          </div>
        </div>

        {/* 右：トレンド（14日内に記録ゼロなら empty state を出す — 空白＝故障と誤解させない） */}
        <div className="compact-on-phone" style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '24px 28px' }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 18 }}>{t.genki_recent_14}</div>
          {trend.some((d) => d.level > 0) ? (
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 5, height: 120 }}>
              {trend.map((d, i) => (
                <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end', gap: 6, height: '100%' }}>
                  <div style={{
                    width: '100%', borderRadius: '6px 6px 2px 2px',
                    height: `${d.level ? Math.max(d.level / 5 * 92, 6) : 4}px`,
                    background: levelColor(d.level),
                  }} />
                  <span style={{ fontSize: 9, fontWeight: 400, color: 'rgba(90,82,72,0.4)', whiteSpace: 'nowrap' }}>{i % 2 === 0 ? d.label : ''}</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ height: 120, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, textAlign: 'center' }}>
              <span style={{ fontSize: 13, color: 'rgba(90,82,72,0.55)', lineHeight: 1.7, maxWidth: 260 }}>
                {t.genki_trend_empty}
              </span>
              {(data.checkins || []).length > 0 && (
                <span style={{ fontSize: 11, color: 'rgba(90,82,72,0.4)' }}>
                  {t.genki_trend_last.replace('{date}', data.checkins[data.checkins.length - 1].day)}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 努力の壁 */}
      <div className="compact-on-phone" style={{ background: 'rgba(232,213,183,0.2)', borderRadius: 26, padding: '24px 28px' }}>
        <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 16 }}>
          {t.genki_wall_heading}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 12 }}>
          {WALL_KEYS.map((wl) => (
            <div key={wl.key} style={{ background: C.bg, borderRadius: 18, padding: '18px 14px', textAlign: 'center', boxShadow: '0 4px 16px rgba(90,82,72,0.08)' }}>
              <div style={{ fontSize: 26, fontWeight: 800, color: C.gold, fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>
                {data.wall?.[wl.key] ?? 0}
              </div>
              <div style={{ fontSize: 11, letterSpacing: '0.5px', color: 'rgba(90,82,72,0.6)', marginTop: 8, lineHeight: 1.4 }}>{t[wl.labelKey as keyof typeof t]}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
