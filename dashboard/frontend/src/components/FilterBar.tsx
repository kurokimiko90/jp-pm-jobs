import React, { useEffect, useState } from 'react';
import { C } from '../theme';
import { FilterChip, FilterGroup, SourcePostingFilter } from './ui';
import { APPLIED_OPTIONS, SCORE_THRESHOLDS } from '../filters/dict';
import { useT } from '../i18n';

/**
 * 統一篩選列 — 以具名 slot 強制固定順序（缺層跳過，順序永不變）：
 *   搜尋 → 品質分數 → 範圍限定(地域/時間/語言/類型) → 來源/類型 → 應募狀態 → 更多篩選 → 清除
 * 狀態層 tabs 不在此列（用 StatusTabs 獨立一行）；操作按鈕禁入（用 ActionBar）。
 */

export interface SingleFilterSlot {
  label?: string;
  options: { key: string; label: React.ReactNode }[];
  value: string;
  onChange: (key: string) => void;
  variant?: 'gold' | 'ink';
}

export interface MultiFilterSlot {
  label?: string;
  allLabel: string;
  options: { key: string; label: React.ReactNode }[];
  value: string[];
  onChange: (v: string[]) => void;
}

type ScopeSlot = SingleFilterSlot | MultiFilterSlot;

const isMulti = (s: ScopeSlot): s is MultiFilterSlot => Array.isArray(s.value);

const Divider: React.FC = () => (
  <span style={{ width: 1, height: 20, background: 'rgba(232,213,183,0.7)', flexShrink: 0 }} />
);

const SearchInput: React.FC<{ value: string; onChange: (v: string) => void; placeholder: string }> =
  ({ value, onChange, placeholder }) => (
    <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
      <svg viewBox="0 0 24 24" fill="none" style={{ position: 'absolute', left: 14, width: 16, height: 16, pointerEvents: 'none' }}>
        <circle cx="11" cy="11" r="6.4" stroke="rgba(90,82,72,0.5)" strokeWidth="1.7" />
        <path d="M16 16l4 4" stroke="rgba(90,82,72,0.5)" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          height: 40, width: 240, maxWidth: '60vw', border: 'none', outline: 'none', borderRadius: 999,
          background: C.bg, boxShadow: 'inset 0 0 0 1.5px rgba(232,213,183,0.95)',
          padding: '0 16px 0 38px', fontSize: 14, fontWeight: 500, color: C.ink,
        }}
      />
    </div>
  );

const MultiChips: React.FC<{ slot: MultiFilterSlot }> = ({ slot }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
    {slot.label && (
      <span style={{ fontSize: 11, letterSpacing: 1.5, color: 'rgba(90,82,72,0.45)' }}>{slot.label}</span>
    )}
    <FilterChip active={slot.value.length === 0} onClick={() => slot.onChange([])}>{slot.allLabel}</FilterChip>
    {slot.options.map((o) => (
      <FilterChip
        key={o.key}
        active={slot.value.includes(o.key)}
        onClick={() => slot.onChange(
          slot.value.includes(o.key) ? slot.value.filter((x) => x !== o.key) : [...slot.value, o.key],
        )}
      >
        {o.label}
      </FilterChip>
    ))}
  </div>
);

interface ScoreFilterSlot {
  min: number;
  max: number;
  onChange: (min: number, max: number) => void;
  label?: string;
  thresholds?: number[];
}

const clampScore = (value: number): number => Math.min(100, Math.max(0, Math.round(value)));

const ScoreFilter: React.FC<{ slot: ScoreFilterSlot }> = ({ slot }) => {
  const t = useT();
  const [minDraft, setMinDraft] = useState(String(slot.min));
  const [maxDraft, setMaxDraft] = useState(String(slot.max));

  useEffect(() => setMinDraft(String(slot.min)), [slot.min]);
  useEffect(() => setMaxDraft(String(slot.max)), [slot.max]);

  const commit = (changed: 'min' | 'max') => {
    const parsedMin = minDraft.trim() === '' ? NaN : Number(minDraft);
    const parsedMax = maxDraft.trim() === '' ? NaN : Number(maxDraft);
    let min = clampScore(Number.isFinite(parsedMin) ? parsedMin : slot.min);
    let max = clampScore(Number.isFinite(parsedMax) ? parsedMax : slot.max);
    if (min > max) {
      if (changed === 'min') max = min;
      else min = max;
    }
    setMinDraft(String(min));
    setMaxDraft(String(max));
    slot.onChange(min, max);
  };

  const inputStyle: React.CSSProperties = {
    width: 54, height: 34, boxSizing: 'border-box', borderRadius: 9,
    border: `1.5px solid ${C.border}`, background: C.bg, outline: 'none',
    color: C.ink, fontSize: 12, fontWeight: 700, textAlign: 'center',
    fontVariantNumeric: 'tabular-nums',
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <FilterGroup
        label={slot.label}
        options={(slot.thresholds ?? SCORE_THRESHOLDS).map((m) => ({
          key: String(m), label: m === 0 ? t.jobs_all : `≥${m}`,
        }))}
        value={slot.max === 100 ? String(slot.min) : '__range__'}
        onChange={(key) => slot.onChange(Number(key), 100)}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11, letterSpacing: 1.2, color: 'rgba(90,82,72,0.45)' }}>
          {t.filter_score_range}
        </span>
        <input
          type="number" inputMode="numeric" min={0} max={100} value={minDraft}
          aria-label={t.filter_score_min} title={t.filter_score_min} style={inputStyle}
          onChange={(e) => setMinDraft(e.target.value)}
          onBlur={() => commit('min')}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit('min');
            }
          }}
        />
        <span style={{ color: C.sub, fontSize: 12 }}>–</span>
        <input
          type="number" inputMode="numeric" min={0} max={100} value={maxDraft}
          aria-label={t.filter_score_max} title={t.filter_score_max} style={inputStyle}
          onChange={(e) => setMaxDraft(e.target.value)}
          onBlur={() => commit('max')}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit('max');
            }
          }}
        />
      </div>
    </div>
  );
};

export const FilterBar: React.FC<{
  search?: { value: string; onChange: (v: string) => void; placeholder: string };
  score?: ScoreFilterSlot;
  scope?: ScopeSlot[];
  sourcePosting?: { source: string; postType: string; onSource: (v: string) => void; onPostType: (v: string) => void };
  channel?: SingleFilterSlot;
  applied?: { value: string; onChange: (v: string) => void };
  more?: React.ReactNode;
  /** 收合區內非預設的篩選數（>0 時預設展開並在切換鈕標數） */
  moreActive?: number;
  dirty?: boolean;
  onClear?: () => void;
}> = ({ search, score, scope = [], sourcePosting, channel, applied, more, moreActive = 0, dirty, onClear }) => {
  const t = useT();
  // 預設只顯示搜尋列；有非預設篩選（dirty）或標記的 moreActive 時才預設展開
  const [moreOpen, setMoreOpen] = useState(Boolean(dirty) || moreActive > 0);

  const hasPanel = Boolean(score || scope.length > 0 || sourcePosting || channel || applied || more);

  const mainGroups: React.ReactNode[] = [];
  const pushMain = (node: React.ReactNode, key: string) => {
    if (mainGroups.length > 0) mainGroups.push(<Divider key={`div-${key}`} />);
    mainGroups.push(<React.Fragment key={key}>{node}</React.Fragment>);
  };

  if (search) pushMain(<SearchInput {...search} />, 'search');
  if (hasPanel) {
    pushMain(
      <FilterChip active={moreOpen || moreActive > 0} onClick={() => setMoreOpen(!moreOpen)}>
        {t.filter_more} {moreOpen ? '▴' : '▾'}{moreActive > 0 ? ` · ${moreActive}` : ''}
      </FilterChip>,
      'more-toggle',
    );
  }

  const panelGroups: React.ReactNode[] = [];
  const pushPanel = (node: React.ReactNode, key: string) => {
    if (panelGroups.length > 0) panelGroups.push(<Divider key={`div-${key}`} />);
    panelGroups.push(<React.Fragment key={key}>{node}</React.Fragment>);
  };

  if (score) {
    pushPanel(<ScoreFilter slot={score} />, 'score');
  }
  scope.forEach((slot, i) => {
    pushPanel(
      isMulti(slot)
        ? <MultiChips slot={slot} />
        : <FilterGroup label={slot.label} options={slot.options} value={slot.value} onChange={slot.onChange} variant={slot.variant} />,
      `scope-${i}`,
    );
  });
  if (sourcePosting) pushPanel(<SourcePostingFilter {...sourcePosting} />, 'source');
  if (channel) {
    pushPanel(
      <FilterGroup label={channel.label} options={channel.options} value={channel.value} onChange={channel.onChange} />,
      'channel',
    );
  }
  if (applied) {
    pushPanel(
      <FilterGroup
        label={t.recommend_applied_label}
        variant="ink"
        options={APPLIED_OPTIONS.map((o) => ({ key: o.key, label: t[o.tKey] }))}
        value={applied.value}
        onChange={applied.onChange}
      />,
      'applied',
    );
  }
  if (more) pushPanel(more, 'more-extra');

  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        {mainGroups}
        {dirty && onClear && (
          <button
            onClick={onClear}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: '4px 2px',
              fontSize: 12, fontWeight: 500, color: C.sub,
              textDecoration: 'underline', textUnderlineOffset: 3,
            }}
          >
            {t.filter_clear}
          </button>
        )}
      </div>
      {hasPanel && moreOpen && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 10 }}>
          {panelGroups}
        </div>
      )}
    </div>
  );
};

// ── 表格右上操作區（批次開JD / 批次略過 / 手動觸發等，與篩選徹底分離） ──

export const ActionBar: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{
    display: 'flex', justifyContent: 'flex-end', alignItems: 'center',
    gap: 8, flexWrap: 'wrap', margin: '0 0 10px',
  }}>
    {children}
  </div>
);

export const ActionButton: React.FC<{
  onClick: () => void; children: React.ReactNode; tone?: 'danger'; disabled?: boolean;
}> = ({ onClick, children, tone, disabled }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    style={{
      padding: '7px 13px', borderRadius: 9, fontSize: 12, fontWeight: 600, cursor: 'pointer',
      background: C.bg, border: `1.5px solid ${C.border}`,
      color: tone === 'danger' ? C.errorRed : C.ink, whiteSpace: 'nowrap',
      ...(disabled ? { opacity: 0.5, cursor: 'default' } : {}),
    }}
  >
    {children}
  </button>
);
