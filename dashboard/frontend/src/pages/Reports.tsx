import React, { useEffect, useMemo, useState } from 'react';
import { UserCircle } from '@phosphor-icons/react';
import { get } from '../api';
import { C, recommendColor, severityColor, riseIn, alpha } from '../theme';
import { Badge, Empty, ReportStat, PageHeader, FilterChip, SkeletonRows, ErrorState, JobIdentityCell, useIsNarrow } from '../components/ui';
import { useT } from '../i18n';

interface BatchMeta {
  id: number;
  created_at: string;
  source_filter: string | null;
  min_score: number;
  job_count: number;
}

interface TierItem {
  id: number; company: string; title: string; rec: number; reason: string;
  score?: number | null; employee_count?: number | null; mentions_ai?: number | null;
  openwork_score?: number | null; openwork_url?: string | null;
}
interface CommonGap { theme: string; count: number; severity: string; nature: string; }
interface Action {
  title: string;
  detail: string;
  type?: string;
  roi?: string;
  effort?: string;
  steps?: string[];
  counter_measures?: string[];
  target_job_ids?: number[];
  done_criteria?: string;
}
interface Summary {
  portrait?: string;
  common_gaps?: CommonGap[];
  tiers?: { go?: TierItem[]; improve?: TierItem[]; skip?: TierItem[] };
  actions?: Action[];
  error?: string;
}

// 單筆職缺列（分層投遞用）
const TierRow: React.FC<{ item: TierItem; tone: string; rank?: number; openJob: (id: number) => void }> = ({ item, tone, rank, openJob }) => {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={() => openJob(item.id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex', gap: 12, alignItems: 'center', cursor: 'pointer',
        padding: '9px 12px', borderRadius: 10,
        background: hover ? C.card : C.bg,
        border: `1px solid ${hover ? tone : C.border}`,
        boxShadow: hover ? '0 2px 10px rgba(90,82,72,.10)' : 'none',
        transform: hover ? 'translateY(-1px)' : 'none',
        transition: 'all .15s ease',
      }}
    >
      {/* RankScore 風格：徽章 + 數字 + bar */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 56 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
          {rank != null && (
            <span style={{ fontSize: 11, fontWeight: 700, color: rank <= 3 ? C.gold : C.sub }}>
              {rank <= 3 ? '⭐' : '#'}{rank}
            </span>
          )}
          <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 800, fontSize: 16, color: recommendColor(item.rec) }}>{item.rec}</span>
        </div>
        <div style={{ height: 4, borderRadius: 2, background: C.border, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${item.rec}%`, background: recommendColor(item.rec) }} />
        </div>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <JobIdentityCell
          company={item.company} title={item.title} score={item.score} recommendScore={item.rec}
          openworkScore={item.openwork_score} openworkUrl={item.openwork_url}
          employeeCount={item.employee_count} mentionsAi={item.mentions_ai}
        />
        <div style={{ fontSize: 12, color: C.sub, marginTop: 4 }}>{item.reason}</div>
      </div>
    </div>
  );
};

const TierBlock: React.FC<{ kind: string; items: TierItem[]; openJob: (id: number) => void }> = ({ kind, items, openJob }) => {
  const t = useT();
  const tierMeta: Record<string, { label: string; color: string; hint: string }> = {
    go: { label: t.verdict_go, color: C.successGreen, hint: t.reports_tier_go_hint },
    improve: { label: t.verdict_improve, color: '#C79A1B', hint: t.reports_tier_improve_hint },
    skip: { label: t.verdict_skip, color: C.errorRed, hint: t.reports_tier_skip_hint },
  };
  const m = tierMeta[kind];
  if (!items?.length) return null;
  const emphasize = kind === 'go';
  // go 層內按 rec 排序給 rank
  const ranked = kind === 'go' ? [...items].sort((a, b) => b.rec - a.rec) : items;
  return (
    <div style={{
      marginBottom: 12, borderRadius: 12, padding: '10px 12px',
      background: `${alpha(m.color, 0.05)}`, border: `1px solid ${alpha(m.color, 0.2)}`,
      ...(emphasize ? { boxShadow: `0 0 0 1px ${alpha(m.color, 0.13)} inset` } : {}),
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: m.color, flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 800, color: m.color }}>{m.label}</span>
        <Badge color={m.color}>{items.length}</Badge>
        <span style={{ fontSize: 11, color: C.sub, marginLeft: 'auto' }}>{m.hint}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {ranked.map((it, i) => (
          <TierRow key={it.id} item={it} tone={m.color} rank={emphasize ? i + 1 : undefined} openJob={openJob} />
        ))}
      </div>
    </div>
  );
};

const ROI_COLOR: Record<string, string> = {
  高: C.successGreen, 中: '#C79A1B', 低: C.sub,
  High: C.successGreen, Medium: '#C79A1B', Low: C.sub,
  high: C.successGreen, medium: '#C79A1B', low: C.sub,
};

const ActionCard: React.FC<{ action: Action; index: number; openJob: (id: number) => void }> = ({ action: a, index, openJob }) => {
  const t = useT();
  const [open, setOpen] = useState(index === 0); // 第一條（最高 ROI）預設展開
  const hasDetail = !!(a.steps?.length || a.counter_measures?.length || a.target_job_ids?.length || a.done_criteria);
  return (
    <div style={{ borderRadius: 10, background: C.bg, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
      <div onClick={() => hasDetail && setOpen(!open)}
        style={{ display: 'flex', gap: 12, padding: '11px 12px', cursor: hasDetail ? 'pointer' : 'default' }}>
        <div style={{
          flexShrink: 0, width: 26, height: 26, borderRadius: 8,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: C.gold, color: C.ink, fontWeight: 800, fontSize: 13, fontVariantNumeric: 'tabular-nums',
        }}>{index + 1}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: C.ink, fontWeight: 700 }}>{a.title}</span>
            {a.roi && <Badge color={ROI_COLOR[a.roi] || C.sub}>ROI {a.roi}</Badge>}
            {a.type && <Badge color={C.progressIndigo}>{a.type}</Badge>}
            {a.effort && <span style={{ fontSize: 11, color: C.sub }}>· {a.effort}</span>}
          </div>
          <div style={{ fontSize: 12, color: C.sub, lineHeight: 1.6, marginTop: 3 }}>{a.detail}</div>
        </div>
        {hasDetail && <span style={{ color: C.sub, fontSize: 12, flexShrink: 0 }}>{open ? '▲' : '▼'}</span>}
      </div>

      {open && hasDetail && (
        <div style={{ padding: '0 12px 12px 50px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {!!a.steps?.length && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: C.ink, marginBottom: 4 }}>{t.reports_steps}</div>
              <ol style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 3 }}>
                {a.steps.map((st, i) => <li key={i} style={{ fontSize: 12, color: C.ink, lineHeight: 1.6 }}>{st}</li>)}
              </ol>
            </div>
          )}
          {!!a.counter_measures?.length && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: C.errorRed, marginBottom: 4 }}>{t.reports_counters}</div>
              <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 3 }}>
                {a.counter_measures.map((cm, i) => <li key={i} style={{ fontSize: 12, color: C.ink, lineHeight: 1.6 }}>{cm}</li>)}
              </ul>
            </div>
          )}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {a.done_criteria && (
              <div style={{ flex: '1 1 200px' }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.successGreen, marginBottom: 3 }}>{t.reports_done_when}</div>
                <div style={{ fontSize: 12, color: C.ink, lineHeight: 1.5 }}>{a.done_criteria}</div>
              </div>
            )}
            {!!a.target_job_ids?.length && (
              <div style={{ flex: '1 1 200px' }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.ink, marginBottom: 3 }}>{t.reports_target_jobs}</div>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {a.target_job_ids.map((id) => (
                    <span key={id} onClick={(e) => { e.stopPropagation(); openJob(id); }}
                      style={{
                        fontSize: 11, fontVariantNumeric: 'tabular-nums', cursor: 'pointer',
                        padding: '1px 7px', borderRadius: 999, background: C.card,
                        border: `1px solid ${C.border}`, color: C.progressIndigo,
                      }}>#{id}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export const Reports: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const isNarrow = useIsNarrow();
  const [batches, setBatches] = useState<BatchMeta[] | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [detail, setDetail] = useState<{ meta: BatchMeta; summary: Summary } | null>(null);
  const [tierFilter, setTierFilter] = useState<'all' | 'go' | 'improve' | 'skip'>('all');
  const [error, setError] = useState<string | null>(null);

  const loadBatches = () => {
    setError(null);
    get('/api/gap-batches').then((d) => {
      setBatches(d.items);
      if (d.items.length) setActive(d.items[0].id);
    }).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(loadBatches, []);

  useEffect(() => {
    if (active == null) return;
    setDetail(null);
    get(`/api/gap-batches/${active}`).then((d) =>
      setDetail({ meta: d, summary: d.summary || {} }));
  }, [active]);

  const s = detail?.summary;

  // 分層投遞 KPI：分布 / 平均推薦度 / 被低估數
  const tierStats = useMemo(() => {
    if (!s?.tiers) return null;
    const go = s.tiers.go || [];
    const improve = s.tiers.improve || [];
    const skip = s.tiers.skip || [];
    const all = [...go, ...improve, ...skip];
    const avg = all.length ? Math.round(all.reduce((a, b) => a + b.rec, 0) / all.length) : 0;
    return { go: go.length, improve: improve.length, skip: skip.length, total: all.length, avg };
  }, [s]);

  const gapMax = useMemo(
    () => (s?.common_gaps?.length ? Math.max(...s.common_gaps.map((g) => g.count)) : 0),
    [s]
  );

  if (error) return <ErrorState message={error} onRetry={loadBatches} />;
  if (!batches) return <SkeletonRows cols={['1fr', '1fr', '1fr']} rows={4} />;
  if (!batches.length) return <Empty>{t.reports_no_batch}</Empty>;

  return (
    <div style={riseIn}>
      {/* Title + Batch Selector */}
      <PageHeader
        title={t.reports_page_title}
        right={
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            {batches.slice(0, 5).map((b) => (
              <FilterChip key={b.id} active={active === b.id} onClick={() => setActive(b.id)}>
                #{b.id} · {b.created_at.slice(5, 10)}
              </FilterChip>
            ))}
            {batches.length > 5 && (
              <select
                value={batches.slice(5).some((b) => b.id === active) ? active ?? '' : ''}
                onChange={(e) => e.target.value && setActive(Number(e.target.value))}
                style={{
                  padding: '6px 10px', borderRadius: 999, fontSize: 12,
                  border: `1px solid ${C.border}`, background: C.bg, color: C.ink, cursor: 'pointer',
                }}
              >
                <option value="">{t.reports_more} ({batches.length - 5})</option>
                {batches.slice(5).map((b) => (
                  <option key={b.id} value={b.id}>#{b.id} · {b.created_at.slice(5, 10)}</option>
                ))}
              </select>
            )}
          </div>
        }
      />

      {!detail ? (
        <Empty>{t.reports_loading}</Empty>
      ) : s?.error ? (
        <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '26px 28px' }}>
          <Empty>{t.reports_error}{s.error}</Empty>
        </div>
      ) : (
        <>
          {/* いまの立ち位置 */}
          {s?.portrait && (
            <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '26px 28px', marginBottom: 18, display: 'flex', gap: 18, alignItems: 'flex-start' }}>
              <UserCircle size={44} weight="duotone" color={C.gold} style={{ flexShrink: 0, marginTop: 2 }} />
              <div style={{ minWidth: 0, maxWidth: '110ch' }}>
                <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 8 }}>
                  {t.reports_position_title}
                </div>
                <div style={{ fontSize: 15, fontWeight: 500, lineHeight: 1.85, letterSpacing: 0.5, color: 'rgba(90,82,72,0.9)' }}>
                  {s.portrait}
                </div>
              </div>
            </div>
          )}

          {/* KPI 摘要 + tier 篩選（點 tier 卡過濾左欄榜單） */}
          {tierStats && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
              <ReportStat value={tierStats.total} label={t.reports_analyzed_count} sub={t.reports_count_sub} />
              <ReportStat value={tierStats.avg} label={t.reports_avg_recommend} color={recommendColor(tierStats.avg)} />
              {(['go', 'improve', 'skip'] as const).map((kind) => {
                const m = {
                  go: { label: t.verdict_go, color: C.successGreen, hint: t.reports_tier_go_hint },
                  improve: { label: t.verdict_improve, color: '#C79A1B', hint: t.reports_tier_improve_hint },
                  skip: { label: t.verdict_skip, color: C.errorRed, hint: t.reports_tier_skip_hint },
                }[kind];
                const active = tierFilter === kind;
                return (
                  <button
                    key={kind}
                    onClick={() => setTierFilter(active ? 'all' : kind)}
                    style={{
                      flex: '1 1 120px', minWidth: 120, textAlign: 'left', cursor: 'pointer',
                      background: active ? C.card : C.bg, borderRadius: 10,
                      padding: '12px 14px', position: 'relative', overflow: 'hidden',
                      border: `1px solid ${active ? m.color : C.border}`,
                      boxShadow: active ? `0 6px 18px ${alpha(m.color, 0.13)}` : 'none',
                      transition: 'border-color 140ms ease, box-shadow 140ms ease, background 140ms ease',
                    }}
                    onMouseEnter={(e) => { if (!active) e.currentTarget.style.borderColor = `${alpha(m.color, 0.5)}`; }}
                    onMouseLeave={(e) => { if (!active) e.currentTarget.style.borderColor = C.border; }}
                  >
                    <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, background: m.color, opacity: active ? 1 : 0.5 }} />
                    <div style={{ fontSize: 26, fontWeight: 800, color: m.color, fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 }}>
                      {tierStats[kind]}
                    </div>
                    <div style={{ fontSize: 12, color: C.ink, fontWeight: 700, marginTop: 4 }}>{m.label}</div>
                    <div style={{ fontSize: 11, color: C.sub, marginTop: 1 }}>{m.hint}</div>
                  </button>
                );
              })}
            </div>
          )}
          {tierFilter !== 'all' && (
            <div style={{ marginBottom: 14 }}>
              <button onClick={() => setTierFilter('all')} style={{
                border: `1px solid ${C.border}`, background: C.bg, color: C.sub,
                borderRadius: 999, padding: '5px 14px', fontSize: 12, cursor: 'pointer',
              }}>
                {t.reports_clear_filter.replace('{label}', {
                  go: t.verdict_go,
                  improve: t.verdict_improve,
                  skip: t.verdict_skip,
                }[tierFilter])}
              </button>
            </div>
          )}

          {/* Main content: 左 フィード / 右 サイドバー */}
          <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? 'minmax(0, 1fr)' : 'minmax(0, 1.3fr) minmax(0, 1fr)', gap: 18, alignItems: 'start' }}>
            {/* 左: 分層榜単（TierBlock — rank + 分數條 + 分組標題） */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
              {(['go', 'improve', 'skip'] as const)
                .filter((kind) => tierFilter === 'all' || tierFilter === kind)
                .map((kind) => (
                  <TierBlock key={kind} kind={kind} items={s?.tiers?.[kind] || []} openJob={openJob} />
                ))}
              {(['go', 'improve', 'skip'] as const)
                .filter((kind) => tierFilter === 'all' || tierFilter === kind)
                .every((kind) => !(s?.tiers?.[kind]?.length)) && (
                  <Empty>{t.reports_no_jobs}</Empty>
                )}
            </div>

            {/* 右: サイドバー（sticky — 左欄スクロール時も追従、末尾の余白を解消） */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18, position: 'sticky', top: 20, alignSelf: 'start', minWidth: 0 }}>
              {/* 共通Gap */}
              {!!s?.common_gaps?.length && (
                <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '22px 24px' }}>
                  <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 16 }}>
                    {t.reports_common_gap_title}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                    {s.common_gaps.map((g, i) => {
                      const sevC = severityColor(g.severity);
                      return (
                        <div key={i}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                            <span style={{ width: 4, height: 16, borderRadius: 2, background: sevC, flexShrink: 0 }} />
                            <span style={{ flex: 1, fontSize: 13, fontWeight: 500, letterSpacing: 0.5 }}>
                              {g.theme}
                            </span>
                            <span style={{ fontSize: 13, fontWeight: 800, color: C.gold, fontVariantNumeric: 'tabular-nums' }}>
                              {g.count}
                            </span>
                            <span style={{ fontSize: 11, fontWeight: 700, color: sevC }}>
                              {g.severity}
                            </span>
                          </div>
                          <div style={{ height: 8, borderRadius: 999, background: 'rgba(232,213,183,0.35)', overflow: 'hidden' }}>
                            <div style={{ height: '100%', width: `${(g.count / gapMax) * 100}%`, background: sevC, borderRadius: 999 }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* つぎの一手 */}
              {!!s?.actions?.length && (
                <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '22px 24px' }}>
                  <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 14 }}>
                    {t.reports_next_action}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {s.actions.map((a, i) => (
                      <ActionCard key={i} action={a} index={i} openJob={openJob} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};
