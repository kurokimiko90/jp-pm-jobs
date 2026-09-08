import React from 'react';
import { Star } from '@phosphor-icons/react';
import { C, card, pill, scoreColor, recommendColor, getInitials, alpha, NARROW_BP } from '../theme';
import { useT } from '../i18n';
import { getStats } from '../api';

// ───────────────────────── 版面骨架（統一各頁手刻樣式） ─────────────────────────

/** 響應式：≤900px 視為窄螢幕。各頁雙欄 grid 據此收合為單欄。 */
export const useIsNarrow = (): boolean => {
  const [narrow, setNarrow] = React.useState(
    () => typeof window !== 'undefined' && window.matchMedia(`(max-width: ${NARROW_BP}px)`).matches,
  );
  React.useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${NARROW_BP}px)`);
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return narrow;
};

/** 頁首：30px 標題 + 副標 + 右側操作槽。取代各頁重複的 h1 區塊。 */
export const PageHeader: React.FC<{
  title: React.ReactNode; subtitle?: React.ReactNode; right?: React.ReactNode;
}> = ({ title, subtitle, right }) => (
  <div className="page-header" style={{
    display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
    gap: 20, flexWrap: 'wrap', marginBottom: 20,
  }}>
    <div className="page-header-copy">
      <h1 style={{
        fontSize: 'clamp(24px, 2vw, 32px)', fontWeight: 700,
        letterSpacing: 'clamp(0.5px, 0.14vw, 2px)', lineHeight: 1.25, margin: '0 0 4px',
      }}>{title}</h1>
      {subtitle != null && (
        <div style={{
          maxWidth: '78ch', fontSize: 14, fontWeight: 400, lineHeight: 1.65,
          letterSpacing: 'clamp(0.3px, 0.08vw, 1.2px)', color: 'rgba(90,82,72,0.55)',
        }}>
          {subtitle}
        </div>
      )}
    </div>
    {right != null && <div className="page-header-actions">{right}</div>}
  </div>
);

/** 小型大寫字距區塊標籤（いまやること / スコア分布 等）。 */
export const SectionLabel: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', ...style }}>{children}</div>
);

/**
 * 統一篩選膠囊：取代各頁手刻的 <div> 篩選鈕。
 * 用 <button> → 自動繼承字體 + 鍵盤 focus 框（index.html 全域）。
 * variant: gold（預設，黃底）/ ink（深靛實心，給「已投遞」類強調）。
 */
export const FilterChip: React.FC<{
  active?: boolean; onClick?: () => void; children: React.ReactNode; variant?: 'gold' | 'ink';
}> = ({ active = false, onClick, children, variant = 'gold' }) => {
  const activeBg = variant === 'ink' ? C.appliedInk : C.gold;
  const activeColor = variant === 'ink' ? '#FFF' : C.ink;
  return (
    <button
      onClick={onClick}
      style={{
        padding: '8px 14px', borderRadius: 999, fontSize: 13, fontWeight: 600,
        background: active ? activeBg : C.bg,
        color: active ? activeColor : C.sub,
        border: active ? 'none' : '1.5px solid rgba(232,213,183,0.5)',
        letterSpacing: 0.5, whiteSpace: 'nowrap',
      }}
    >{children}</button>
  );
};

/** 一組互斥篩選 chip（可選前置 label）。value 對應 option.key。 */
export const FilterGroup: React.FC<{
  label?: string;
  options: { key: string; label: React.ReactNode }[];
  value: string;
  onChange: (key: string) => void;
  variant?: 'gold' | 'ink';
}> = ({ label, options, value, onChange, variant }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
    {label && (
      <span style={{ fontSize: 11, letterSpacing: 1.5, color: 'rgba(90,82,72,0.45)' }}>{label}</span>
    )}
    {options.map((o) => (
      <FilterChip key={o.key} active={value === o.key} variant={variant} onClick={() => onChange(o.key)}>
        {o.label}
      </FilterChip>
    ))}
  </div>
);

/** 可排序表頭欄位：取代各頁獨立的排序 chip 列，直接點欄位標題排序。 */
export const SortTh: React.FC<{
  children: React.ReactNode; active: boolean; order: 'asc' | 'desc'; onClick: () => void;
}> = ({ children, active, order, onClick }) => (
  <div
    onClick={onClick}
    style={{
      display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', userSelect: 'none',
      color: active ? C.ink : 'inherit', fontWeight: active ? 700 : 400,
    }}
  >
    {children}
    <span style={{ fontSize: 9, opacity: active ? 1 : 0.3 }}>{active ? (order === 'desc' ? '▼' : '▲') : '▲▼'}</span>
  </div>
);

// ───────────────────────── Report 專用小組件 ─────────────────────────

// KPI 數字卡：大數字 + 標籤 + 可選副標。onClick 時可點（作篩選入口），active 金框標示篩選中
export const ReportStat: React.FC<{
  value: React.ReactNode; label: string; sub?: string; color?: string;
  onClick?: () => void; active?: boolean;
}> = ({ value, label, sub, color = C.ink, onClick, active }) => (
  <div onClick={onClick} style={{
    flex: '1 1 120px', minWidth: 120, background: C.bg, borderRadius: 10,
    padding: '12px 14px', border: `1px solid ${C.border}`,
    cursor: onClick ? 'pointer' : undefined,
    boxShadow: active ? `inset 0 0 0 1.5px ${C.gold}` : undefined,
  }}>
    <div style={{ fontSize: 26, fontWeight: 800, color, fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 }}>{value}</div>
    <div style={{ fontSize: 12, color: C.ink, fontWeight: 600, marginTop: 4 }}>{label}</div>
    {sub && <div style={{ fontSize: 11, color: C.sub, marginTop: 2 }}>{sub}</div>}
  </div>
);

// 水平佔比 bar（gap 頻次 / 分布用）
export const ReportBar: React.FC<{ value: number; max: number; color: string; width?: number }> = ({ value, max, color, width = 56 }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
    <div style={{ width, height: 6, borderRadius: 3, background: `${C.border}`, overflow: 'hidden' }}>
      <div style={{ height: '100%', width: `${max > 0 ? (value / max) * 100 : 0}%`, background: color, borderRadius: 3 }} />
    </div>
    <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', fontWeight: 700, color: C.ink, minWidth: 16 }}>{value}</span>
  </div>
);

/**
 * 輕量 markdown 渲染：支援 ## 標題、**粗體**、- / * 列表、純段落。
 * 不引入套件，足以美化 LLM 輸出的整體畫像。
 */
const renderInline = (text: string, keyPrefix: string): React.ReactNode[] => {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) {
      return <strong key={`${keyPrefix}-${i}`} style={{ color: C.ink, fontWeight: 700 }}>{p.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={`${keyPrefix}-${i}`}>{p}</React.Fragment>;
  });
};

export const ReportMarkdown: React.FC<{ text: string }> = ({ text }) => {
  const lines = text.split('\n');
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  let bi = 0;

  const flushList = () => {
    if (!list.length) return;
    const items = [...list];
    blocks.push(
      <ul key={`ul-${bi++}`} style={{ margin: '4px 0 8px', paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
        {items.map((li, i) => (
          <li key={i} style={{ fontSize: 13, color: C.ink, lineHeight: 1.6 }}>{renderInline(li, `li-${bi}-${i}`)}</li>
        ))}
      </ul>
    );
    list = [];
  };

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) { flushList(); return; }
    const h = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (h) {
      flushList();
      blocks.push(
        <div key={`h-${idx}`} style={{
          fontSize: 13, fontWeight: 700, color: C.ink, margin: '10px 0 4px',
          paddingLeft: 8, borderLeft: `3px solid ${C.gold}`,
        }}>{renderInline(h[2], `h-${idx}`)}</div>
      );
      return;
    }
    const lm = /^[-*]\s+(.*)$/.exec(trimmed);
    if (lm) { list.push(lm[1]); return; }
    flushList();
    blocks.push(
      <p key={`p-${idx}`} style={{ margin: '0 0 8px', fontSize: 13, color: C.ink, lineHeight: 1.7 }}>{renderInline(trimmed, `p-${idx}`)}</p>
    );
  });
  flushList();
  return <div>{blocks}</div>;
};

export const Card: React.FC<{ style?: React.CSSProperties; children: React.ReactNode }> = ({ style, children }) => (
  <div style={{ ...card, ...style }}>{children}</div>
);

export const Pill: React.FC<{
  active?: boolean; onClick?: () => void; children: React.ReactNode; style?: React.CSSProperties;
}> = ({ active = false, onClick, children, style }) => (
  <button onClick={onClick} style={{ ...pill(active), ...style }}>{children}</button>
);

// ───────────────────────── 展開面板操作列（共用） ─────────────────────────

export interface PanelAction {
  label: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'danger';
}

const panelLinkStyle = (tone?: 'danger', disabled?: boolean): React.CSSProperties => ({
  background: 'none', border: 'none', padding: '4px 2px', cursor: 'pointer',
  fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap',
  color: tone === 'danger' ? C.errorRed : C.sub,
  textDecoration: 'underline', textUnderlineOffset: 3,
  textDecorationColor: alpha(tone === 'danger' ? C.errorRed : C.sub, 0.35),
  ...(disabled ? { opacity: 0.4, pointerEvents: 'none' } : {}),
});

/**
 * 展開面板統一操作列：主次分明，取代整排等權重 Pill。
 * primary → 金色實心 pill（最常用，1-2 個）；links → 12px 文字連結（次要導覽）；
 * right → 靠右文字連結（tone: 'danger' 紅字，給刪除/略過等最弱化操作）。
 */
export const PanelActions: React.FC<{
  primary?: PanelAction[]; links?: PanelAction[]; right?: PanelAction[];
}> = ({ primary = [], links = [], right = [] }) => (
  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
    {primary.map((a, i) => (
      <Pill key={`p-${i}`} active onClick={a.onClick}
        style={{ fontWeight: 700, ...(a.disabled ? { opacity: 0.4, pointerEvents: 'none' } : {}) }}>
        {a.label}
      </Pill>
    ))}
    {links.map((a, i) => (
      <button key={`l-${i}`} onClick={a.onClick} style={panelLinkStyle(a.tone, a.disabled)}>{a.label}</button>
    ))}
    <span style={{ flex: 1 }} />
    {right.map((a, i) => (
      <button key={`r-${i}`} onClick={a.onClick} style={panelLinkStyle(a.tone, a.disabled)}>{a.label}</button>
    ))}
  </div>
);

export const Badge: React.FC<{ color?: string; children: React.ReactNode }> = ({ color = C.sub, children }) => (
  <span style={{
    display: 'inline-block', borderRadius: 999, padding: '1px 9px', fontSize: 11,
    background: `${alpha(color, 0.13)}`, color, border: `1px solid ${alpha(color, 0.33)}`, whiteSpace: 'nowrap',
  }}>{children}</span>
);

/**
 * 列表行的判斷依據小標籤列（公司人數 / JD 是否提及 AI）。年収獨立成欄，不放這裡。
 * 三頁（職缺／推薦列表／官網直投）共用，置於公司/職稱 cell 第三行，不佔額外欄位。
 * 缺值一律省略（不顯示「—」），全部缺值時整列不渲染。
 */
export const JobFacts: React.FC<{
  employeeCount?: number | null;
  mentionsAi?: number | null;
}> = ({ employeeCount, mentionsAi }) => {
  const t = useT();
  if (employeeCount == null && mentionsAi !== 1) return null;
  return (
    <div style={{ display: 'flex', gap: 5, marginTop: 3, flexWrap: 'wrap' }}>
      {employeeCount != null && (
        <Badge color={C.sub}>{t.jobs_employee_count.replace('{n}', String(employeeCount))}</Badge>
      )}
      {mentionsAi === 1 && (
        <Badge color={C.gold}>{t.jobs_ai_mentioned}</Badge>
      )}
    </div>
  );
};

/**
 * 公司／職缺列表欄的唯一樣板。最初由推薦列表定義，所有職缺列表均使用相同的
 * initials、公司＋OpenWork、職缺名稱與 facts 三行排列；頁面特有的欄位留在 cell 外。
 */
export const JobIdentityCell: React.FC<{
  company?: string | null;
  title?: string | null;
  score?: number | null;
  recommendScore?: number | null;
  openworkScore?: number | null;
  openworkUrl?: string | null;
  employeeCount?: number | null;
  mentionsAi?: number | null;
}> = ({ company, title, score, recommendScore, openworkScore, openworkUrl, employeeCount, mentionsAi }) => {
  const color = recommendColor(recommendScore ?? score);
  const companyName = company || title || '—';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
      <span style={{
        width: 36, height: 36, borderRadius: 8, background: `${alpha(color, 0.13)}`, color,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 12, fontWeight: 700, flexShrink: 0,
      }}>
        {getInitials(companyName)}
      </span>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{
            minWidth: 0, flex: '1 1 auto', fontSize: 15, fontWeight: 700, letterSpacing: 0.5,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {companyName}
          </span>
          {openworkScore != null && (
            <a
              href={openworkUrl || '#'}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              style={{ flex: '0 0 auto', textDecoration: 'none' }}
              title={`OpenWork ${openworkScore}`}
            >
              <Badge color={openworkScore >= 3.5 ? C.successGreen : openworkScore >= 3.0 ? '#C79A1B' : C.sub}>
                OW {openworkScore.toFixed(2)}
              </Badge>
            </a>
          )}
        </div>
        <div style={{ fontSize: 12, fontWeight: 400, color: 'rgba(90,82,72,0.6)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {title || '—'}
        </div>
        <JobFacts employeeCount={employeeCount} mentionsAi={mentionsAi} />
      </div>
    </div>
  );
};

/** 年収欄專用格式化：兩端皆無值顯示 —，單邊缺值用 ? 補位。 */
export const SalaryCell: React.FC<{ salaryMin?: number | null; salaryMax?: number | null }> = ({ salaryMin, salaryMax }) => {
  const t = useT();
  if (salaryMin == null && salaryMax == null) {
    return <span style={{ fontSize: 12, color: 'rgba(90,82,72,0.35)' }}>—</span>;
  }
  return (
    <span style={{ fontSize: 12, fontWeight: 700, color: C.successGreen, whiteSpace: 'nowrap' }}>
      {salaryMin ?? '?'}–{salaryMax ?? '?'}{t.analytics_salary_unit}
    </span>
  );
};

// ───────────────────────── 應募狀態（共用） ─────────────────────────

export const useAppStatusLabels = (): Record<string, string> => {
  const t = useT();
  return {
    applied: t.status_applied,
    casual: t.status_casual,
    recruiter: t.status_recruiter,
    tech: t.status_tech,
    onsite: t.status_onsite,
    offer: t.status_offer,
    rejected: t.status_rejected,
  };
};

// 選考漏斗節點（rejected 不在序列內，單獨處理）
export const STAGE_ORDER = ['applied', 'casual', 'recruiter', 'tech', 'onsite', 'offer'];

/**
 * 應募標識 badge。優先級：本職缺已投（實心）> 同公司已投（輪廓，可點跳轉）。
 * 兩者皆無 → 回傳 null，由呼叫端 fallback 到既有邏輯。
 */
export const AppStatusBadge: React.FC<{
  appStatus?: string | null;
  companyAppStatus?: string | null;
  onCompanyClick?: () => void;
}> = ({ appStatus, companyAppStatus, onCompanyClick }) => {
  const t = useT();
  const APP_STATUS_LABELS = useAppStatusLabels();
  if (appStatus) {
    const bg = appStatus === 'offer' ? C.doneGold
      : appStatus === 'rejected' ? C.draftGray : C.appliedInk;
    const fg = appStatus === 'offer' ? C.ink : '#FFF';
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', borderRadius: 999,
        padding: '4px 11px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
        background: bg, color: fg, letterSpacing: 0.3,
      }}>
        {APP_STATUS_LABELS[appStatus] ?? appStatus}
      </span>
    );
  }
  if (companyAppStatus) {
    return (
      <span
        onClick={onCompanyClick ? (e) => { e.stopPropagation(); onCompanyClick(); } : undefined}
        title={t.app_status_same_company_title}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 2, borderRadius: 999,
          padding: '3px 10px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
          background: 'transparent', color: C.progressIndigo,
          border: `1px solid ${alpha(C.progressIndigo, 0.4)}`,
          cursor: onCompanyClick ? 'pointer' : 'default',
        }}
      >
        {t.app_status_same_company_short}{onCompanyClick ? ` ${t.app_status_same_company_jump}` : ''}
      </span>
    );
  }
  return null;
};

/** 選考ステージ進度點：applied→recruiter→tech→onsite→offer。
 * rejected：reached（實際走到的最遠階段）有值時畫灰點到該階段 + 紅✕收尾，
 * 無值（舊資料無歷程）fallback 純文字。 */
export const StageDots: React.FC<{ status: string; reached?: string }> = ({ status, reached }) => {
  const t = useT();
  if (status === 'rejected') {
    const ridx = STAGE_ORDER.indexOf(reached ?? '');
    if (ridx < 0) {
      return <span style={{ fontSize: 12, color: C.draftGray, fontWeight: 700 }}>{t.stage_rejected_short}</span>;
    }
    return (
      <div style={{ display: 'flex', alignItems: 'center' }} title={t.stage_rejected_short}>
        {STAGE_ORDER.slice(0, ridx + 1).map((s, i) => (
          <React.Fragment key={s}>
            {i > 0 && <span style={{ width: 12, height: 2, background: C.draftGray }} />}
            <span style={{ width: 9, height: 9, borderRadius: 999, background: C.draftGray }} />
          </React.Fragment>
        ))}
        <span style={{ width: 12, height: 2, background: C.draftGray }} />
        <span style={{ fontSize: 12, fontWeight: 800, color: C.errorRed, lineHeight: 1 }}>✕</span>
      </div>
    );
  }
  const idx = STAGE_ORDER.indexOf(status);
  return (
    <div style={{ display: 'flex', alignItems: 'center' }}>
      {STAGE_ORDER.map((s, i) => {
        const reached = i <= idx;
        const col = reached ? (s === 'offer' ? C.doneGold : C.progressIndigo) : C.border;
        return (
          <React.Fragment key={s}>
            {i > 0 && <span style={{ width: 12, height: 2, background: i <= idx ? col : C.border }} />}
            <span style={{
              width: 9, height: 9, borderRadius: 999, background: col,
              boxShadow: i === idx ? `0 0 0 3px ${alpha(col, 0.2)}` : undefined,
            }} />
          </React.Fragment>
        );
      })}
    </div>
  );
};

export const PostingBadge: React.FC<{ type?: string | null }> = ({ type }) => {
  const t = useT();
  const meta: Record<string, { label: string; color: string }> = {
    direct:  { label: t.analytics_direct, color: '#4A9076' },
    shokai:  { label: t.analytics_shokai, color: '#B07A2E' },
    haken:   { label: t.analytics_haken, color: '#A0522D' },
  };
  const { label, color } = meta[type ?? ''] ?? meta.direct;
  return (
    <span style={{
      display: 'inline-block', borderRadius: 4, padding: '2px 7px', fontSize: 11, fontWeight: 700,
      background: `${alpha(color, 0.09)}`, color, letterSpacing: 0.5, whiteSpace: 'nowrap',
    }}>{label}</span>
  );
};

const SOURCE_META: Record<string, { domain: string; label: string; accent: string }> = {
  linkedin_jp:      { domain: 'linkedin.com',  label: 'LinkedIn',   accent: '#0A66C2' },
  indeed_jp:        { domain: 'indeed.com',    label: 'Indeed',     accent: '#2164F3' },
  bizreach:         { domain: 'bizreach.jp',   label: 'Bizreach',   accent: '#E02020' },
  wantedly:         { domain: 'wantedly.com',  label: 'Wantedly',   accent: '#21D6AD' },
  green:            { domain: 'green-japan.com', label: 'Green',    accent: '#00A784' },
  'greenhouse-api': { domain: 'greenhouse.io', label: 'Greenhouse', accent: '#24A148' },
  'lever-api':      { domain: 'lever.co',      label: 'Lever',      accent: '#5B4CE2' },
  'ashby-api':      { domain: 'ashbyhq.com',   label: 'Ashby',      accent: '#E8813A' },
  recruiter_agent:  { domain: 'gmail.com',     label: 'R-agent',    accent: '#B06AB3' },
  recruit_agent:    { domain: 'gmail.com',     label: 'R-agent',    accent: '#B06AB3' },
  jac_recruitment:  { domain: 'jac-recruitment.jp', label: 'JAC',   accent: '#C8102E' },
};

export const SourceLogo: React.FC<{ source: string; postingType?: string | null }> = ({ source, postingType }) => {
  const meta = SOURCE_META[source] ?? { domain: source, label: source, accent: '#8B7D6B' };
  const isNotDirect = postingType === 'shokai' || postingType === 'haken';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, alignItems: 'flex-start' }}>
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        background: `${alpha(meta.accent, 0.07)}`,
        border: `1px solid ${alpha(meta.accent, 0.19)}`,
        borderRadius: 6, padding: '4px 8px',
      }}>
        <img
          src={`https://www.google.com/s2/favicons?domain=${meta.domain}&sz=32`}
          width={14} height={14}
          style={{ flexShrink: 0, display: 'block' }}
          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
        />
        <span style={{ fontSize: 11, fontWeight: 700, color: meta.accent, whiteSpace: 'nowrap', letterSpacing: 0.3 }}>
          {meta.label}
        </span>
      </div>
      {isNotDirect && <PostingBadge type={postingType} />}
    </div>
  );
};

// ───────────────────────── 來源 + 投稿類型篩選列 ─────────────────────────

/** 來源清單改由 DB 動態取得（/api/stats 的 by_source）：新增 scraper 來源時自動出現在篩選列。
 *  釘選來源固定排前面，其餘按職缺數多寡接在後面。 */
const PINNED_SOURCES = ['recruiter_agent', 'green', 'indeed_jp', 'linkedin_jp', 'bizreach'];
const FALLBACK_SOURCES = [...PINNED_SOURCES, 'greenhouse-api', 'ashby-api', 'lever-api'];

const orderSources = (all: string[]): string[] => [
  ...PINNED_SOURCES.filter((s) => all.includes(s)),
  ...all.filter((s) => !PINNED_SOURCES.includes(s)),
];

let sourcesCache: string[] | null = null;

const useJobSources = (): string[] => {
  const [sources, setSources] = React.useState<string[]>(sourcesCache ?? FALLBACK_SOURCES);
  React.useEffect(() => {
    if (sourcesCache) return;
    getStats()
      .then((d: { by_source?: { source: string }[] }) => {
        sourcesCache = orderSources((d.by_source ?? []).map((r) => r.source));
        setSources(sourcesCache);
      })
      .catch(() => {}); // 取失敗時沿用 FALLBACK_SOURCES，篩選仍可用
  }, []);
  return sources;
};

const POSTING_OPTIONS: { key: string; label: string }[] = [
  { key: '', label: '' },
  { key: 'direct', label: 'direct' },
  { key: 'shokai', label: 'shokai' },
  { key: 'haken', label: 'haken' },
];

export const SourcePostingFilter: React.FC<{
  source: string;
  postType: string;
  onSource: (v: string) => void;
  onPostType: (v: string) => void;
}> = ({ source, postType, onSource, onPostType }) => {
  const t = useT();
  const dbSources = useJobSources();
  const sourceOptions = [
    { key: '', label: t.source_all },
    ...dbSources.map((s) => ({ key: s, label: SOURCE_META[s]?.label ?? s })),
  ];
  const postingOptions = POSTING_OPTIONS.map((o) => ({
    ...o,
    label: o.key === ''
      ? t.posting_all
      : o.key === 'direct'
      ? t.analytics_direct
      : o.key === 'shokai'
      ? t.analytics_shokai
      : t.analytics_haken,
  }));
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 11, fontWeight: 400, letterSpacing: 1.5, color: 'rgba(90,82,72,0.45)', marginRight: 2 }}>{t.filter_source}</span>
      {sourceOptions.map((o) => (
        <Pill key={o.key} active={source === o.key} onClick={() => onSource(o.key)}>{o.label}</Pill>
      ))}
      <span style={{ width: 1, height: 20, background: 'rgba(232,213,183,0.7)', margin: '0 6px' }} />
      <span style={{ fontSize: 11, fontWeight: 400, letterSpacing: 1.5, color: 'rgba(90,82,72,0.45)', marginRight: 2 }}>{t.filter_posting}</span>
      {postingOptions.map((o) => (
        <Pill key={o.key} active={postType === o.key} onClick={() => onPostType(o.key)}>{o.label}</Pill>
      ))}
    </div>
  );
};

export const Score: React.FC<{ value: number | null; size?: number }> = ({ value, size = 14 }) => (
  <span style={{
    fontVariantNumeric: 'tabular-nums', fontWeight: 700,
    color: scoreColor(value), fontSize: size,
  }}>{value ?? '—'}</span>
);

export const RankScore: React.FC<{ value: number | null; rank?: number }> = ({ value, rank }) => {
  const col = recommendColor(value);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 64 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        {rank != null && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 1, fontSize: 11, color: rank <= 3 ? C.gold : C.sub, fontWeight: 700 }}>
            {rank <= 3 ? <Star size={10} weight="fill" /> : '#'}{rank}
          </span>
        )}
        <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 700, fontSize: 14, color: col }}>
          {value ?? '—'}
        </span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: `${C.border}`, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${value ?? 0}%`, background: col }} />
      </div>
    </div>
  );
};

export const SectionTitle: React.FC<{ children: React.ReactNode; right?: React.ReactNode }> = ({ children, right }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '4px 0 10px' }}>
    <h3 style={{ margin: 0, fontSize: 15, color: C.ink }}>{children}</h3>
    {right}
  </div>
);

export const Empty: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ color: C.sub, fontSize: 13, padding: '14px 0', textAlign: 'center' }}>{children}</div>
);

/** 骨架屏：貼合列表行的欄位寬度，載入中顯示，取代裸文字「読み込み中…」。 */
export const SkeletonRows: React.FC<{ cols: (number | string)[]; rows?: number }> = ({ cols, rows = 6 }) => (
  <div>
    {Array.from({ length: rows }).map((_, i) => (
      <div
        key={i}
        style={{
          display: 'grid', gridTemplateColumns: cols.map((c) => (typeof c === 'number' ? `${c}px` : c)).join(' '),
          gap: 16, alignItems: 'center', padding: '14px 26px',
          borderBottom: '1px solid rgba(232,213,183,0.5)',
        }}
      >
        {cols.map((_, j) => (
          <div key={j} style={{
            height: 14, borderRadius: 4, background: C.border, opacity: 0.35 + (i % 3) * 0.05,
          }} />
        ))}
      </div>
    ))}
  </div>
);

/** 錯誤態：訊息 + 重試按鈕，取代空白或裸文字錯誤。 */
export const ErrorState: React.FC<{ message?: string; onRetry?: () => void }> = ({ message, onRetry }) => {
  const t = useT();
  return (
    <div style={{ padding: '32px 16px', textAlign: 'center' }}>
      <div style={{ fontSize: 13, color: C.errorRed, marginBottom: onRetry ? 12 : 0 }}>
        {message || t.error_load_failed}
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            border: `1px solid ${C.border}`, borderRadius: 999, padding: '6px 16px',
            background: C.card, color: C.ink, fontSize: 13, cursor: 'pointer',
          }}
        >
          {t.error_retry}
        </button>
      )}
    </div>
  );
};

export const Cmd: React.FC<{ children: string }> = ({ children }) => {
  const t = useT();
  return (
    <code
      onClick={() => navigator.clipboard.writeText(children)}
      title={t.cmd_copy}
      style={{
        background: '#F5EFE6', border: `1px solid ${C.border}`, borderRadius: 6,
        padding: '2px 8px', fontSize: 12, cursor: 'copy', color: C.ink,
      }}
    >{children}</code>
  );
};

export const Pager: React.FC<{
  page: number;
  total: number;
  size: number;
  onPage: (p: number) => void;
  onSize?: (s: number) => void;
  sizeOptions?: number[];
  showJumper?: boolean;
}> = ({ page, total, size, onPage, onSize, sizeOptions = [10, 25, 50], showJumper }) => {
  const t = useT();
  // hooks 必須在任何提前 return 之前呼叫（否則單頁⇄多頁切換時 hooks 數量改變 → React #300 白屏）
  const [jumperVal, setJumperVal] = React.useState('');
  const pages = Math.max(1, Math.ceil(total / size));
  if (pages <= 1 && !onSize) return null;
  if (pages <= 1) {
    // Still show total + size selector when onSize is provided
    return (
      <div style={{
        padding: '14px 26px', borderTop: '1px solid rgba(232,213,183,0.5)',
        display: 'flex', alignItems: 'center', gap: 12,
      }}>
        <span style={{ fontSize: 12, color: C.sub }}>{t.pager_total.replace('{total}', String(total))}</span>
        {onSize && (
          <select
            value={size}
            onChange={(e) => onSize(Number(e.target.value))}
            style={{
              height: 30, borderRadius: 6, border: `1px solid ${C.border}`,
              background: C.bg, fontSize: 12, color: C.ink, padding: '0 8px',
              cursor: 'pointer', outline: 'none',
            }}
          >
            {sizeOptions.map((s) => (
              <option key={s} value={s}>{t.pager_per_page.replace('{size}', String(s))}</option>
            ))}
          </select>
        )}
      </div>
    );
  }

  // Build page number list: first, last, current ±1, with ellipsis gaps
  const nums: number[] = [];
  const add = (n: number) => { if (n >= 1 && n <= pages && !nums.includes(n)) nums.push(n); };
  add(1);
  add(page - 1);
  add(page);
  add(page + 1);
  add(pages);
  nums.sort((a, b) => a - b);

  const pageElements: React.ReactNode[] = [];
  for (let i = 0; i < nums.length; i++) {
    if (i > 0 && nums[i] - nums[i - 1] > 1) {
      pageElements.push(<span key={`e-${i}`} style={{ color: C.sub, fontSize: 12, userSelect: 'none' }}>…</span>);
    }
    const n = nums[i];
    pageElements.push(
      <Pill key={n} active={n === page} onClick={() => onPage(n)}>{n}</Pill>
    );
  }

  const disabledStyle: React.CSSProperties = { opacity: 0.35, cursor: 'default' };
  const autoJumper = showJumper ?? pages > 4;

  const handleJump = () => {
    const n = parseInt(jumperVal, 10);
    if (!isNaN(n) && n >= 1 && n <= pages) {
      onPage(n);
      setJumperVal('');
    }
  };

  return (
    <div style={{
      padding: '14px 26px', borderTop: '1px solid rgba(232,213,183,0.5)',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
      flexWrap: 'wrap',
    }}>
      {/* Left: total + size selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 12, color: C.sub, whiteSpace: 'nowrap' }}>{t.pager_total.replace('{total}', String(total))}</span>
        {onSize && (
          <select
            value={size}
            onChange={(e) => onSize(Number(e.target.value))}
            style={{
              height: 30, borderRadius: 6, border: `1px solid ${C.border}`,
              background: C.bg, fontSize: 12, color: C.ink, padding: '0 8px',
              cursor: 'pointer', outline: 'none',
            }}
          >
            {sizeOptions.map((s) => (
              <option key={s} value={s}>{t.pager_per_page.replace('{size}', String(s))}</option>
            ))}
          </select>
        )}
      </div>

      {/* Center: page navigation */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Pill onClick={() => page > 1 ? onPage(1) : undefined} style={page === 1 ? disabledStyle : undefined}>«</Pill>
        <Pill onClick={() => page > 1 ? onPage(page - 1) : undefined} style={page === 1 ? disabledStyle : undefined}>{t.pager_prev}</Pill>
        {pageElements}
        <Pill onClick={() => page < pages ? onPage(page + 1) : undefined} style={page >= pages ? disabledStyle : undefined}>{t.pager_next}</Pill>
        <Pill onClick={() => page < pages ? onPage(pages) : undefined} style={page >= pages ? disabledStyle : undefined}>»</Pill>
      </div>

      {/* Right: jumper */}
      {autoJumper ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 12, color: C.sub, whiteSpace: 'nowrap' }}>{t.pager_jump}</span>
          <input
            type="number"
            min={1}
            max={pages}
            value={jumperVal}
            onChange={(e) => setJumperVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleJump(); }}
            style={{
              width: 48, height: 30, textAlign: 'center', borderRadius: 6,
              border: `1px solid ${C.border}`, fontSize: 12, color: C.ink,
              background: C.bg, outline: 'none',
            }}
          />
          <span style={{ fontSize: 12, color: C.sub, whiteSpace: 'nowrap' }}>{t.pager_go}</span>
        </div>
      ) : <div />}
    </div>
  );
};
