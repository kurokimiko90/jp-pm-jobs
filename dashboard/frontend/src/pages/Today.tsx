import React, { useEffect, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts';
import { Heartbeat, CheckCircle } from '@phosphor-icons/react';
import { get, getStats } from '../api';
import { C, scoreColor, riseIn, alpha, getTierLabel } from '../theme';
import { Card, Badge, Score, SectionTitle, Empty, JobIdentityCell, useIsNarrow } from '../components/ui';
import { useLang, useT } from '../i18n';

const TIER_COLORS: Record<string, string> = {
  ai_startup: C.gold, mega_venture: C.progressIndigo,
  traditional_sier: C.milkTea, unknown: C.sub,
};

const Cmd: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <code style={{
    background: `${alpha(C.border, 0.4)}`, padding: '2px 8px', borderRadius: 6,
    fontSize: 12.5, whiteSpace: 'nowrap',
  }}>{children}</code>
);

/** DB 全空時的首次使用引導（開源訪客 setup.sh 剛跑完的第一眼）。 */
const FirstRunGuide: React.FC<{ t: ReturnType<typeof useT> }> = ({ t }) => (
  <div style={{ ...riseIn, maxWidth: 620, margin: '48px auto', textAlign: 'center' }}>
    <img src="/jobpilot-logo.svg" alt="" width={56} height={56} />
    <h1 style={{ fontSize: 24, margin: '16px 0 8px' }}>{t.today_first_run_title}</h1>
    <p style={{ fontSize: 14, color: C.sub, lineHeight: 1.7, marginBottom: 28 }}>
      {t.today_first_run_desc}
    </p>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, textAlign: 'left' }}>
      <div style={{ background: C.bg, borderRadius: 16, padding: '20px 24px', boxShadow: '0 10px 40px rgba(90,82,72,0.13)' }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>{t.today_first_run_demo_title}</div>
        <div style={{ fontSize: 13, color: C.sub, lineHeight: 1.8 }}>
          {t.today_first_run_demo_body}
        </div>
      </div>
      <div style={{ background: C.bg, borderRadius: 16, padding: '20px 24px', boxShadow: '0 10px 40px rgba(90,82,72,0.13)' }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>{t.today_first_run_real_title}</div>
        <div style={{ fontSize: 13, color: C.sub, lineHeight: 1.8 }}>
          {t.today_first_run_real_body}
        </div>
      </div>
    </div>
  </div>
);

/** demo 資料展示中の常設バナー。 */
const DemoBanner: React.FC<{ t: ReturnType<typeof useT> }> = ({ t }) => (
  <div style={{
    display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
    background: `${alpha(C.gold, 0.1)}`, border: `1px solid ${alpha(C.gold, 0.4)}`, borderRadius: 12,
    padding: '10px 16px', marginBottom: 18, fontSize: 13, lineHeight: 1.7,
  }}>
    <span style={{ fontWeight: 700 }}>{t.today_demo_title}</span>
    <span style={{ color: C.sub }}>
      {t.today_demo_body}
    </span>
  </div>
);

// Helper to get color for score value
export const Today: React.FC<{
  openJob: (id: number) => void;
  goGenki?: () => void;
  goJobs?: () => void;
  goApplications?: (tab: 'applications' | 'strategy' | 'direct') => void;
  goTailored?: () => void;
}> = ({ openJob, goGenki, goJobs, goApplications, goTailored }) => {
  const t = useT();
  const { lang } = useLang();
  const isNarrow = useIsNarrow();
  const [funnel, setFunnel] = useState<any>(null);
  const [actions, setActions] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);

  useEffect(() => {
    get('/api/funnel').then(setFunnel);
    get('/api/actions').then(setActions);
    getStats().then(setStats);
  }, []);

  if (!funnel || !actions || !stats) return <Empty>{t.jobs_loading}</Empty>;
  if (stats.total === 0) return <FirstRunGuide t={t} />;

  // Build actions list (「いま やること」) — 按優先級排列，每項帶 why（reason）+ contextual action label
  const formatActionReason = (j: any): string => {
    switch (j.reason_type) {
      case 'followup_overdue':
        return t.today_reason_followup_overdue.replace('{days}', String(j.reason_days ?? 0));
      case 'followup_due':
        return t.today_reason_followup_due.replace('{days}', String(Math.abs(j.reason_days ?? 0)));
      case 'followup_now':
        return t.today_reason_followup_now;
      case 'pack_ready':
        return t.today_reason_pack_ready.replace('{days}', String(j.reason_days ?? 0));
      case 'go_no_pack':
        return t.today_reason_go_no_pack.replace('{rec}', String(j.reason_rec ?? j.rec ?? '—'));
      default:
        return j.reason || '';
    }
  };
  const todoItems: { id: number; label: string; sub: string; color: string; action: string; company?: string }[] = [];
  // P1: 跟進已投遞
  for (const j of (actions.in_flight || []).slice(0, 2)) {
    todoItems.push({ id: j.id, label: j.company || j.title, sub: formatActionReason(j), color: C.errorRed, action: t.today_action_followup });
  }
  // P2: 投遞包已備未投
  for (const j of (actions.pack_not_applied || []).slice(0, 2)) {
    todoItems.push({ id: j.id, label: j.company || j.title, sub: formatActionReason(j), color: C.gold, action: t.today_action_pack_apply });
  }
  // P3: Go 職缺未備包
  for (const j of (actions.go_no_pack || []).slice(0, 2)) {
    todoItems.push({ id: j.id, label: j.company || j.title, sub: formatActionReason(j), color: C.successGreen, action: t.today_action_prepare_pack });
  }
  // P4: 未跑 gap
  if (actions.no_gap_count > 0) {
    todoItems.push({
      id: 0,
      label: t.today_gap_pending_title.replace('{count}', String(actions.no_gap_count)),
      sub: t.today_gap_pending_sub,
      color: C.progressIndigo,
      action: t.today_action_gap_pending,
    });
  }
  // 聚焦今天最該做的 3 件事；其餘量體用下方「待處理佇列」聚合呈現
  const actionsList = todoItems.slice(0, 3);

  // 待處理佇列：各模組的積壓總量 + 點擊直達
  const queueChips: { count: number; label: string; onClick?: () => void }[] = [
    { count: (actions.in_flight || []).length, label: t.today_queue_followup, onClick: goApplications && (() => goApplications('applications')) },
    { count: (actions.pack_not_applied || []).length, label: t.today_queue_pack, onClick: goTailored },
    { count: (actions.go_no_pack || []).length, label: t.today_queue_go_no_pack, onClick: goJobs },
    { count: actions.direct_pending_count || 0, label: t.today_queue_direct, onClick: goApplications && (() => goApplications('direct')) },
  ].filter((q) => q.count > 0);

  // Build new jobs list (「24h 新着求人」)
  const newJobsList = (actions.new_high_24h || []).slice(0, 5);

  // Build funnel bars with max count
  const funnelMax = Math.max(...funnel.stages.map((s: any) => s.n), 1);

  // Calculate distribution by tier
  const tierDist = stats.by_tier?.map((t: any) => ({
    label: getTierLabel(t.tier),
    count: t.n,
    barStyle: {
      height: '100%',
      width: `${(t.n / Math.max(...(stats.by_tier?.map((x: any) => x.n) || [1])) * 100)}%`,
      background: TIER_COLORS[t.tier] || C.sub,
      borderRadius: '999px',
    }
  })) || [];

  // Build score distribution bars — bin 是 10 分寬區間的下界，標成範圍避免「0」被誤讀為未評分
  const scoreBars = stats.score_bins?.map((b: any) => ({
    label: typeof b.bin === 'number' ? (b.bin >= 100 ? '100' : `${b.bin}–${b.bin + 9}`) : b.bin,
    count: b.n,
    barStyle: {
      width: '100%',
      height: `${Math.max((b.n / Math.max(...(stats.score_bins?.map((x: any) => x.n) || [1])) * 100), 8)}px`,
      background: scoreColor(typeof b.bin === 'number' ? b.bin : parseInt(String(b.bin).split('-')[0])),
      borderRadius: '4px 4px 0 0',
    }
  })) || [];

  return (
    <div style={riseIn}>
      {stats.demo_only && <DemoBanner t={t} />}
      {/* Header: 日付 + 挨拶 + 元気ボタン */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap', marginBottom: 28 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 400, letterSpacing: 2, color: 'rgba(90,82,72,0.55)' }}>
            {new Intl.DateTimeFormat(lang === 'zh' ? 'zh-TW' : lang === 'en' ? 'en-US' : 'ja-JP', {
              year: 'numeric', month: '2-digit', day: '2-digit',
            }).format(new Date())}
          </div>
          <h1 style={{ fontSize: 32, fontWeight: 700, letterSpacing: 2, margin: '6px 0 0' }}>
            {t.today_greeting}
          </h1>
        </div>
        <div
          onClick={goGenki}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') goGenki?.(); }}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 10, height: 46, padding: '0 20px', borderRadius: 999, background: C.bg, boxShadow: '0 6px 22px rgba(90,82,72,0.12)', cursor: 'pointer', fontSize: 14, fontWeight: 700, letterSpacing: '1.5px' }}
        >
          <Heartbeat size={18} weight="regular" color={C.gold} /> {t.today_checkin_button}
        </div>
      </div>

      {/* Grid: 左 いま やること / 右 応募ファネル（窄螢幕收合單欄） */}
      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? '1fr' : '1.35fr 1fr', gap: 18, marginBottom: 18 }}>
        {/* 左: いま やること */}
        <div className="compact-on-phone" style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '26px 28px' }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 16 }}>
            {t.today_action_now}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {actionsList.length === 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: 16, fontSize: 13, color: 'rgba(90,82,72,0.5)', letterSpacing: 1 }}>
                <CheckCircle size={16} weight="fill" color={C.successGreen} /> {t.today_action_none}
              </div>
            )}
            {actionsList.map((item, i) => (
              <div
                className="today-action-row"
                key={`${item.id}-${i}`}
                onClick={() => item.id > 0 && openJob(item.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 14, padding: '14px 16px', borderRadius: 16,
                  background: 'rgba(232,213,183,0.26)', cursor: item.id > 0 ? 'pointer' : 'default',
                }}
              >
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: item.color, flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 500, letterSpacing: 1 }}>{item.label}</div>
                  <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.55)', marginTop: 2 }}>{item.sub}</div>
                </div>
                {item.id > 0 && (
                  <span className="today-action-cta" style={{
                    fontSize: 12, fontWeight: 700, letterSpacing: 0.5, color: C.ink, whiteSpace: 'nowrap',
                    padding: '5px 12px', borderRadius: 999, border: `1px solid ${C.border}`, flexShrink: 0,
                  }}>
                    {item.action}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* 待處理佇列：積壓總量一眼看到，點擊直達對應模組 */}
          {queueChips.length > 0 && (
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px dashed rgba(232,213,183,0.8)' }}>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.45)', marginBottom: 10 }}>
                {t.today_queue_title}
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {queueChips.map((q) => (
                  <span
                    key={q.label}
                    onClick={q.onClick}
                    role={q.onClick ? 'button' : undefined}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                      borderRadius: 999, border: `1px solid ${C.border}`, fontSize: 12, fontWeight: 500,
                      color: 'rgba(90,82,72,0.75)', cursor: q.onClick ? 'pointer' : 'default', whiteSpace: 'nowrap',
                    }}
                  >
                    <b style={{ color: C.gold, fontVariantNumeric: 'tabular-nums' }}>{q.count}</b> {q.label}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 右: 応募ファネル */}
        <div className="compact-on-phone" style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '26px 28px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)' }}>
              {t.today_pipeline}
            </div>
            {funnel.reply_days_median != null && (
              <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.5)' }}>
                {t.today_reply_median.replace('{days}', String(funnel.reply_days_median)).split(String(funnel.reply_days_median))[0]}<b style={{ color: C.ink }}>{funnel.reply_days_median}</b>{t.today_reply_median.replace('{days}', String(funnel.reply_days_median)).split(String(funnel.reply_days_median))[1]}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {funnel.stages.map((stage: any) => {
              const percent = (stage.n / funnelMax) * 100;
              return (
                <div key={stage.key} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ width: 90, fontSize: 13, fontWeight: 500, letterSpacing: 1, color: 'rgba(90,82,72,0.7)', flexShrink: 0 }}>
                    {(t as Record<string, string>)[`funnel_${stage.key}`] ?? stage.label}
                  </span>
                  <div style={{ flex: 1, height: 26, borderRadius: 999, background: 'rgba(232,213,183,0.35)', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${percent}%`, background: C.gold, borderRadius: 999 }} />
                  </div>
                  <span style={{ width: 34, textAlign: 'right', fontSize: 18, fontWeight: 800, color: C.gold, fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                    {stage.n}
                  </span>
                  <span style={{ width: 34, textAlign: 'right', fontSize: 11, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                    {stage.pct != null ? `${stage.pct}%` : ''}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Stats row */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        {stats.by_source && stats.by_source.slice(0, 4).map((s: any) => (
          <div key={s.source} style={{ flex: 1, minWidth: 150, background: C.bg, borderRadius: 22, boxShadow: '0 4px 16px rgba(90,82,72,0.1)', padding: '18px 22px' }}>
            <div style={{ fontSize: 30, fontWeight: 800, color: C.gold, fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>
              {s.n}
            </div>
            <div style={{ fontSize: 13, fontWeight: 400, letterSpacing: '1.5px', color: 'rgba(90,82,72,0.6)', marginTop: 6 }}>
              {s.source}
            </div>
          </div>
        ))}
      </div>

      {/* Grid: 左 新着求人 / 右 分布（窄螢幕收合單欄） */}
      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? '1fr' : '1.35fr 1fr', gap: 18 }}>
        {/* 左: 新着求人リスト */}
        <div className="compact-on-phone" style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '24px 26px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)' }}>
              {t.today_new_jobs_24h}
            </div>
            <span
              onClick={goJobs}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') goJobs?.(); }}
              style={{ fontSize: 13, fontWeight: 700, letterSpacing: 1, color: 'rgba(90,82,72,0.5)', cursor: 'pointer' }}
            >
              {t.today_view_all}
            </span>
          </div>
          <div>
            {newJobsList.length === 0 && (
              <div style={{ padding: '28px 6px', fontSize: 13, color: 'rgba(90,82,72,0.55)', letterSpacing: 0.5, borderTop: '1px dashed rgba(232,213,183,0.8)' }}>
                {t.today_new_jobs_empty}
              </div>
            )}
            {newJobsList.map((j: any, idx: number) => (
              <div
                key={j.id}
                onClick={() => openJob(j.id)}
                style={{
                  display: 'grid', gridTemplateColumns: '24px 1fr auto', gap: 14, alignItems: 'center',
                  padding: '13px 6px', borderTop: '1px dashed rgba(232,213,183,0.8)', cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                  {idx + 1}
                </span>
                <JobIdentityCell
                  company={j.company} title={j.title} score={j.score}
                  openworkScore={j.openwork_score} openworkUrl={j.openwork_url}
                  employeeCount={j.employee_count} mentionsAi={j.mentions_ai}
                />
                <span style={{ fontSize: 18, fontWeight: 800, color: C.gold, fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                  {j.score}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* 右: 分布（企業タイプ + スコア） */}
        <div className="compact-on-phone" style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '24px 26px' }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 16 }}>
            {t.today_tier_dist}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 10 }}>
            {tierDist.map((d: any, i: number) => (
              <div key={i}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 500, letterSpacing: 1, marginBottom: 6 }}>
                  <span>{d.label}</span>
                  <span style={{ fontWeight: 700, color: 'rgba(90,82,72,0.55)' }}>{d.count}</span>
                </div>
                <div style={{ height: 10, borderRadius: 999, background: 'rgba(232,213,183,0.35)', overflow: 'hidden' }}>
                  <div style={d.barStyle} />
                </div>
              </div>
            ))}
          </div>
          {(stats.by_tier || []).some((x: any) => x.tier === 'unknown' && x.n > 0) && (
            <div style={{ fontSize: 11, color: 'rgba(90,82,72,0.5)', lineHeight: 1.6, marginBottom: 20 }}>
              {t.today_tier_unknown_note}
            </div>
          )}

          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 4 }}>
            {t.today_score_dist}
          </div>
          <div style={{ fontSize: 11, color: 'rgba(90,82,72,0.5)', lineHeight: 1.6, marginBottom: 12 }}>
            {t.today_score_dist_note}
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, height: 96 }}>
            {scoreBars.map((b: any, i: number) => (
              <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, height: '100%', justifyContent: 'flex-end' }}>
                <span style={{ fontSize: 13, fontWeight: 800, color: C.gold, fontVariantNumeric: 'tabular-nums' }}>
                  {b.count}
                </span>
                <div style={b.barStyle} />
                <span style={{ fontSize: 9, fontWeight: 400, color: 'rgba(90,82,72,0.5)', whiteSpace: 'nowrap' }}>
                  {b.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
