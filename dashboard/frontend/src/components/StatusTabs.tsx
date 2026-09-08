import React from 'react';
import { C, alpha } from '../theme';

export interface StatusTabItem {
  key: string;
  label: string;
  count?: number;
  color?: string;
}

/**
 * 狀態層 tabs — 各列表頁「看哪個子集合」的最高層級導航，獨立一行放篩選列之上。
 * 統一取代：統計卡兼篩選（推薦/投遞包）、雜牌 status pills（応募管理/官網直投）。
 * 明確的按鈕外觀 + 計數，可點性一目了然；統計卡從此只做純展示。
 */
export const StatusTabs: React.FC<{
  tabs: StatusTabItem[];
  value: string;
  onChange: (key: string) => void;
  /** count === 0 的 tab 隱藏（第一個「全部」tab 永遠顯示） */
  hideEmpty?: boolean;
}> = ({ tabs, value, onChange, hideEmpty }) => (
  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
    {tabs.map((tb, i) => {
      if (hideEmpty && i > 0 && (tb.count ?? 0) === 0) return null;
      const active = value === tb.key;
      const col = tb.color ?? C.gold;
      return (
        <button
          key={tb.key}
          onClick={() => onChange(tb.key)}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer',
            padding: '9px 15px', borderRadius: 12, fontSize: 13, letterSpacing: 0.5,
            border: 'none', whiteSpace: 'nowrap',
            fontWeight: active ? 700 : 500,
            color: active ? C.ink : C.sub,
            background: active ? alpha(col, 0.13) : C.bg,
            boxShadow: active
              ? `inset 0 0 0 1.5px ${col}`
              : `inset 0 0 0 1.5px ${C.border}`,
          }}
        >
          {tb.label}
          {tb.count != null && (
            <span style={{
              fontVariantNumeric: 'tabular-nums', fontWeight: 800, fontSize: 13,
              color: active ? col : 'rgba(90,82,72,0.45)',
            }}>
              {tb.count}
            </span>
          )}
        </button>
      );
    })}
  </div>
);
