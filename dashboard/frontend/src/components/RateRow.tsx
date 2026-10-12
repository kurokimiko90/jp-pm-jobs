import React from 'react';
import { C, alpha } from '../theme';
import { useT } from '../i18n';

/** 條件通過率的一行：點估計 + Wilson 95% CI + 相對基準的 lift。 */
export interface RateItem {
  key: string; passed: number; decided: number; pending: number; unknown: number;
  rate: number | null; ci_low: number | null; ci_high: number | null;
  lift: number | null; insufficient: boolean;
}
// ───────────────────────── 通過率チャート（点推定 + 95% CI） ─────────────────────────

/**
 * 1 行 = 1 分群。バー長ではなく「点 + 誤差棒」で描くのは、
 * n=1 の 100% と n=146 の 16% を同じ太さのバーで並べると誤読するため。
 * 破線 = 全体基準率。これが無いと「高い/低い」の判断ができない。
 */
export const RateRow: React.FC<{
  label: string; item: RateItem; baseline: number | null; scaleMax: number; insufficientText: string;
}> = ({ label, item, baseline, scaleMax, insufficientText }) => {
  const t = useT();
  const dim = item.insufficient;
  const pos = (v: number): string => `${Math.min(100, (v / scaleMax) * 100)}%`;
  const lo = item.ci_low ?? 0;
  const hi = item.ci_high ?? 0;
  const rate = item.rate ?? 0;
  const color = dim ? C.sub : (item.lift != null && item.lift >= 1.3) ? C.successGreen
    : (item.lift != null && item.lift <= 0.7) ? C.errorRed : C.ink;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
      <span style={{
        minWidth: 92, color: dim ? C.sub : C.ink, fontWeight: dim ? 400 : 600,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }} title={label}>{label}</span>

      <div style={{ position: 'relative', flex: 1, height: 18, minWidth: 90 }}>
        {/* 目盛りの底線 */}
        <div style={{
          position: 'absolute', top: 8.5, left: 0, right: 0, height: 1, background: alpha(C.border, 0.45),
        }} />
        {/* 全体基準率 */}
        {baseline != null && (
          <div title={`${t.app_stats_baseline} ${baseline}%`} style={{
            position: 'absolute', top: 1, bottom: 1, left: pos(baseline), width: 0,
            borderLeft: `1px dashed ${alpha(C.ink, 0.55)}`,
          }} />
        )}
        {/* 95% 信頼区間 */}
        <div style={{
          position: 'absolute', top: 7.5, height: 3, borderRadius: 2,
          left: pos(lo), width: `calc(${pos(hi)} - ${pos(lo)})`,
          background: alpha(color, dim ? 0.14 : 0.22),
        }} />
        {/* 点推定 */}
        <div title={`${rate}% (95%CI ${lo}–${hi})`} style={{
          position: 'absolute', top: 3, left: pos(rate), width: 8, height: 8,
          marginLeft: -6, borderRadius: '50%', background: color,
          border: `2px solid ${C.card}`, boxSizing: 'content-box',
        }} />
      </div>

      <span style={{
        minWidth: 46, textAlign: 'right', fontVariantNumeric: 'tabular-nums',
        fontWeight: 700, color,
      }}>{item.rate}%</span>
      <span style={{ minWidth: 62, fontSize: 11, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>
        {item.passed}/{item.decided}
      </span>
      {dim
        ? <span style={{ fontSize: 11, color: C.sub, whiteSpace: 'nowrap' }} title={insufficientText}>⚠</span>
        : <span style={{ fontSize: 11, color: C.sub, minWidth: 8 }} />}
    </div>
  );
};

