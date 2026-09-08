import React, { useEffect, useRef, useState } from 'react';
import { get, post } from '../api';
import { C, recommendColor, scoreColor, relativeDate, softCard, riseIn, alpha } from '../theme';
import { Empty, Pill, ReportStat, StageDots, PageHeader, Pager, SkeletonRows, ErrorState, STAGE_ORDER, SortTh, PanelActions, JobIdentityCell } from '../components/ui';
import { FilterBar } from '../components/FilterBar';
import { StatusTabs } from '../components/StatusTabs';
import { useHashFilters } from '../filters/useHashFilters';
import { STAGE_TABS } from '../filters/dict';
import { useT } from '../i18n';

interface AppItem {
  job_id: number;
  status: string;
  applied_at: string;
  last_updated: string;
  next_event: string | null;
  notes: string | null;
  title: string;
  company: string;
  score: number | null;
  tier: string | null;
  source: string;
  url: string;
  posting_type: string | null;
  channel: string | null;
  recommend_score: number | null;
  employee_count: number | null;
  mentions_ai: number | null;
  openwork_score: number | null;
  openwork_url: string | null;
  days_since_update: number;
  is_overdue: boolean;
  rejection_stage: string | null;
  rejection_reason: string | null;
  history: { status: string; changed_at: string }[];
}

interface ListResp {
  items: AppItem[];
  counts: Record<string, number>;
  total: number;
  replied: number;
  offers: number;
}

const COLS = '30px 52px minmax(240px,2.4fr) 56px 56px 72px 190px 92px minmax(130px,1.3fr)';

/** 応募経路徽章：direct（官網直投）金色高亮，其餘灰階顯示來源名。 */
const ChannelBadge: React.FC<{ channel: string | null; directLabel: string }> = ({ channel, directLabel }) => {
  if (!channel) return <span style={{ fontSize: 11, color: 'rgba(90,82,72,0.3)' }}>—</span>;
  const isDirect = channel === 'direct';
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, whiteSpace: 'nowrap', borderRadius: 999,
      padding: '3px 8px', justifySelf: 'start',
      color: isDirect ? '#8A6D00' : 'rgba(90,82,72,0.65)',
      background: isDirect ? 'rgba(245,200,66,0.25)' : 'rgba(90,82,72,0.08)',
    }}>
      {isDirect ? directLabel : channel}
    </span>
  );
};
const PAGE_SIZE = 25;

export const Applications: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const STAGES: [string, string][] = [
    ['applied', t.status_applied], ['casual', t.status_casual], ['recruiter', t.status_recruiter],
    ['tech', t.status_tech], ['onsite', t.status_onsite], ['offer', t.status_offer], ['rejected', t.status_rejected],
  ];
  const REJECTION_STAGES: [string, string][] = [
    ['shorui', t.rejection_stage_shorui], ['casual', t.rejection_stage_casual],
    ['ichiji', t.rejection_stage_ichiji], ['niji', t.rejection_stage_niji], ['saishu', t.rejection_stage_saishu],
  ];
  const stageLabel = (s: string) => STAGES.find(([k]) => k === s)?.[1] ?? s;
  // rejected 時實際走到的最遠階段：歷程與落選階段標記取較遠者（回填舊資料只有落選標記可考）
  const REJECTION_STAGE_REACHED: Record<string, string> = {
    shorui: 'applied', casual: 'casual', ichiji: 'recruiter', niji: 'tech', saishu: 'onsite',
  };
  const reachedStage = (it: AppItem): string | undefined => {
    const passed = (it.history ?? []).filter((h) => STAGE_ORDER.includes(h.status));
    const fromHist = passed.length ? passed[passed.length - 1].status : undefined;
    const fromRej = REJECTION_STAGE_REACHED[it.rejection_stage ?? ''];
    const hi = STAGE_ORDER.indexOf(fromHist ?? '');
    const ri = STAGE_ORDER.indexOf(fromRej ?? '');
    return STAGE_ORDER[Math.max(hi, ri)];
  };
  const REJECTION_REASONS: [string, string][] = [
    ['experience', t.rejection_reason_experience], ['language', t.rejection_reason_language], ['age', t.rejection_reason_age],
    ['salary', t.rejection_reason_salary], ['unspecified', t.rejection_reason_unspecified],
  ];
  const [data, setData] = useState<ListResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [eventDraft, setEventDraft] = useState('');
  const [focusIdx, setFocusIdx] = useState(0);
  const [rejectPrompt, setRejectPrompt] = useState<{ jobId: number; stage: string; reason: string } | null>(null);
  const [f, updateF, resetF, dirty] = useHashFilters({
    tab: '', q: '', min: 0, max: 100, channel: '', sort: '', order: 'desc',
  });
  const listRef = useRef<HTMLDivElement>(null);

  const resetView = () => { setPage(1); setFocusIdx(0); setExpanded(null); };
  const setF = (patch: Parameters<typeof updateF>[0]) => { updateF(patch); resetView(); };
  // 3段階トグル: 未選択→降順→昇順→未選択（デフォルト順）に戻る
  const clickSort = (col: string) => {
    if (f.sort !== col) setF({ sort: col, order: 'desc' });
    else if (f.order === 'desc') setF({ order: 'asc' });
    else setF({ sort: '' });
  };
  const ord: 'asc' | 'desc' = f.order === 'asc' ? 'asc' : 'desc';

  const load = () => {
    setError(null);
    get('/api/applications/list').then(setData).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(() => { load(); }, []);

  const setStatus = async (
    jobId: number, status: string, nextEvent: string,
    rejection?: { stage: string; reason: string },
  ) => {
    await post('/api/applications', {
      job_id: jobId, status, next_event: nextEvent,
      rejection_stage: rejection?.stage, rejection_reason: rejection?.reason,
    });
    load();
  };

  const remove = async (jobId: number) => {
    if (!confirm(t.applications_delete_confirm)) return;
    await fetch(`/api/applications/${jobId}`, { method: 'DELETE' });
    setExpanded(null);
    load();
  };

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (data === null) return <SkeletonRows cols={[30, 40, 52, '2.4fr', 56, 56, 72, 190, 92, '1.3fr']} />;
  if (data.total === 0) return <Empty>{t.applications_empty}</Empty>;

  // '_overdue' 是跨 stage 的特殊 tab（篩逾期跟進），與 stage tab 互斥
  let filtered = f.tab === '_overdue'
    ? data.items.filter((it) => it.is_overdue)
    : f.tab ? data.items.filter((it) => it.status === f.tab) : data.items;
  if (f.min > 0) filtered = filtered.filter((it) => (it.recommend_score ?? -1) >= f.min);
  if (f.max < 100) filtered = filtered.filter((it) => (it.recommend_score ?? 101) <= f.max);
  if (f.channel) filtered = filtered.filter((it) => (it.channel ?? '') === f.channel);
  const q = f.q.trim().toLowerCase();
  if (q) {
    filtered = filtered.filter((it) =>
      `${it.company} ${it.title} ${it.job_id}`.toLowerCase().includes(q));
  }
  const cmp = (a: AppItem, b: AppItem): number => {
    const dir = f.order === 'desc' ? -1 : 1;
    switch (f.sort) {
      case 'applied_at': return dir * a.applied_at.localeCompare(b.applied_at);
      case 'last_updated': return dir * a.last_updated.localeCompare(b.last_updated);
      case 'company': return dir * a.company.localeCompare(b.company, 'ja');
      case 'score':
      case 'recommend_score': {
        const av = a[f.sort as 'score' | 'recommend_score']; const bv = b[f.sort as 'score' | 'recommend_score'];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        return dir * (av - bv);
      }
      default: return 0;
    }
  };
  const sorted = f.sort ? [...filtered].sort(cmp) : filtered;
  const totalFiltered = sorted.length;
  const items = sorted.slice((page - 1) * pageSize, page * pageSize);
  const overdue = data.items.filter((it) => it.is_overdue).length;
  const channels = [...new Set(data.items.map((it) => it.channel).filter((c): c is string => !!c))].sort();

  const onListKey = (e: React.KeyboardEvent) => {
    const tgt = e.target as HTMLElement;
    if (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA') return;
    if (e.key === 'j') { e.preventDefault(); setFocusIdx((i) => Math.min(i + 1, items.length - 1)); }
    else if (e.key === 'k') { e.preventDefault(); setFocusIdx((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && items[focusIdx]) { e.preventDefault(); openJob(items[focusIdx].job_id); }
  };

  return (
    <div style={riseIn}>
      <PageHeader title={t.applications_title} subtitle={t.applications_subtitle} />

      {/* KPI strip（純展示，不可點；篩選一律走下方 tabs / 篩選列） */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <ReportStat value={data.total} label={t.applications_kpi_applied} sub={t.applications_kpi_total_sub} color={C.ink} />
        <ReportStat value={data.replied} label={t.applications_kpi_replied} sub={t.applications_kpi_replied_sub} color={C.progressIndigo} />
        <ReportStat
          value={overdue} label={t.applications_kpi_overdue} sub={t.applications_kpi_overdue_sub}
          color={overdue > 0 ? C.errorRed : C.sub}
        />
        <ReportStat value={data.offers} label={t.applications_kpi_offers} sub={t.applications_kpi_offers_sub} color={C.doneGold} />
      </div>

      {/* 狀態層 tabs（stage 漏斗 + 跨 stage 的逾期跟進） */}
      <StatusTabs
        tabs={[
          { key: '', label: t.applications_filter_all, count: data.total },
          ...STAGE_TABS.map((tb) => ({
            key: tb.key, label: t[tb.tKey], color: tb.color, count: data.counts[tb.key] || 0,
          })),
          { key: '_overdue', label: t.applications_kpi_overdue, color: C.errorRed, count: overdue },
        ]}
        value={f.tab}
        onChange={(k) => setF({ tab: k })}
        hideEmpty
      />

      {/* 統一篩選列：搜尋 → 推薦度 → 來源 */}
      <FilterBar
        search={{ value: f.q, onChange: (v) => setF({ q: v }), placeholder: t.applications_search_placeholder }}
        score={{ label: t.jobs_sort_recommend, min: f.min, max: f.max, onChange: (min, max) => setF({ min, max }) }}
        channel={channels.length > 0 ? {
          label: t.applications_col_channel,
          options: [
            { key: '', label: t.jobs_all },
            ...channels.map((c) => ({ key: c, label: c === 'direct' ? t.channel_direct : c })),
          ],
          value: f.channel, onChange: (v) => setF({ channel: v }),
        } : undefined}
        dirty={dirty}
        onClear={() => { resetF(); resetView(); }}
      />

      {/* Table */}
      <div className="data-table-scroll" ref={listRef} tabIndex={0} onKeyDown={onListKey} style={{ ...softCard, outline: 'none' }}>
        <div style={{
          display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center', padding: '14px 26px', minWidth: 'min-content',
          borderBottom: '1.5px solid rgba(232,213,183,0.7)',
          fontSize: 12, fontWeight: 400, letterSpacing: 2, color: 'rgba(90,82,72,0.5)',
        }}>
          <div>#</div>
          <div>ID</div>
          <SortTh active={f.sort === 'company'} order={ord} onClick={() => clickSort('company')}>{t.applications_col_company_role}</SortTh>
          <SortTh active={f.sort === 'score'} order={ord} onClick={() => clickSort('score')}>
            <span title={t.score_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.applications_col_score}</span>
          </SortTh>
          <SortTh active={f.sort === 'recommend_score'} order={ord} onClick={() => clickSort('recommend_score')}>
            <span title={t.recommend_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.applications_col_recommend}</span>
          </SortTh>
          <div>{t.applications_col_channel}</div>
          <div>{t.applications_col_stage}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <SortTh active={f.sort === 'applied_at'} order={ord} onClick={() => clickSort('applied_at')}>{t.applications_col_applied_date}</SortTh>
            <SortTh active={f.sort === 'last_updated'} order={ord} onClick={() => clickSort('last_updated')}>{t.sort_last_updated}</SortTh>
          </div>
          <div>{t.applications_col_next_event}</div>
        </div>

        {items.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', fontSize: 14, color: 'rgba(90,82,72,0.6)', letterSpacing: 2 }}>
            {t.applications_none}
          </div>
        ) : (
          items.map((it, idx) => {
            const isRej = it.status === 'rejected';
            const open = expanded === it.job_id;
            const seqNo = (page - 1) * pageSize + idx + 1;
            return (
              <div key={it.job_id} style={{ borderBottom: '1px solid rgba(232,213,183,0.5)', opacity: isRej ? 0.6 : 1 }}>
                <div
                  onClick={() => { setExpanded(open ? null : it.job_id); setEventDraft(it.next_event || ''); setFocusIdx(idx); }}
                  style={{
                    display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center',
                    padding: '14px 26px', cursor: 'pointer', minWidth: 'min-content',
                    background: open ? C.card : undefined,
                    boxShadow: idx === focusIdx
                      ? `inset 3px 0 0 ${C.gold}, inset 0 0 0 1.5px ${alpha(C.gold, 0.33)}`
                      : `inset 3px 0 0 ${isRej ? C.draftGray : it.status === 'offer' ? C.doneGold : C.appliedInk}`,
                  }}
                  onMouseEnter={(e) => { if (!open) e.currentTarget.style.background = C.card; }}
                  onMouseLeave={(e) => { if (!open) e.currentTarget.style.background = ''; }}
                >
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                    {seqNo}
                  </span>

                  <span style={{ fontSize: 11, fontWeight: 600, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                    {it.job_id}
                  </span>

                  <JobIdentityCell
                    company={it.company} title={it.title} score={it.score} recommendScore={it.recommend_score}
                    openworkScore={it.openwork_score} openworkUrl={it.openwork_url}
                    employeeCount={it.employee_count} mentionsAi={it.mentions_ai}
                  />

                  {/* 評分（規則分）降為次要字級，推薦度（LLM 判定）是決策主數字 */}
                  <span style={{ fontSize: 13, fontWeight: 700, color: scoreColor(it.score), fontVariantNumeric: 'tabular-nums' }}>
                    {it.score ?? '—'}
                  </span>

                  <span style={{ fontSize: 18, fontWeight: 800, color: recommendColor(it.recommend_score), fontVariantNumeric: 'tabular-nums' }}>
                    {it.recommend_score ?? '—'}
                  </span>

                  <ChannelBadge channel={it.channel} directLabel={t.channel_direct} />

                  <StageDots status={it.status} reached={isRej ? reachedStage(it) : undefined} />

                  <div style={{ fontSize: 12, fontWeight: 500, color: 'rgba(90,82,72,0.6)' }} title={it.applied_at}>
                    {relativeDate(it.applied_at)}
                    {it.is_overdue && (
                      <span style={{
                        display: 'block', marginTop: 3, fontSize: 10, fontWeight: 700, color: '#C79A1B',
                        whiteSpace: 'nowrap',
                      }}>
                        {t.applications_stale_days.replace('{days}', String(it.days_since_update))}
                      </span>
                    )}
                  </div>

                  <div style={{ fontSize: 12, fontWeight: 500, color: it.next_event ? C.ink : 'rgba(90,82,72,0.3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={it.next_event || ''}>
                    {it.next_event || '—'}
                  </div>
                </div>

                {/* Expanded editor */}
                {open && (
                  <div style={{ padding: '4px 26px 18px', background: C.card }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: C.sub, margin: '4px 0 8px', letterSpacing: 1 }}>
                      {t.applications_update_stage}
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                      {STAGES.map(([s, label]) => (
                        <Pill
                          key={s}
                          active={it.status === s}
                          onClick={() => {
                            if (s === 'rejected' && it.status !== 'rejected') {
                              setRejectPrompt({ jobId: it.job_id, stage: '', reason: '' });
                            } else {
                              setStatus(it.job_id, s, eventDraft);
                            }
                          }}
                          style={s === 'rejected' && it.status !== 'rejected' ? { color: C.sub } : undefined}
                        >
                          {label}
                        </Pill>
                      ))}
                    </div>

                    {rejectPrompt?.jobId === it.job_id && (
                      <div style={{
                        marginBottom: 12, padding: '10px 14px', borderRadius: 12,
                        background: `${alpha(C.errorRed, 0.05)}`, border: `1px solid ${alpha(C.errorRed, 0.2)}`,
                      }}>
                        <div style={{ fontSize: 12, fontWeight: 700, color: C.sub, marginBottom: 8 }}>
                          {t.applications_reject_prompt}
                        </div>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                          {REJECTION_STAGES.map(([k, label]) => (
                            <Pill key={k} active={rejectPrompt.stage === k}
                              onClick={() => setRejectPrompt({ ...rejectPrompt, stage: k })}>
                              {label}
                            </Pill>
                          ))}
                        </div>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                          {REJECTION_REASONS.map(([k, label]) => (
                            <Pill key={k} active={rejectPrompt.reason === k}
                              onClick={() => setRejectPrompt({ ...rejectPrompt, reason: k })}>
                              {label}
                            </Pill>
                          ))}
                        </div>
                        <div style={{ display: 'flex', gap: 8 }}>
                          <Pill
                            onClick={() => {
                              if (!rejectPrompt.stage) return;
                              setStatus(it.job_id, 'rejected', eventDraft, rejectPrompt);
                              setRejectPrompt(null);
                            }}
                            style={rejectPrompt.stage ? undefined : { opacity: 0.4, pointerEvents: 'none' }}
                          >
                            {t.applications_confirm}
                          </Pill>
                          <Pill onClick={() => setRejectPrompt(null)}>{t.applications_cancel}</Pill>
                        </div>
                      </div>
                    )}

                    <input
                      value={eventDraft}
                      onChange={(e) => setEventDraft(e.target.value)}
                      onBlur={() => { if (eventDraft !== (it.next_event || '')) setStatus(it.job_id, it.status, eventDraft); }}
                      onKeyDown={(e) => { if (e.key === 'Enter') setStatus(it.job_id, it.status, eventDraft); }}
                      placeholder={t.applications_next_event_placeholder}
                      style={{
                        width: '100%', maxWidth: 420, borderRadius: 999, border: `1px solid ${C.border}`,
                        padding: '7px 14px', fontSize: 12, background: C.bg, color: C.ink, outline: 'none',
                      }}
                    />
                    <div style={{ marginTop: 12 }}>
                      <PanelActions
                        primary={[{ label: t.applications_open_detail, onClick: () => openJob(it.job_id) }]}
                        links={[{ label: t.applications_open_jd, onClick: () => window.open(it.url, '_blank') }]}
                        right={[{ label: t.applications_delete_record, onClick: () => remove(it.job_id), tone: 'danger' }]}
                      />
                    </div>
                    {it.history?.length > 0 && (
                      <div style={{
                        display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center',
                        marginTop: 12, fontSize: 11, color: 'rgba(90,82,72,0.65)',
                      }}>
                        <span style={{ fontWeight: 700, letterSpacing: 1 }}>{t.applications_history_label}</span>
                        {it.history.map((h, i) => (
                          <React.Fragment key={i}>
                            {i > 0 && <span style={{ opacity: 0.4 }}>→</span>}
                            <span style={{ whiteSpace: 'nowrap', fontWeight: h.status === 'rejected' ? 700 : 500, color: h.status === 'rejected' ? C.errorRed : undefined }}>
                              {stageLabel(h.status)}
                              <span style={{ opacity: 0.6, marginLeft: 3, fontWeight: 500 }}>{h.changed_at.slice(5)}</span>
                            </span>
                          </React.Fragment>
                        ))}
                        {isRej && it.rejection_stage && (
                          <span style={{ color: C.errorRed }}>
                            ({REJECTION_STAGES.find(([k]) => k === it.rejection_stage)?.[1] ?? it.rejection_stage})
                          </span>
                        )}
                      </div>
                    )}
                    <div style={{ fontSize: 11, color: 'rgba(90,82,72,0.5)', marginTop: 10 }}>
                      {t.applications_applied_meta.replace('{applied}', it.applied_at).replace('{updated}', it.last_updated)}
                      {it.notes ? ` ・ ${it.notes}` : ''}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
        <Pager
          page={page}
          total={totalFiltered}
          size={pageSize}
          onPage={(p) => { setPage(p); setExpanded(null); setFocusIdx(0); }}
          onSize={(s) => { setPageSize(s); setPage(1); setExpanded(null); setFocusIdx(0); }}
          sizeOptions={[10, 25, 50]}
        />
      </div>
    </div>
  );
};
