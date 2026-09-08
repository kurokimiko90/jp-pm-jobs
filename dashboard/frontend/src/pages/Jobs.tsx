import React, { useEffect, useState } from 'react';
import { get, batchOpenJds } from '../api';
import { C, getTierLabel, relativeDate, softCard, scoreColor, recommendColor, riseIn, alpha } from '../theme';
import { Empty, Pager, SourceLogo, AppStatusBadge, PageHeader, FilterChip, SkeletonRows, ErrorState, SortTh, JobIdentityCell, SalaryCell } from '../components/ui';
import { FilterBar, ActionBar, ActionButton } from '../components/FilterBar';
import { useHashFilters } from '../filters/useHashFilters';
import { DAYS_OPTIONS, REGION_OPTIONS, ROLE_OPTIONS, TIER_KEYS } from '../filters/dict';
import { useT } from '../i18n';

const FILTER_DEFAULTS = {
  q: '', min: 60, max: 100, tiers: [] as string[], loc: '', role: '', src: '', post: '',
  applied: '', closed: false, days: 0, sort: 'score', order: 'desc',
};

export const Jobs: React.FC<{ openJob: (id: number) => void; selected: number | null }> = ({ openJob, selected }) => {
  const t = useT();
  const [data, setData] = useState<any>(null);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [f, updateF, resetF, dirty] = useHashFilters(FILTER_DEFAULTS);
  const setF = (patch: Partial<typeof FILTER_DEFAULTS>) => { updateF(patch); setPage(1); };
  const [allTotal, setAllTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusIdx, setFocusIdx] = useState(0);
  const [expandedCos, setExpandedCos] = useState<Set<string>>(new Set());

  const reload = () => {
    setError(null);
    const p = new URLSearchParams({
      page: String(page), size: String(size), min_score: String(f.min),
      tier: f.tiers.join(','), q: f.q, sort: f.sort, order: f.order, loc: f.loc,
      ...(f.src ? { source: f.src } : {}),
      ...(f.post ? { posting_type: f.post } : {}),
      ...(f.role ? { job_type: f.role } : {}),
      ...(f.days > 0 ? { days: String(f.days) } : {}),
      ...(f.closed ? { show_closed: 'true' } : {}),
      ...(f.applied ? { applied_filter: f.applied } : {}),
      ...(f.max < 100 ? { max_score: String(f.max) } : {}),
    });
    get(`/api/jobs?${p}`).then(setData).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  useEffect(reload, [page, size, f]);
  useEffect(() => { setFocusIdx(0); setExpandedCos(new Set()); }, [data]);

  useEffect(() => { get('/api/jobs?size=1').then((d) => setAllTotal(d.total)); }, []);

  // 同公司多筆職缺摺疊：頁內以公司分組，首筆為代表、其餘展開才顯示
  type Group = { key: string; head: any; children: any[] };
  const groups: Group[] = [];
  {
    const byCo = new Map<string, Group>();
    for (const j of (data?.items ?? [])) {
      const key = (j.company || '').trim() || `#${j.id}`;
      const g = byCo.get(key);
      if (g) g.children.push(j);
      else { const ng: Group = { key, head: j, children: [] }; byCo.set(key, ng); groups.push(ng); }
    }
  }
  const renderList: { j: any; isChild: boolean; group?: Group }[] = [];
  for (const g of groups) {
    renderList.push({ j: g.head, isChild: false, group: g.children.length ? g : undefined });
    if (g.children.length && expandedCos.has(g.key)) {
      g.children.forEach((c) => renderList.push({ j: c, isChild: true }));
    }
  }

  const toggleCompany = (key: string) => {
    setExpandedCos((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const onListKey = (e: React.KeyboardEvent) => {
    const tgt = e.target as HTMLElement;
    if (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA') return;
    if (e.key === 'j') { e.preventDefault(); setFocusIdx((i: number) => Math.min(i + 1, renderList.length - 1)); }
    else if (e.key === 'k') { e.preventDefault(); setFocusIdx((i: number) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && renderList[focusIdx]) { e.preventDefault(); openJob(renderList[focusIdx].j.id); }
  };

  // 全庫百分位標籤（分數飽和時輔助排序判讀）：後端已算好「比此分高的占比」
  const topPctLabel = (j: any): string | null => {
    if (j.score == null || j.score_pct_above == null) return null;
    const p = j.score_pct_above;
    const shown = p < 0.05 ? '<0.1' : p < 1 ? p.toFixed(1) : String(Math.round(p));
    return t.jobs_top_pct.replace('{pct}', shown);
  };

  const clickSort = (col: string) => {
    if (f.sort === col) setF({ order: f.order === 'desc' ? 'asc' : 'desc' });
    else setF({ sort: col, order: 'desc' });
  };
  const ord: 'asc' | 'desc' = f.order === 'asc' ? 'asc' : 'desc';

  return (
    <div style={riseIn}>
      <PageHeader
        title={t.jobs_page_title}
        subtitle={t.jobs_page_subtitle
          .replace('{all}', String(allTotal ?? 0))
          .replace('{shown}', String(data?.total ?? 0))}
      />

      {/* 統一篩選列：搜尋 → 分數 → 範圍(地域/時間/企業類型) → 來源/類型 → 應募狀態 → 更多 */}
      <FilterBar
        search={{ value: f.q, onChange: (v) => setF({ q: v }), placeholder: t.jobs_search_placeholder }}
        score={{ label: t.jobs_sort_score, min: f.min, max: f.max, onChange: (min, max) => setF({ min, max }) }}
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
        <ActionButton
          onClick={() => {
            // 開くのは「今この画面に見えている」求人だけ。以前は score 範囲だけを
            // 送っていたため、他の絞り込みとページングが無視されて DB 全件が開いた。
            batchOpenJds(renderList.map(({ j }) => j.id as number));
          }}
        >
          {t.jobs_batch_open}
        </ActionButton>
      </ActionBar>

      {/* Table */}
      <div className="data-table-scroll" tabIndex={0} onKeyDown={onListKey} style={{ ...softCard, outline: 'none' }}>
        {error ? (
          <ErrorState message={error} onRetry={reload} />
        ) : !data ? (
          <SkeletonRows cols={[30, 40, 56, 56, '1fr', 100, 130, 118, 80, 110, 110]} />
        ) : data.items.length === 0 ? (
          <Empty>{t.jobs_no_match}</Empty>
        ) : (
          <>
            {/* Header */}
            <div style={{
              display: 'grid', gridTemplateColumns: '30px 40px 56px 56px minmax(320px,1fr) 100px 130px 118px 80px 110px 110px', minWidth: 'min-content',
              gap: 16, alignItems: 'center', padding: '14px 26px', borderBottom: '1.5px solid rgba(232,213,183,0.7)',
              fontSize: 12, fontWeight: 400, letterSpacing: 2, color: 'rgba(90,82,72,0.5)',
            }}>
              <div>#</div>
              <div>ID</div>
              <SortTh active={f.sort === 'score'} order={ord} onClick={() => clickSort('score')}>
                <span title={t.score_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.jobs_sort_score}</span>
              </SortTh>
              <SortTh active={f.sort === 'recommend_score'} order={ord} onClick={() => clickSort('recommend_score')}>
                <span title={t.recommend_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.jobs_sort_recommend}</span>
              </SortTh>
              <div>{t.jobs_table_company_role}</div>
              <div>{t.jobs_table_salary}</div>
              <div>{t.jobs_table_posting}</div>
              <SortTh active={f.sort === 'tier'} order={ord} onClick={() => clickSort('tier')}>{t.jobs_table_tier}</SortTh>
              <SortTh active={f.sort === 'first_seen'} order={ord} onClick={() => clickSort('first_seen')}>{t.jobs_first_seen}</SortTh>
              <div>{t.jobs_table_location}</div>
              <div>{t.jobs_table_status}</div>
            </div>

            {/* Rows — 同公司分組：代表列 + 「+N 同社」展開列 */}
            {renderList.map(({ j, isChild, group }, idx) => (
              <React.Fragment key={j.id}>
              <div
                onClick={() => { openJob(j.id); setFocusIdx(idx); }}
                style={{
                  display: 'grid', gridTemplateColumns: '30px 40px 56px 56px minmax(320px,1fr) 100px 130px 118px 80px 110px 110px', minWidth: 'min-content',
                  gap: 16, alignItems: 'center', padding: '14px 26px', borderBottom: '1px solid rgba(232,213,183,0.5)',
                  cursor: 'pointer',
                  opacity: isChild ? 0.88 : 1,
                  background: selected === j.id ? `${alpha(C.gold, 0.13)}` : isChild ? 'rgba(232,213,183,0.12)' : undefined,
                  boxShadow: idx === focusIdx
                    ? `inset 3px 0 0 ${C.gold}, inset 0 0 0 1.5px ${alpha(C.gold, 0.33)}`
                    : j.app_status ? `inset 3px 0 0 ${C.appliedInk}`
                    : j.company_app_status ? `inset 3px 0 0 ${alpha(C.progressIndigo, 0.53)}` : undefined,
                }}
                onMouseEnter={(e) => { if (selected !== j.id) e.currentTarget.style.background = C.bg; }}
                onMouseLeave={(e) => { if (selected !== j.id) e.currentTarget.style.background = isChild ? 'rgba(232,213,183,0.12)' : ''; }}
              >
                {/* # */}
                <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                  {isChild ? '↳' : (page - 1) * size + data.items.indexOf(j) + 1}
                </span>

                {/* ID */}
                <span style={{ fontSize: 12, fontWeight: 500, color: 'rgba(90,82,72,0.5)', fontVariantNumeric: 'tabular-nums' }}>
                  {j.id}
                </span>

                {/* Score (職缺評分) — 規則分降為次要字級，附全庫百分位輔助判讀 */}
                <span style={{ fontSize: 13, fontWeight: 700, color: scoreColor(j.score), fontVariantNumeric: 'tabular-nums' }}>
                  {j.score ?? '—'}
                  {topPctLabel(j) && (
                    <span style={{ display: 'block', fontSize: 9, fontWeight: 500, color: 'rgba(90,82,72,0.5)', whiteSpace: 'nowrap' }}>
                      {topPctLabel(j)}
                    </span>
                  )}
                </span>

                {/* Recommend Score (推薦度) — 決策主數字 */}
                <span style={{ fontSize: 18, fontWeight: 800, color: recommendColor(j.recommend_score), fontVariantNumeric: 'tabular-nums', opacity: j.recommend_score != null ? 1 : 0.3 }}>
                  {j.recommend_score ?? '—'}
                </span>

                {/* Company + Title — 推薦列表的共用樣板 */}
                <JobIdentityCell
                  company={j.company} title={j.title} score={j.score} recommendScore={j.recommend_score}
                  openworkScore={j.openwork_score} openworkUrl={j.openwork_url}
                  employeeCount={j.employee_count} mentionsAi={j.mentions_ai}
                />

                {/* 年収 */}
                <SalaryCell salaryMin={j.salary_min} salaryMax={j.salary_max} />

                {/* Posting type + source */}
                <SourceLogo source={j.source} postingType={j.posting_type} />

                {/* Tier */}
                <div>
                  <span style={{
                    display: 'inline-block', whiteSpace: 'nowrap',
                    padding: '6px 10px', borderRadius: 8, fontSize: 12, fontWeight: 700, letterSpacing: 0.5,
                    background: j.tier === 'ai_startup' ? `${alpha(C.gold, 0.13)}` : j.tier === 'mega_venture' ? `${alpha(C.progressIndigo, 0.13)}` : `${alpha(C.sub, 0.13)}`,
                    color: j.tier === 'ai_startup' ? C.gold : j.tier === 'mega_venture' ? C.progressIndigo : C.sub,
                  }}>
                    {getTierLabel(j.tier)}
                  </span>
                </div>

                {/* First seen (relative date) */}
                <div style={{ fontSize: 12, fontWeight: 500, color: 'rgba(90,82,72,0.6)' }} title={j.first_seen || ''}>
                  {relativeDate(j.first_seen)}
                </div>

                {/* Location */}
                <div style={{ fontSize: 13, fontWeight: 500, letterSpacing: 0.5, color: 'rgba(90,82,72,0.75)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={j.location || ''}>
                  {j.location || '—'}
                </div>

                {/* Status */}
                <div>
                  {(j.app_status || j.company_app_status) ? (
                    <AppStatusBadge
                      appStatus={j.app_status}
                      companyAppStatus={j.company_app_status}
                      onCompanyClick={j.company_applied_job_id ? () => openJob(j.company_applied_job_id) : undefined}
                    />
                  ) : j.liveness_status === 'expired' ? (
                    <span style={{ padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 700, letterSpacing: 0.5, background: '#E8E0D8', color: '#8B7D6B' }}>
                      {t.jobs_status_closed}
                    </span>
                  ) : j.liveness_status === 'uncertain' ? (
                    <span style={{ padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 700, letterSpacing: 0.5, background: '#FFF3CD', color: '#856404' }}>
                      {t.jobs_status_confirm}
                    </span>
                  ) : j.has_gap ? (
                    <span style={{
                      padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 700, letterSpacing: 0.5,
                      background: (j.recommend_score ?? 0) >= 75 ? `${alpha(C.gold, 0.2)}` : (j.recommend_score ?? 0) >= 60 ? '#E8F5E944' : `${alpha(C.sub, 0.13)}`,
                      color: (j.recommend_score ?? 0) >= 75 ? '#B8860B' : (j.recommend_score ?? 0) >= 60 ? '#2E7D32' : C.sub,
                    }}>
                      {(j.recommend_score ?? 0) >= 75 ? t.verdict_go : (j.recommend_score ?? 0) >= 60 ? t.jobs_status_review : t.verdict_skip}
                    </span>
                  ) : (
                    <span style={{ padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 700, letterSpacing: 0.5, background: `${alpha(C.sub, 0.13)}`, color: C.sub }}>
                      {t.jobs_status_unanalyzed}
                    </span>
                  )}
                </div>
              </div>

              {/* 同公司其餘職缺的展開/收合列 */}
              {group && (
                <div
                  onClick={() => toggleCompany(group.key)}
                  style={{
                    padding: '7px 26px 7px 72px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    color: 'rgba(90,82,72,0.6)', borderBottom: '1px solid rgba(232,213,183,0.5)',
                    background: 'rgba(232,213,183,0.12)', letterSpacing: 0.5,
                  }}
                >
                  {expandedCos.has(group.key)
                    ? `▴ ${t.jobs_group_less}`
                    : `▾ ${t.jobs_group_more.replace('{n}', String(group.children.length))}`}
                </div>
              )}
              </React.Fragment>
            ))}
          </>
        )}

        {data && (
          <Pager page={page} total={data.total} size={size} onPage={setPage} onSize={(s) => { setSize(s); setPage(1); }} />
        )}
      </div>
    </div>
  );
};
