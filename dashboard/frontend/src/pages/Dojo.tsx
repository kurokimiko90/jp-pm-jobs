import React, { useEffect, useRef, useState } from 'react';
import { Cards, Timer, Target, ArrowRight } from '@phosphor-icons/react';
import './dojo.css';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { get, post } from '../api';
import { C, alpha } from '../theme';
import { Card, Pill, Badge, SectionTitle, Empty, PageHeader, FilterChip } from '../components/ui';
import { useT } from '../i18n';

type CardT = { id: number; company: string; qno: string; question: string; answer: string; last_grade: string | null };
type Mode = 'flash' | 'mock' | 'weak';

const getGradeMeta = (t: ReturnType<typeof useT>) => ({
  o: { label: t.dojo_fluent, color: C.successGreen },
  d: { label: t.dojo_hesitate, color: '#C79A1B' },
  x: { label: t.dojo_fail, color: C.sub }, // 刻意不用紅：答不出是發現，不是懲罰
});

const MOCK_SEC = 120;

/** 本機錄音：blob URL 回放，不上傳任何伺服器 */
const Recorder: React.FC<{ resetKey: number }> = ({ resetKey }) => {
  const t = useT();
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => () => { rec?.stream.getTracks().forEach((track) => track.stop()); }, [rec]);
  useEffect(() => { setUrl(null); }, [resetKey]);

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      r.ondataavailable = (e) => chunks.push(e.data);
      r.onstop = () => {
        setUrl(URL.createObjectURL(new Blob(chunks, { type: 'audio/webm' })));
        stream.getTracks().forEach((track) => track.stop());
      };
      r.start();
      setRec(r);
    } catch {
      alert(t.dojo_no_mic);
    }
  };
  const stop = () => { rec?.stop(); setRec(null); };

  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'center' }}>
      {rec
        ? <button onClick={stop} style={{
            borderRadius: 999, border: `2px solid ${C.ink}`, background: C.card,
            padding: '6px 18px', fontWeight: 700, color: C.ink }}>{t.dojo_stop_record}</button>
        : <button onClick={start} style={{
            borderRadius: 999, border: `1px solid ${C.border}`, background: C.card,
            padding: '6px 18px', fontSize: 13, color: '#7A7168' }}>{t.dojo_record}</button>}
      {url && <audio controls src={url} style={{ height: 32 }} />}
    </div>
  );
};

/** 練習進行畫面：全屏覆蓋，隱藏一切干擾 */
const Practice: React.FC<{
  cards: CardT[]; mode: Mode; onExit: (results: { card: CardT; grade: string }[]) => void;
}> = ({ cards, mode, onExit }) => {
  const t = useT();
  const gradeMeta = getGradeMeta(t);
  const [idx, setIdx] = useState(0);
  const [reveal, setReveal] = useState(false);
  const [results, setResults] = useState<{ card: CardT; grade: string }[]>([]);
  const [remain, setRemain] = useState(MOCK_SEC);
  const startRef = useRef(Date.now());

  const cur = cards[idx];

  useEffect(() => {
    setReveal(false); setRemain(MOCK_SEC); startRef.current = Date.now();
  }, [idx]);

  useEffect(() => {
    if (mode !== 'mock' || reveal) return;
    const t = setInterval(() => setRemain((r) => Math.max(0, r - 1)), 1000);
    return () => clearInterval(t);
  }, [mode, reveal, idx]);

  const grade = async (g: 'o' | 'd' | 'x') => {
    const dur = Math.round((Date.now() - startRef.current) / 1000);
    post('/api/dojo/review', { card_id: cur.id, grade: g, mode, duration_sec: dur });
    const next = [...results, { card: cur, grade: g }];
    if (idx + 1 < cards.length) { setResults(next); setIdx(idx + 1); }
    else onExit(next);
  };

  const timeColor = remain <= 20 ? '#C79A1B' : C.sub;

  return (
    <div
      className="practice-overlay"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        background: '#FFF8F0',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: 32,
      }}
    >
      <div
        className="practice-header"
        style={{
          width: '100%',
          maxWidth: 760,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 32,
        }}
      >
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: 32,
              padding: '0 12px',
              borderRadius: 6,
              background: C.progressIndigo,
              color: 'white',
              fontSize: 12,
              fontWeight: 700,
            }}
          >
            {cur.company} ・ {cur.qno}
          </span>
          <span
            style={{
              fontSize: 13,
              color: 'rgba(90,82,72,0.55)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {idx + 1} / {cards.length}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          {mode === 'mock' && !reveal && (
            <span
              style={{
                fontVariantNumeric: 'tabular-nums',
                fontWeight: 700,
                color: timeColor,
              }}
            >
              ⏱ {Math.floor(remain / 60)}:{String(remain % 60).padStart(2, '0')}
            </span>
          )}
          <button
            onClick={() => onExit(results)}
            style={{
              border: 'none',
              background: 'none',
              color: 'rgba(90,82,72,0.55)',
              fontSize: 15,
              cursor: 'pointer',
            }}
          >
            {t.dojo_exit}
          </button>
        </div>
      </div>

      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          width: '100%',
          maxWidth: 760,
          gap: 20,
        }}
      >
        <div className="practice-card" style={{
          background: '#FFF8F0',
          borderRadius: 28,
          boxShadow: 'var(--shadow-card)',
          padding: '34px 38px',
          minHeight: 360,
          display: 'flex',
          flexDirection: 'column',
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 22,
          }}>
            <span style={{
              fontSize: 13,
              fontWeight: 700,
              letterSpacing: '0.08em',
              color: 'rgba(90,82,72,0.5)',
            }}>
              {t.dojo_question_heading} {idx + 1} / {cards.length}
            </span>
            {mode === 'mock' && !reveal && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <svg
                  viewBox="0 0 36 36"
                  style={{ width: 32, height: 32, transform: 'rotate(-90deg)' }}
                >
                  <circle
                    cx="18"
                    cy="18"
                    r="15"
                    fill="none"
                    stroke="rgba(232,213,183,0.55)"
                    strokeWidth="4"
                  />
                  <circle
                    cx="18"
                    cy="18"
                    r="15"
                    fill="none"
                    stroke={C.gold}
                    strokeWidth="4"
                    strokeLinecap="round"
                    strokeDasharray={`${(remain / MOCK_SEC) * 94.25}`}
                  />
                </svg>
                <span
                  style={{
                    fontSize: 15,
                    fontWeight: 700,
                    color: '#5A5248',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {Math.floor(remain / 60)}:{String(remain % 60).padStart(2, '0')}
                </span>
              </div>
            )}
          </div>

          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
          }}>
            <div style={{
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: '0.04em',
              lineHeight: 1.65,
              textWrap: 'pretty',
            } as React.CSSProperties}>
              「{cur.question}」
            </div>
            {reveal && (
              <div
                style={{
                  marginTop: 22,
                  paddingTop: 22,
                  borderTop: '1.5px dashed rgba(232,213,183,0.8)',
                  animation: 'panelIn 240ms var(--ease-out) both',
                }}
              >
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 700,
                    letterSpacing: '0.08em',
                    color: 'rgba(90,82,72,0.5)',
                    marginBottom: 10,
                  }}
                >
                  {t.dojo_answer_heading}
                </div>
                <div
                  className="md-body"
                  style={{
                    fontSize: 15,
                    fontWeight: 500,
                    lineHeight: 1.85,
                    letterSpacing: '0.02em',
                    color: 'rgba(90,82,72,0.9)',
                  }}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {cur.answer || t.dojo_no_answer}
                  </ReactMarkdown>
                </div>
              </div>
            )}
          </div>

          <div
            style={{
              marginTop: 26,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 16,
              flexWrap: 'wrap',
            }}
          >
            <button
              onClick={() => setReveal(!reveal)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 9,
                height: 44,
                padding: '0 20px',
                borderRadius: 999,
                background: 'transparent',
                border: `1.5px solid rgba(232,213,183,0.95)`,
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.04em',
                cursor: 'pointer',
                color: '#5A5248',
              }}
            >
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: 999,
                  background: '#F2B5C0',
                }}
              />
              {reveal ? t.dojo_hide_answer : t.dojo_show_answer}
            </button>
            {reveal && (
              <div style={{ display: 'flex', gap: 12 }}>
                {(Object.keys(gradeMeta) as ('o' | 'd' | 'x')[]).map((g) => (
                  <button
                    key={g}
                    onClick={() => grade(g)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      height: 46,
                      padding: '0 24px',
                      borderRadius: 999,
                      background:
                        g === 'o'
                          ? C.gold
                          : g === 'd'
                            ? '#E8D5B7'
                            : 'rgba(242,181,192,0.45)',
                      border: 'none',
                      fontSize: 15,
                      fontWeight: 700,
                      letterSpacing: '0.04em',
                      cursor: 'pointer',
                      color: '#5A5248',
                    }}
                  >
                    {g === 'o' ? '○' : g === 'd' ? '△' : '×'} {gradeMeta[g].label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

const Summary: React.FC<{ results: { card: CardT; grade: string }[]; onClose: () => void }> = ({ results, onClose }) => {
  const t = useT();
  const gradeMeta = getGradeMeta(t);
  const weak = results.filter((r) => r.grade !== 'o');
  // 練完是動機最高點：橋接到應募（momentum chaining）
  const [bridge, setBridge] = useState<any>(null);
  useEffect(() => {
    get('/api/actions').then((a) => setBridge(a.prepped_not_applied[0] || null));
  }, []);
  return (
    <div style={{
      background: '#FFF8F0',
      borderRadius: 26,
      boxShadow: 'var(--shadow-card)',
      padding: '24px 28px',
    }}>
      <div style={{
        fontSize: 13,
        fontWeight: 700,
        letterSpacing: '0.08em',
        color: 'rgba(90,82,72,0.55)',
        marginBottom: 16,
      }}>
        {t.dojo_review}{results.length} {t.dojo_questions}）
      </div>
      {weak.length === 0 ? (
        <div style={{ fontSize: 14, color: C.successGreen, fontWeight: 700 }}>
          {t.dojo_perfect}
        </div>
      ) : (
        <>
          <div style={{ fontSize: 13, marginBottom: 12, color: 'rgba(90,82,72,0.7)' }}>
            {t.dojo_practice_worth} {weak.length} {t.dojo_practice_again}
          </div>
          {weak.map((r) => (
            <div
              key={r.card.id}
              style={{
                fontSize: 13,
                padding: '6px 0',
                borderBottom: `1px solid rgba(232,213,183,0.3)`,
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  height: 24,
                  padding: '0 8px',
                  borderRadius: 4,
                  background: gradeMeta[r.grade as 'd' | 'x'].color,
                  color: 'white',
                  fontSize: 11,
                  fontWeight: 700,
                  marginRight: 8,
                }}
              >
                {r.grade === 'd' ? '△' : '×'}
              </span>
              {r.card.company} {r.card.qno} — {r.card.question}
            </div>
          ))}
        </>
      )}
      {bridge && (
        <div
          style={{
            marginTop: 16,
            background: `${alpha(C.gold, 0.13)}`,
            borderRadius: 14,
            padding: 16,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          <span style={{ fontSize: 13, flex: 1, color: C.ink }}>
            {t.dojo_timing}
            <b>{bridge.company || bridge.title}</b> {t.dojo_bridge_prompt}
          </span>
          <button
            onClick={() => window.open(bridge.url, '_blank')}
            style={{
              borderRadius: 999,
              border: 'none',
              background: C.gold,
              padding: '8px 22px',
              fontWeight: 700,
              color: C.ink,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            {t.dojo_apply_now}
          </button>
        </div>
      )}
      <div style={{ marginTop: 16 }}>
        <button
          onClick={onClose}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            height: 40,
            padding: '0 20px',
            borderRadius: 22,
            background: C.gold,
            border: 'none',
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: '0.04em',
            cursor: 'pointer',
            color: C.ink,
          }}
        >
          {t.dojo_back}
        </button>
      </div>
    </div>
  );
};

export const Dojo: React.FC = () => {
  const t = useT();
  const [stats, setStats] = useState<any>(null);
  const [companies, setCompanies] = useState<any[]>([]);
  const [company, setCompany] = useState('');
  const [practice, setPractice] = useState<{ cards: CardT[]; mode: Mode } | null>(null);
  const [summary, setSummary] = useState<{ card: CardT; grade: string }[] | null>(null);

  const reload = () => {
    get('/api/dojo/stats').then(setStats);
    get('/api/dojo/cards').then((d) => setCompanies(d.companies));
  };
  useEffect(reload, []);

  const start = async (mode: Mode) => {
    try {
      const p = new URLSearchParams({ mode, company, n: mode === 'mock' ? '8' : '8' });
      const s = await get(`/api/dojo/session?${p}`);
      if (!s.cards.length) { alert(s.note || t.dojo_no_cards); return; }
      setSummary(null);
      setPractice({ cards: s.cards, mode });
    } catch {
      alert(t.dojo_no_cards);
    }
  };

  if (!stats) return <Empty>{t.tailored_loading}</Empty>;

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 20,
      animation: 'riseIn 240ms var(--ease-out) both',
    }}>
      {practice && (
        <Practice
          cards={practice.cards}
          mode={practice.mode}
          onExit={(r) => {
            setPractice(null);
            setSummary(r.length ? r : null);
            reload();
          }}
        />
      )}

      <PageHeader
        title={t.dojo_title}
        subtitle={<>{t.dojo_session_prompt} · {t.date_today} <b className="num-display" style={{ color: C.ink }}>{stats.today}</b> {t.dojo_questions}</>}
      />

      {/* 練習の手ごたえ：○△× の累計と題庫に対する到達度 */}
      {(() => {
        const o = stats.total.o ?? 0, d = stats.total.d ?? 0, x = stats.total.x ?? 0;
        const done = o + d + x;
        const bank = companies.reduce((a: number, c: any) => a + (c.n || 0), 0);
        // 分母は題庫全体：回答済み比だと 1 問答えただけで満タンに見える
        const pct = (n: number) => (bank > 0 ? (n / bank) * 100 : 0);
        return (
          <section className="dj-progress" aria-label={t.dojo_title}>
            <div className="dj-progress-main">
              <span className="num-display dj-progress-n">{done}</span>
              <span className="dj-progress-of">/ {bank}</span>
            </div>
            <div className="dj-progress-bar" role="img" aria-label={`○${o} △${d} ×${x}`}>
              <span style={{ width: `${pct(o)}%`, background: C.successGreen }} />
              <span style={{ width: `${pct(d)}%`, background: C.gold }} />
              <span style={{ width: `${pct(x)}%`, background: C.errorRed }} />
            </div>
            <div className="dj-progress-legend">
              <span><i style={{ background: C.successGreen }} />○ <b className="num-display">{o}</b></span>
              <span><i style={{ background: C.gold }} />△ <b className="num-display">{d}</b></span>
              <span><i style={{ background: C.errorRed }} />× <b className="num-display">{x}</b></span>
            </div>
          </section>
        );
      })()}

      <div className="dj-companies">
        <FilterChip active={company === ''} onClick={() => setCompany('')}>{t.dojo_all_companies}</FilterChip>
        {companies.map((c: any) => (
          <FilterChip key={c.company} active={company === c.company} onClick={() => setCompany(c.company)}>
            {c.company}<span className="dj-chip-n">{c.n}</span>
          </FilterChip>
        ))}
      </div>

      <div className="dj-modes">
        {([
          ['flash', t.dojo_flash, t.dojo_flash_desc, Cards],
          ['mock', t.dojo_mock, t.dojo_mock_desc, Timer],
          ['weak', t.dojo_weak, `${t.dojo_weak_desc_normal}（${stats.weak.length}）`, Target],
        ] as [Mode, string, string, React.ElementType][]).map(([m, title, desc, Icon], i) => {
          const disabled = m === 'weak' && stats.weak.length === 0;
          return (
            <button
              key={m}
              type="button"
              className={`dj-mode${i === 0 ? ' dj-mode--primary' : ''}`}
              onClick={() => !disabled && start(m)}
              disabled={disabled}
            >
              <span className="dj-mode-icon"><Icon size={22} weight="duotone" /></span>
              <span className="dj-mode-title">{title}</span>
              <span className="dj-mode-desc">{disabled ? t.dojo_weak_desc_empty : desc}</span>
              {!disabled && <span className="dj-mode-go"><ArrowRight size={16} weight="bold" /></span>}
            </button>
          );
        })}
      </div>

      {summary && <Summary results={summary} onClose={() => setSummary(null)} />}

      {stats.weak.length > 0 && (
        <div style={{
          background: '#FFF8F0',
          borderRadius: 26,
          boxShadow: 'var(--shadow-card)',
          padding: '24px 28px',
        }}>
          <div style={{
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: '0.08em',
            color: 'rgba(90,82,72,0.55)',
            marginBottom: 16,
          }}>
            {t.dojo_weak_title}
          </div>
          {stats.weak.map((w: any) => (
            <div
              key={w.id}
              style={{
                fontSize: 13,
                padding: '6px 0',
                borderBottom: `1px solid rgba(232,213,183,0.3)`,
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  height: 28,
                  padding: '0 10px',
                  borderRadius: 6,
                  background: C.progressIndigo,
                  color: 'white',
                  fontSize: 11,
                  fontWeight: 700,
                  marginRight: 8,
                }}
              >
                {w.company}
              </span>
              {w.qno} — {w.question}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
