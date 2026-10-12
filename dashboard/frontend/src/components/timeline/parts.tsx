import React, { useEffect, useRef, useState } from 'react';
import './timeline.css';
import { C, alpha } from '../../theme';
import { useT } from '../../i18n';
import type { Insight, Tone } from './insights';
import type { DotPoint } from './types';

// ───────────────────────── 区（問い → 読み方 → 結論 → カード） ─────────────────────────

export interface SectionMeta { id: string; short: string }

const TONE_COLOR: Record<Tone, string> = { good: C.successGreen, bad: C.errorRed, neutral: C.amber };

/** 結論文：{verdict} だけは翻訳キーを受け取るので、ここで解決する。 */
const useInsightText = () => {
  const t = useT();
  return (it: Insight): string => Object.entries(it.params).reduce<string>((s, [k, v]) => {
    const val = k === 'verdict' ? (t as Record<string, string>)[String(v)] ?? String(v) : String(v);
    return s.split(`{${k}}`).join(val);
  }, t[it.key]);
};

export const InsightList: React.FC<{ items: Insight[] }> = ({ items }) => {
  const t = useT();
  const text = useInsightText();
  if (items.length === 0) return null;
  return (
    <div className="tl-insights" role="note" aria-label={t.tl_insight_label}>
      <span className="tl-insights-label">{t.tl_insight_label}</span>
      <ul>
        {items.map((it, i) => (
          <li key={`${it.key}-${i}`}>
            <span className="tl-dot" style={{ background: TONE_COLOR[it.tone] }} aria-hidden />
            {text(it)}
          </li>
        ))}
      </ul>
    </div>
  );
};

export const TimelineSection: React.FC<{
  id: string; index: number; short: string; title: string; hint: string; insights: Insight[]; children: React.ReactNode;
}> = ({ id, index, short, title, hint, insights, children }) => (
  <section id={id} className="tl-section" aria-labelledby={`${id}-title`}>
    <header className="tl-section-head">
      <span className="tl-section-num num-display" aria-hidden>{String(index).padStart(2, '0')}</span>
      <div className="tl-section-text">
        <span className="tl-eyebrow">{short}</span>
        <h2 id={`${id}-title`} className="tl-section-title">{title}</h2>
        <p className="tl-section-hint">{hint}</p>
      </div>
    </header>
    <InsightList items={insights} />
    <div className="tl-section-body">{children}</div>
  </section>
);

/** いま読んでいる区：見出しがビューポート上部 1/3 を越えた最後の区。最下部では最後の区。 */
const useActiveSection = (ids: string[]): string | null => {
  const [active, setActive] = useState<string | null>(null);
  const key = ids.join(',');
  useEffect(() => {
    const list = key.split(',');
    let frame = 0;
    const update = (): void => {
      frame = 0;
      const line = window.innerHeight / 3;
      const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      const passed = list.filter((id) => (document.getElementById(id)?.getBoundingClientRect().top ?? Infinity) <= line);
      setActive(atBottom ? list[list.length - 1] : passed[passed.length - 1] ?? null);
    };
    const onScroll = (): void => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, [key]);
  return active;
};

/** 区へのジャンプ。hash ルーターなので <a href="#id"> ではなく scrollIntoView。 */
export const SectionNav: React.FC<{ sections: SectionMeta[] }> = ({ sections }) => {
  const active = useActiveSection(sections.map((s) => s.id));
  const navRef = useRef<HTMLElement>(null);
  // 狭い画面ではナビ自体が横スクロールする。今の区のチップを見える位置へ寄せる（ページは動かさない）
  useEffect(() => {
    const nav = navRef.current;
    const chip = nav?.querySelector<HTMLElement>('[aria-current]');
    if (!nav || !chip) return;
    const left = chip.offsetLeft - (nav.clientWidth - chip.offsetWidth) / 2;
    nav.scrollTo({ left, behavior: 'smooth' });
  }, [active]);
  return (
    <nav ref={navRef} className="tl-nav" aria-label="sections">
      {sections.map((s, i) => (
        <button key={s.id} type="button" className="tl-nav-chip" aria-current={active === s.id ? 'true' : undefined}
          onClick={() => document.getElementById(s.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
          <span className="tl-nav-num">{String(i + 1).padStart(2, '0')}</span>{s.short}
        </button>
      ))}
    </nav>
  );
};

// ───────────────────────── カード ─────────────────────────

/** タイトル＋「この図の読み方」1 行。読み方の無いカードは何を見ればいいか分からない。 */
export const InfoCard: React.FC<{
  title: string; hint?: string; right?: React.ReactNode; children: React.ReactNode; style?: React.CSSProperties;
}> = ({ title, hint, right, children, style }) => (
  <div className="tl-card" style={style}>
    <div className="tl-card-head">
      <h3 className="tl-card-title">{title}</h3>
      {right && <span className="tl-card-right">{right}</span>}
    </div>
    {hint && <p className="tl-card-hint">{hint}</p>}
    {children}
  </div>
);

/** カードを並べる格子。stretch=true で同じ行の高さを揃える（片方だけ短く浮くのを防ぐ）。 */
export const Grid: React.FC<{ min?: number; stretch?: boolean; children: React.ReactNode }> = ({ min = 360, stretch, children }) => (
  <div className="tl-grid" style={{
    gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, ${min}px), 1fr))`,
    alignItems: stretch ? 'stretch' : 'start',
  }}>{children}</div>
);

/** 既定で閉じておく補助情報（弱い維度・辞退一覧）。件数は閉じていても見える。 */
export const Collapsible: React.FC<{ summary: string; children: React.ReactNode }> = ({ summary, children }) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="tl-collapse">
      <button type="button" className="tl-collapse-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="tl-caret" style={{ transform: open ? 'rotate(90deg)' : 'none' }} aria-hidden>▸</span>
        {summary}
      </button>
      {open && <div style={{ marginTop: 12 }}>{children}</div>}
    </div>
  );
};

/** 数値の横に置く小さな統計（平均・中位数など）。 */
export const MiniStats: React.FC<{ items: [string, React.ReactNode][] }> = ({ items }) => (
  <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 12, color: C.sub, marginBottom: 10 }}>
    {items.map(([label, v]) => (
      <span key={label}>{label} <strong style={{ color: C.ink, fontSize: 14, fontVariantNumeric: 'tabular-nums' }}>{v}</strong></span>
    ))}
  </div>
);

// ───────────────────────── ドットプロット（数字の軸に点を積む） ─────────────────────────

/** 2 つの分布を同じ横軸で比べるため、値域を [min, max] に揃えて 0 件の列を補う。 */
export const padPoints = (points: DotPoint[], min: number, max: number): DotPoint[] => {
  const byValue = new Map(points.map((p) => [p.value, p]));
  return Array.from({ length: max - min + 1 }, (_, i) => byValue.get(min + i) ?? { value: min + i, n: 0 });
};

/**
 * Wilkinson 式ドットヒストグラム：横軸は実際の値（日数／点数）、縦は同じ値の
 * 件数を丸で積み上げる。4-5 本の太いバーに潰すと「3-7日」の中でも偏って
 * いるのか均等なのか、「80点」が最頻値なのかが消える——バーより情報量が多い。
 * 0 件の目盛りも列として残す（間引くと軸上の距離が歪む）。
 * marker を渡すと中位数などの位置に縦線を引く。
 */
export const DotHistogram: React.FC<{
  points: DotPoint[]; color: string; tickEvery?: number; suffix?: string; marker?: number | null; label?: string;
}> = ({ points, color, tickEvery = 5, suffix = '', marker, label }) => {
  // 列幅はカード幅に合わせて広げる（固定幅だと全幅カードの左 1/3 に縮こまる）。点の大きさは変えない
  const boxRef = useRef<HTMLDivElement>(null);
  const [boxW, setBoxW] = useState(0);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([e]) => setBoxW(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const dot = 6;
  const colWidth = Math.max(dot + 2, Math.min(26, points.length ? boxW / points.length : 0));
  const maxN = Math.max(1, ...points.map((p) => p.n));
  const plotHeight = Math.max(24, maxN * (dot + 1));
  const width = points.length * colWidth;
  const height = plotHeight + 18;
  const markerIdx = marker == null ? -1 : points.findIndex((p) => p.value >= marker);
  return (
    <div ref={boxRef} style={{ overflowX: 'auto' }}>
      <svg width={width} height={height} role="img" aria-label={label} style={{ display: 'block', minWidth: '100%' }}>
        <line x1={0} y1={plotHeight + 0.5} x2={width} y2={plotHeight + 0.5} stroke={C.border} strokeWidth={1} />
        {markerIdx >= 0 && (
          <line x1={markerIdx * colWidth + colWidth / 2} x2={markerIdx * colWidth + colWidth / 2}
            y1={0} y2={plotHeight} stroke={alpha(C.ink, 0.5)} strokeDasharray="3 3" />
        )}
        {points.map((p, i) => {
          const cx = i * colWidth + colWidth / 2;
          const tip = `${p.overflow ? `${p.value}+` : p.value}${suffix}: ${p.n}`;
          return (
            <g key={p.value}>
              {Array.from({ length: p.n }).map((_, j) => (
                <circle key={j} cx={cx} cy={plotHeight - dot / 2 - j * (dot + 1)} r={dot / 2} fill={color}>
                  <title>{tip}</title>
                </circle>
              ))}
              {i % tickEvery === 0 && (
                <text x={cx} y={height - 4} fontSize={11} fill={C.sub} textAnchor="middle">
                  {p.overflow ? `${p.value}+` : p.value}{i === 0 ? suffix : ''}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
};

/** 水平バー 1 行（ラベル・バー・数値・補足）。漏斗・拒絶分布・停留日数で共用。 */
export const BarRow: React.FC<{
  label: string; value: number; max: number; color: string; display?: React.ReactNode;
  note?: React.ReactNode; emphasis?: boolean; labelWidth?: number;
}> = ({ label, value, max, color, display, note, emphasis, labelWidth = 96 }) => (
  <div style={{ display: 'grid', gridTemplateColumns: `minmax(72px, ${labelWidth}px) 1fr auto`, alignItems: 'center', gap: 10, fontSize: 13 }}>
    <span style={{ color: C.ink, fontWeight: emphasis ? 700 : 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
      title={label}>{label}</span>
    <div style={{ height: 14, background: alpha(C.border, 0.45), borderRadius: 4, overflow: 'hidden' }}>
      <div style={{
        height: '100%', width: `${max > 0 ? Math.max(value > 0 ? 1.5 : 0, (value / max) * 100) : 0}%`,
        background: color, borderRadius: 4, transition: 'width .3s',
      }} />
    </div>
    <span style={{ display: 'flex', alignItems: 'baseline', gap: 8, justifyContent: 'flex-end', minWidth: note != null ? 120 : 56 }}>
      <strong style={{ color: C.ink, fontVariantNumeric: 'tabular-nums' }}>{display ?? value}</strong>
      <span style={{ fontSize: 11, color: C.sub, fontVariantNumeric: 'tabular-nums', minWidth: 70 }}>{note}</span>
    </span>
  </div>
);
