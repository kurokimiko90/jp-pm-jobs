import React, { useEffect, useState } from 'react';
import { Heartbeat, CheckCircle, ArrowRight } from '@phosphor-icons/react';
import { get, getStats } from '../api';
import { C, scoreColor, riseIn, alpha, getTierLabel } from '../theme';
import { Empty, JobIdentityCell, sourceMeta } from '../components/ui';
import { useLang, useT } from '../i18n';
import './today.css';

const TIER_COLORS: Record<string, string> = {
  ai_startup: C.gold, mega_venture: C.progressIndigo,
  traditional_sier: C.milkTea, unknown: C.sub,
};

/** DB 全空時的首次使用引導（開源訪客 setup.sh 剛跑完的第一眼）。 */
const FirstRunGuide: React.FC<{ t: ReturnType<typeof useT> }> = ({ t }) => (
  <div style={{ ...riseIn, maxWidth: 620, margin: '48px auto', textAlign: 'center' }}>
    <img src="/jobpilot-logo.svg" alt="" width={56} height={56} />
    <h1 style={{ fontSize: 24, margin: '16px 0 8px' }}>{t.today_first_run_title}</h1>
    <p style={{ fontSize: 14, color: C.sub, lineHeight: 1.7, marginBottom: 28 }}>
      {t.today_first_run_desc}
    </p>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, textAlign: 'left' }}>
      <div className="td-panel">
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>{t.today_first_run_demo_title}</div>
        <div style={{ fontSize: 13, color: C.sub, lineHeight: 1.8 }}>{t.today_first_run_demo_body}</div>
      </div>
      <div className="td-panel">
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>{t.today_first_run_real_title}</div>
        <div style={{ fontSize: 13, color: C.sub, lineHeight: 1.8 }}>{t.today_first_run_real_body}</div>
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
    <span style={{ color: C.sub }}>{t.today_demo_body}</span>
  </div>
);

/** Enter / Space で onClick と同じ動作（div に role=button を付けた行用）。 */
const activateOnKey = (fn?: () => void) => (ev: React.KeyboardEvent) => {
  if (fn && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); fn(); }
};

export const Today: React.FC<{
  openJob: (id: number) => void;
  goGenki?: () => void;
  goJobs?: () => void;
  goApplications?: (tab: 'applications' | 'strategy' | 'direct') => void;
  goTailored?: () => void;
}> = ({ openJob, goGenki, goJobs, goApplications, goTailored }) => {
  const t = useT();
  const { lang } = useLang();
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

  // 「いま やること」— 按優先級排列，每項帶 why（reason）+ contextual action label
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
  const todoItems: { id: number; label: string; sub: string; color: string; action: string }[] = [
    // P1: 跟進已投遞
    ...(actions.in_flight || []).slice(0, 2).map((j: any) => ({
      id: j.id, label: j.company || j.title, sub: formatActionReason(j), color: C.errorRed, action: t.today_action_followup,
    })),
    // P2: 投遞包已備未投
    ...(actions.pack_not_applied || []).slice(0, 2).map((j: any) => ({
      id: j.id, label: j.company || j.title, sub: formatActionReason(j), color: C.gold, action: t.today_action_pack_apply,
    })),
    // P3: Go 職缺未備包
    ...(actions.go_no_pack || []).slice(0, 2).map((j: any) => ({
      id: j.id, label: j.company || j.title, sub: formatActionReason(j), color: C.successGreen, action: t.today_action_prepare_pack,
    })),
    // P4: 未跑 gap
    ...(actions.no_gap_count > 0 ? [{
      id: 0,
      label: t.today_gap_pending_title.replace('{count}', String(actions.no_gap_count)),
      sub: t.today_gap_pending_sub,
      color: C.progressIndigo,
      action: t.today_action_gap_pending,
    }] : []),
  ];
  // 聚焦今天最該做的 3 件事；其餘量體用下方「待處理佇列」聚合呈現
  const actionsList = todoItems.slice(0, 3);

  // 待處理佇列：各模組的積壓總量 + 點擊直達
  const queueChips: { count: number; label: string; onClick?: () => void }[] = [
    { count: (actions.in_flight || []).length, label: t.today_queue_followup, onClick: goApplications && (() => goApplications('applications')) },
    { count: (actions.pack_not_applied || []).length, label: t.today_queue_pack, onClick: goTailored },
    { count: (actions.go_no_pack || []).length, label: t.today_queue_go_no_pack, onClick: goJobs },
    { count: actions.direct_pending_count || 0, label: t.today_queue_direct, onClick: goApplications && (() => goApplications('direct')) },
  ].filter((q) => q.count > 0);

  const newJobsList = (actions.new_high_24h || []).slice(0, 5);

  // 漏斗切兩段：「篩選」(抓取→投遞包) 與「應募」(已投遞→offer)。
  // 舊版全部對「抓取」取比例 → 後段永遠 0%，資訊量為零；改成段內對前一階段的轉換率。
  const stageByKey: Record<string, any> = Object.fromEntries(funnel.stages.map((s: any) => [s.key, s]));
  const pick = (keys: string[]) => keys.map((k) => stageByKey[k]).filter(Boolean);
  const funnelSegs = [
    { title: t.today_funnel_seg_discover, stages: pick(['scraped', 'scored', 'gap', 'apply']), tone: C.milkTea },
    { title: t.today_funnel_seg_apply, stages: pick(['applied', 'interview', 'offer']), tone: C.gold },
  ].filter((seg) => seg.stages.length > 0);

  // 企業類型：「未分類」佔九成會把其餘壓扁 → 不畫條，改成一行註記
  const tierKnown = (stats.by_tier || []).filter((x: any) => x.tier !== 'unknown');
  const tierUnknown: number = (stats.by_tier || []).find((x: any) => x.tier === 'unknown')?.n ?? 0;
  const tierMax = Math.max(...tierKnown.map((x: any) => x.n), 1);

  // 分數分布 — bin 是 10 分寬區間的下界
  const binMax = Math.max(...(stats.score_bins || []).map((x: any) => x.n), 1);
  const scoreBars = (stats.score_bins || []).map((b: any) => {
    const lo = typeof b.bin === 'number' ? b.bin : parseInt(String(b.bin).split('-')[0]);
    // 60 未満は「対象外」帯：濃い灰だと視線を奪うので淡色に落とす
    const color = lo >= 60 ? scoreColor(lo) : 'rgb(63 56 48 / 18%)';
    return { label: String(b.bin), count: b.n, pct: (b.n / binMax) * 100, color };
  });

  // 來源：原本 4 張同款大數字卡 + 內部 key（recruiter_agent）→ 改成一條堆疊比例條 + 圖例
  const sources = (stats.by_source || []) as { source: string; n: number }[];
  const sourceTotal = sources.reduce((a, s) => a + s.n, 0) || 1;
  const topSources = sources.slice(0, 5);
  const restSources = sources.slice(5).reduce((a, s) => a + s.n, 0);

  const hour = new Date().getHours();
  const greeting = hour < 11 ? t.today_greet_morning : hour < 18 ? t.today_greet_afternoon : t.today_greet_evening;
  const locale = lang === 'zh' ? 'zh-TW' : lang === 'en' ? 'en-US' : 'ja-JP';
  const dateLine = new Intl.DateTimeFormat(locale, {
    year: 'numeric', month: 'long', day: 'numeric', weekday: 'short',
  }).format(new Date());
  const interviewing: number = stageByKey.interview?.n ?? 0;
  const summary = [
    actionsList.length > 0 ? t.today_summary_todo.replace('{n}', String(actionsList.length)) : t.today_summary_none,
    interviewing > 0 ? t.today_summary_interview.replace('{n}', String(interviewing)) : null,
  ].filter(Boolean).join('　·　');

  return (
    <div className="td" style={riseIn}>
      {stats.demo_only && <DemoBanner t={t} />}

      <header className="td-hero">
        <div>
          <div className="td-eyebrow">{dateLine}</div>
          <h1 className="td-title">{greeting}</h1>
          <p className="td-summary">{summary}</p>
        </div>
        <button type="button" className="td-checkin" onClick={goGenki}>
          <Heartbeat size={18} weight="duotone" color={C.amber} /> {t.today_checkin_button}
        </button>
      </header>

      <div className="td-grid">
        <section className="td-panel" aria-labelledby="td-now">
          <h2 id="td-now" className="td-label">{t.today_action_now}</h2>
          <div className="td-actions">
            {actionsList.length === 0 && (
              <div className="td-empty">
                <CheckCircle size={16} weight="fill" color={C.successGreen} /> {t.today_action_none}
              </div>
            )}
            {actionsList.map((item, i) => {
              const open = item.id > 0 ? () => openJob(item.id) : undefined;
              return (
                <div
                  key={`${item.id}-${i}`}
                  className={`td-action${open ? ' td-action--link' : ''}`}
                  style={{ '--td-accent': item.color } as React.CSSProperties}
                  onClick={open}
                  onKeyDown={activateOnKey(open)}
                  role={open ? 'button' : undefined}
                  tabIndex={open ? 0 : undefined}
                >
                  <span className="td-action-idx num-display">{String(i + 1).padStart(2, '0')}</span>
                  <div className="td-action-body">
                    <div className="td-action-title">{item.label}</div>
                    <div className="td-action-sub">{item.sub}</div>
                  </div>
                  {open && (
                    <span className="td-action-cta">{item.action}<ArrowRight size={13} weight="bold" /></span>
                  )}
                </div>
              );
            })}
          </div>

          {queueChips.length > 0 && (
            <div className="td-queue">
              <div className="td-label td-label--sm">{t.today_queue_title}</div>
              <div className="td-queue-list">
                {queueChips.map((q) => (
                  <button key={q.label} type="button" className="td-chip" onClick={q.onClick} disabled={!q.onClick}>
                    <b className="num-display">{q.count}</b>{q.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>

        <section className="td-panel" aria-labelledby="td-funnel">
          <div className="td-panel-head">
            <h2 id="td-funnel" className="td-label">{t.today_pipeline}</h2>
            {funnel.reply_days_median != null && (
              <span className="td-meta">{t.today_reply_median.replace('{days}', String(funnel.reply_days_median))}</span>
            )}
          </div>
          {funnelSegs.map((seg) => {
            const segMax = Math.max(seg.stages[0].n, 1);
            return (
              <div key={seg.title} className="td-seg">
                <div className="td-seg-title">{seg.title}</div>
                {seg.stages.map((stage: any, i: number) => {
                  const prev = i > 0 ? seg.stages[i - 1].n : null;
                  const conv = prev ? Math.round((stage.n / prev) * 1000) / 10 : null;
                  const w = stage.n > 0 ? Math.max((stage.n / segMax) * 100, 1.5) : 0;
                  return (
                    <div
                      key={stage.key}
                      className="td-stage"
                      title={conv != null ? t.today_funnel_conv.replace('{p}', String(conv)) : undefined}
                    >
                      <span className="td-stage-name">{(t as Record<string, string>)[`funnel_${stage.key}`] ?? stage.label}</span>
                      <span className="td-stage-track">
                        <span className="td-stage-fill" style={{ width: `${w}%`, background: seg.tone }} />
                      </span>
                      <span className="td-stage-n num-display">{stage.n.toLocaleString()}</span>
                      <span className="td-stage-conv">{conv != null ? `${conv}%` : ''}</span>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </section>
      </div>

      <section className="td-panel td-sources" aria-labelledby="td-src">
        <div className="td-panel-head">
          <h2 id="td-src" className="td-label">{t.today_sources}</h2>
          <span className="td-meta">{t.today_sources_total.replace('{n}', sourceTotal.toLocaleString())}</span>
        </div>
        <div
          className="td-src-bar"
          role="img"
          aria-label={topSources.map((s) => `${sourceMeta(s.source).label} ${s.n}`).join(', ')}
        >
          {topSources.map((s) => (
            <span key={s.source} style={{ flexGrow: s.n, background: sourceMeta(s.source).accent }} />
          ))}
          {restSources > 0 && <span style={{ flexGrow: restSources, background: C.milkTea }} />}
        </div>
        <div className="td-src-list">
          {topSources.map((s) => (
            <div key={s.source} className="td-src">
              <span className="td-src-dot" style={{ background: sourceMeta(s.source).accent }} />
              <span className="td-src-name">{sourceMeta(s.source).label}</span>
              <span className="td-src-n num-display">{s.n.toLocaleString()}</span>
              <span className="td-src-pct">{Math.round((s.n / sourceTotal) * 100)}%</span>
            </div>
          ))}
        </div>
      </section>

      <div className="td-grid">
        <section className="td-panel" aria-labelledby="td-new">
          <div className="td-panel-head">
            <h2 id="td-new" className="td-label">{t.today_new_jobs_24h}</h2>
            <button type="button" className="td-link" onClick={goJobs}>{t.today_view_all}</button>
          </div>
          {newJobsList.length === 0 && <div className="td-empty td-empty--rule">{t.today_new_jobs_empty}</div>}
          <ol className="td-jobs">
            {newJobsList.map((j: any, idx: number) => (
              <li
                key={j.id}
                className="td-job"
                onClick={() => openJob(j.id)}
                onKeyDown={activateOnKey(() => openJob(j.id))}
                role="button"
                tabIndex={0}
              >
                <span className="td-job-idx num-display">{idx + 1}</span>
                <JobIdentityCell
                  company={j.company} title={j.title} score={j.score}
                  openworkScore={j.openwork_score} openworkUrl={j.openwork_url}
                  employeeCount={j.employee_count} mentionsAi={j.mentions_ai} langEnv={j.lang_env}
                />
                <span className="td-job-score num-display" style={{ color: scoreColor(j.score) }}>{j.score}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="td-panel" aria-labelledby="td-tier">
          <h2 id="td-tier" className="td-label">{t.today_tier_dist}</h2>
          <div className="td-tiers">
            {tierKnown.map((d: any) => (
              <div key={d.tier} className="td-tier">
                <span className="td-tier-name">{getTierLabel(d.tier)}</span>
                <span className="td-tier-track">
                  <span style={{ width: `${(d.n / tierMax) * 100}%`, background: TIER_COLORS[d.tier] || C.sub }} />
                </span>
                <span className="td-tier-n num-display">{d.n}</span>
              </div>
            ))}
          </div>
          {tierUnknown > 0 && (
            <p className="td-note">
              {t.today_tier_unknown_count.replace('{n}', tierUnknown.toLocaleString())}。{t.today_tier_unknown_note}
            </p>
          )}

          <h2 className="td-label td-label--gap">{t.today_score_dist}</h2>
          <div className="td-hist" role="img" aria-label={scoreBars.map((b: any) => `${b.label}: ${b.count}`).join(', ')}>
            {scoreBars.map((b: any) => (
              <div key={b.label} className="td-hist-col">
                <span className="td-hist-n num-display">{b.count}</span>
                <span className="td-hist-bar" style={{ height: `${Math.max(b.pct, 3)}%`, background: b.color }} />
                <span className="td-hist-x">{b.label}</span>
              </div>
            ))}
          </div>
          <p className="td-note">{t.today_score_dist_note}</p>
        </section>
      </div>
    </div>
  );
};
