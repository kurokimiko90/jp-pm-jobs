import React, { useEffect, useState } from 'react';
import { get, batchOpenJds } from '../api';
import { C, getTierLabel, recommendColor, softCard, riseIn, alpha } from '../theme';
import { Empty, Pager, SourceLogo, PageHeader, SkeletonRows, ErrorState, SortTh, FilterChip, JobIdentityCell, SalaryCell, AppStatusBadge } from '../components/ui';
import { FilterBar, ActionBar, ActionButton } from '../components/FilterBar';
import { StatusTabs } from '../components/StatusTabs';
import { useHashFilters } from '../filters/useHashFilters';
import { REGION_OPTIONS, DAYS_OPTIONS, ROLE_OPTIONS, TIER_KEYS, VERDICT_TABS } from '../filters/dict';
import { useT } from '../i18n';

interface RecJob {
  id: number;
  title: string;
  company: string;
  tier: string;
  score: number | null;
  recommend_score: number;
  posting_type: string | null;
  location: string | null;
  source: string;
  verdict: 'go' | 'improve' | 'skip';
  reason: string | null;
  openwork_score: number | null;
  openwork_url: string | null;
  salary_min: number | null;
  salary_max: number | null;
  employee_count: number | null;
  mentions_ai: number | null;
  app_status: string | null;
  company_app_status: string | null;
  company_applied_job_id: number | null;
}

type VerdictFilter = 'all' | 'go' | 'improve' | 'skip';

const MEDAL = ['#D9A41A', '#A8A29A', '#B07A3C']; // 金 / 銀 / 銅

const COLS = '30px 40px 56px minmax(260px,2fr) 100px 130px 100px 80px minmax(120px,1fr)';

const FILTER_DEFAULTS = {
  tab: 'all', q: '', loc: '', role: '', src: '', post: '', applied: '',
  min: 0, max: 100, days: 0, tiers: [] as string[], closed: false,
  sort: 'recommend_score', order: 'desc',
};

export const Recommend: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const verdictMeta: Record<Exclude<VerdictFilter, 'all'>, { label: string; color: string }> = {
    go: { label: t.verdict_go, color: C.successGreen },
    improve: { label: t.verdict_improve, color: '#C79A1B' },
    skip: { label: t.verdict_skip, color: C.sub },
  };
  const [items, setItems] = useState<RecJob[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [f, updateF, resetF, dirty] = useHashFilters(FILTER_DEFAULTS);
  const setF = (patch: Parameters<typeof updateF>[0]) => { updateF(patch); setPage(1); };

  const [error, setError] = useState<string | null>(null);

  const fetchData = () => {
    setError(null);
    const params = new URLSearchParams({
      page: String(page), size: String(pageSize),
    });
    if (f.tab !== 'all') params.set('verdict', f.tab);
    if (f.q.trim()) params.set('q', f.q.trim());
    if (f.loc) params.set('loc', f.loc);
    if (f.src) params.set('source', f.src);
    if (f.post) params.set('posting_type', f.post);
    if (f.role) params.set('job_type', f.role);
    if (f.applied) params.set('applied_filter', f.applied);
    if (f.min > 0) params.set('min_score', String(f.min));
    if (f.max < 100) params.set('max_score', String(f.max));
    if (f.days > 0) params.set('days', String(f.days));
    if (f.tiers.length > 0) params.set('tier', f.tiers.join(','));
    if (f.closed) params.set('show_closed', 'true');
    params.set('sort', f.sort);
    params.set('order', f.order);
    get(`/api/recommend-jobs?${params}`).then((d) => {
      setItems(d.items || []);
      setTotal(d.total);
    }).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  useEffect(fetchData, [page, pageSize, f]);

  const clickSort = (col: string) => {
    if (f.sort === col) setF({ order: f.order === 'desc' ? 'asc' : 'desc' });
    else setF({ sort: col, order: 'desc' });
  };
  const ord: 'asc' | 'desc' = f.order === 'asc' ? 'asc' : 'desc';

  // counts per verdict (separate lightweight queries)
  const [counts, setCounts] = useState({ all: 0, go: 0, improve: 0, skip: 0 });
  useEffect(() => {
    Promise.all([
      get('/api/recommend-jobs?size=1'),
      get('/api/recommend-jobs?size=1&verdict=go'),
      get('/api/recommend-jobs?size=1&verdict=improve'),
      get('/api/recommend-jobs?size=1&verdict=skip'),
    ]).then(([a, g, i, s]) => {
      setCounts({ all: a.total, go: g.total, improve: i.total, skip: s.total });
    });
  }, []);

  if (error) return <ErrorState message={error} onRetry={fetchData} />;
  if (items === null) return <SkeletonRows cols={[30, 40, 56, '2fr', 100, 130, 100, 80, '1fr']} />;
  if (counts.all === 0 && items.length === 0) return <Empty>{t.reports_no_batch}</Empty>;

  return (
    <div style={riseIn}>
      <PageHeader title={t.nav_recommend} subtitle={t.recommend_subtitle} />

      {/* 狀態層 tabs（獨立一行，取代統計卡兼篩選） */}
      <StatusTabs
        tabs={[
          { key: 'all', label: t.recommend_card_all, count: counts.all },
          ...VERDICT_TABS.map((tb) => ({
            key: tb.key, label: t[tb.tKey], color: tb.color,
            count: counts[tb.key as Exclude<VerdictFilter, 'all'>],
          })),
        ]}
        value={f.tab}
        onChange={(k) => setF({ tab: k })}
      />

      {/* 統一篩選列：搜尋 → 推薦度 → 範圍(地域/時間/企業類型) → 來源/類型 → 更多 */}
      <FilterBar
        search={{ value: f.q, onChange: (v) => setF({ q: v }), placeholder: t.jobs_search_placeholder }}
        score={{ label: t.jobs_sort_recommend, min: f.min, max: f.max, onChange: (min, max) => setF({ min, max }) }}
        scope={[
          {
            options: REGION_OPTIONS.map((o) => ({ key: o.key, label: t[o.tKey] })),
            value: f.loc, onChange: (v: string) => setF({ loc: v }),
          },
          {
            options: DAYS_OPTIONS.map((o) => ({ key: o.key, label: t[o.tKey] })),
            value: String(f.days), onChange: (v: string) => setF({ days: Number(v) }),
          },
          {
            allLabel: t.jobs_filter_all_types,
            options: TIER_KEYS.map((k) => ({ key: k, label: getTierLabel(k) })),
            value: f.tiers, onChange: (v: string[]) => setF({ tiers: v }),
          },
          {
            options: ROLE_OPTIONS.map((o) => ({ key: o.key, label: t[o.tKey] })),
            value: f.role, onChange: (v: string) => setF({ role: v }),
          },
        ]}
        sourcePosting={{
          source: f.src, postType: f.post,
          onSource: (v) => setF({ src: v }), onPostType: (v) => setF({ post: v }),
        }}
        applied={{ value: f.applied, onChange: (v) => setF({ applied: v }) }}
        more={
          <FilterChip active={f.closed} onClick={() => setF({ closed: !f.closed })}>
            {t.jobs_filter_include_closed}
          </FilterChip>
        }
        moreActive={f.closed ? 1 : 0}
        dirty={dirty}
        onClear={() => { resetF(); setPage(1); }}
      />

      {/* 操作區：與篩選分離 */}
      <ActionBar>
        <ActionButton onClick={() => batchOpenJds((items ?? []).map((r) => r.id))}>
          {t.jobs_batch_open}
        </ActionButton>
      </ActionBar>

      {/* Table */}
      <div className="data-table-scroll" style={softCard}>
        <div style={{
          display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center', padding: '14px 26px',
          minWidth: 'min-content',
          borderBottom: '1.5px solid rgba(232,213,183,0.7)',
          fontSize: 12, fontWeight: 400, letterSpacing: 2, color: 'rgba(90,82,72,0.5)',
        }}>
          <div>#</div>
          <div>ID</div>
          <SortTh active={f.sort === 'recommend_score'} order={ord} onClick={() => clickSort('recommend_score')}>
            <span title={t.recommend_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.jobs_recommend}</span>
          </SortTh>
          <SortTh active={f.sort === 'company'} order={ord} onClick={() => clickSort('company')}>{t.recommend_table_company_role}</SortTh>
          <div>{t.jobs_table_salary}</div>
          <div>{t.recommend_table_posting}</div>
          <div>{t.recommend_table_location}</div>
          <div>{t.recommend_table_verdict}</div>
          <div>{t.recommend_table_reason}</div>
        </div>

        {items.length === 0 ? (
          <div style={{ padding: 64, textAlign: 'center', fontSize: 15, color: 'rgba(90,82,72,0.6)', letterSpacing: 2 }}>
            {t.jobs_no_match}
          </div>
        ) : (
          items.map((r, idx) => {
            const col = recommendColor(r.recommend_score);
            const m = verdictMeta[r.verdict];
            const rank = (page - 1) * pageSize + idx + 1;
            const medal = f.sort === 'recommend_score' && f.order === 'desc' && rank <= 3 ? MEDAL[rank - 1] : null;
            return (
              <div
                key={r.id}
                onClick={() => openJob(r.id)}
                style={{
                  display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center',
                  padding: '14px 26px', borderBottom: '1px solid rgba(232,213,183,0.5)',
                  cursor: 'pointer', minWidth: 'min-content',
                  boxShadow: r.app_status ? `inset 3px 0 0 ${C.appliedInk}`
                    : r.company_app_status ? `inset 3px 0 0 ${alpha(C.progressIndigo, 0.53)}` : undefined,
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = C.card; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
              >
                <span style={{
                  fontSize: medal ? 15 : 13, fontWeight: medal ? 800 : 600,
                  color: medal || 'rgba(90,82,72,0.4)', fontVariantNumeric: 'tabular-nums',
                }}>
                  {rank}
                </span>

                <span style={{ fontSize: 12, fontWeight: 500, color: 'rgba(90,82,72,0.5)', fontVariantNumeric: 'tabular-nums' }}>
                  {r.id}
                </span>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                  <span style={{ fontSize: 18, fontWeight: 800, color: col, fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>
                    {r.recommend_score}
                  </span>
                  <div style={{ width: 44, height: 4, borderRadius: 999, background: C.border, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${Math.min(100, Math.max(0, r.recommend_score))}%`, background: col, borderRadius: 999 }} />
                  </div>
                </div>

                <JobIdentityCell
                  company={r.company} title={r.title} score={r.score} recommendScore={r.recommend_score}
                  openworkScore={r.openwork_score} openworkUrl={r.openwork_url}
                  employeeCount={r.employee_count} mentionsAi={r.mentions_ai}
                />

                <SalaryCell salaryMin={r.salary_min} salaryMax={r.salary_max} />

                <SourceLogo source={r.source} postingType={r.posting_type} />

                <div style={{ fontSize: 13, fontWeight: 500, letterSpacing: 0.5, color: 'rgba(90,82,72,0.75)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={r.location || ''}>
                  {r.location || '—'}
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5,
                    fontSize: 11, fontWeight: 700, letterSpacing: 0.5, color: m.color,
                    background: `${alpha(m.color, 0.08)}`, border: `1px solid ${alpha(m.color, 0.2)}`,
                    borderRadius: 999, padding: '3px 10px',
                  }}>
                    <span style={{ width: 6, height: 6, borderRadius: 999, background: m.color }} />
                    {m.label}
                  </span>
                  <AppStatusBadge
                    appStatus={r.app_status}
                    companyAppStatus={r.company_app_status}
                    onCompanyClick={r.company_applied_job_id ? () => openJob(r.company_applied_job_id!) : undefined}
                  />
                </div>

                <div style={{
                  fontSize: 12, fontWeight: 400, color: 'rgba(90,82,72,0.6)', lineHeight: 1.5,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }} title={r.reason || ''}>
                  {r.reason || '—'}
                </div>
              </div>
            );
          })
        )}
        <Pager page={page} total={total} size={pageSize} onPage={setPage} onSize={(s) => { setPageSize(s); setPage(1); }} />
      </div>
    </div>
  );
};
