import React, { useEffect, useState } from 'react';
import { get, post, openFolder } from '../api';
import { C, getTierLabel, scoreColor, recommendColor, relativeDate, softCard, riseIn, alpha } from '../theme';
import { Empty, Pill, ReportStat, PageHeader, ErrorState, SkeletonRows, Pager, SourceLogo, PanelActions, SortTh, type PanelAction, FilterChip, JobIdentityCell, SalaryCell } from '../components/ui';
import { ActionBar, ActionButton, FilterBar } from '../components/FilterBar';
import { StatusTabs } from '../components/StatusTabs';
import { useHashFilters } from '../filters/useHashFilters';
import { DA_STATUS_TABS, DA_STATUS_COLOR, DAYS_OPTIONS, REGION_OPTIONS, ROLE_OPTIONS, TIER_KEYS } from '../filters/dict';
import { useT } from '../i18n';

interface EmailCandidate {
  email: string;
  confidence: number;
  source: string;
}

interface DAItem {
  job_id: number;
  status: string;
  apply_method: string | null;
  careers_url: string | null;
  form_url: string | null;
  emails: EmailCandidate[];
  confirmed_email: string | null;
  pack_dir: string | null;
  gmail_draft_id: string | null;
  error: string | null;
  updated_at: string | null;
  company: string;
  title: string;
  recommend_score: number | null;
  score: number | null;
  url: string;
  source: string;
  posting_type: string | null;
  job_type: string | null;
  salary_min: number | null;
  salary_max: number | null;
  employee_count: number | null;
  mentions_ai: number | null;
  openwork_score: number | null;
  openwork_url: string | null;
  location: string | null;
  tier: string | null;
  first_seen: string | null;
  liveness_status: string | null;
  app_status: string | null;
  company_app_status: string | null;
  company_applied_job_id: number | null;
  running: boolean;
}

interface ListResp {
  items: DAItem[];
  counts: Record<string, number>;
  total: number;
}

const COLS = '24px 52px minmax(320px,2.2fr) 100px 92px 56px 56px 70px minmax(220px,1.8fr) 110px 80px';
const PAGE_SIZE = 25;
const STATUS_ORDER = ['pack_ready', 'detected', 'drafted', 'not_found', 'failed', 'sent', 'skipped', 'pending'];
type DirectFilterState = {
  tab: string;
  q: string;
  min: number;
  max: number;
  loc: string;
  role: string;
  src: string;
  post: string;
  applied: string;
  days: number;
  tiers: string[];
  closed: boolean;
  sort: string;
  order: 'asc' | 'desc';
};

const put = (url: string, body: unknown) =>
  fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  });

export const DirectApply: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const [data, setData] = useState<ListResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [f, updateF, resetF, dirty] = useHashFilters<DirectFilterState>({
    tab: '', q: '', min: 0, max: 100, loc: '', role: '', src: '', post: '', applied: '', days: 0, tiers: [], closed: false, sort: '', order: 'desc',
  });
  const setF = (patch: Parameters<typeof updateF>[0]) => { updateF(patch); setPage(1); setExpanded(null); };
  const [expanded, setExpanded] = useState<number | null>(null);
  const [emailDraft, setEmailDraft] = useState('');
  const [manualId, setManualId] = useState('');
  const [busy, setBusy] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const statusLabel = (s: string): string => {
    const map: Record<string, string> = {
      pending: t.da_status_pending, detected: t.da_status_detected,
      not_found: t.da_status_not_found, pack_ready: t.da_status_pack_ready,
      drafted: t.da_status_drafted, sent: t.da_status_sent,
      skipped: t.da_status_skipped, failed: t.da_status_failed,
    };
    return map[s] ?? s;
  };
  const methodLabel = (m: string | null): string =>
    m === 'email' ? t.da_method_email : m === 'form' ? t.da_method_form : t.da_method_none;
  const itemEmail = (it: DAItem): string => it.confirmed_email || it.emails[0]?.email || '';
  const matchesLoc = (loc: string | null | undefined, filter: string): boolean => {
    const v = (loc || '').toLowerCase();
    if (!filter) return true;
    if (filter === 'japan') return /tokyo|東京|japan|大阪|osaka|京都|kyoto|リモート|在宅|remote|hybrid/i.test(v);
    if (filter === 'overseas') return !!v && !/tokyo|東京|japan|大阪|osaka|京都|kyoto|リモート|在宅|remote|hybrid/i.test(v);
    return true;
  };

  const clickSort = (col: string) => {
    if (f.sort !== col) setF({ sort: col, order: 'desc' });
    else if (f.order === 'desc') setF({ order: 'asc' });
    else setF({ sort: '' });
  };
  const ord: 'asc' | 'desc' = f.order === 'asc' ? 'asc' : 'desc';

  const load = () => {
    setError(null);
    get('/api/direct-apply/list').then(setData).catch((e: unknown) =>
      setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(() => { load(); }, []);
  // 有背景任務時輪詢刷新
  useEffect(() => {
    if (!data?.items.some((it) => it.running)) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [data]);

  const runJob = async (jobId: number) => {
    setBusy(jobId);
    try {
      await post(`/api/direct-apply/${jobId}/run`, {});
      load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const saveEmail = async (jobId: number, email: string) => {
    await put(`/api/direct-apply/${jobId}`, { confirmed_email: email });
    load();
  };

  const createDraft = async (it: DAItem) => {
    const email = it.confirmed_email || emailDraft || it.emails[0]?.email;
    if (!email) return;
    setBusy(it.job_id);
    try {
      if (email !== it.confirmed_email) await put(`/api/direct-apply/${it.job_id}`, { confirmed_email: email });
      await post(`/api/direct-apply/${it.job_id}/draft`, {});
      alert(t.da_draft_done);
      load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const skip = async (jobId: number) => {
    await put(`/api/direct-apply/${jobId}`, { status: 'skipped' });
    load();
  };

  const toggleSelect = (jobId: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId); else next.add(jobId);
      return next;
    });
  };

  const batchSkip = async () => {
    for (const id of selected) {
      await put(`/api/direct-apply/${id}`, { status: 'skipped' });
    }
    setSelected(new Set());
    load();
  };

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (data === null) return <SkeletonRows cols={[24, 52, '2.2fr', 100, 92, 56, 56, 70, '1.8fr', 110, 80]} />;

  const counts = data.counts;
  const q = f.q.trim().toLowerCase();
  let items = f.tab ? data.items.filter((it) => it.status === f.tab) : data.items;
  if (q) {
    items = items.filter((it) =>
      `${it.company} ${it.title} ${it.job_id} ${itemEmail(it)} ${it.source}`.toLowerCase().includes(q));
  }
  if (f.min > 0) items = items.filter((it) => (it.recommend_score ?? -1) >= f.min);
  if (f.max < 100) items = items.filter((it) => (it.recommend_score ?? 101) <= f.max);
  if (f.loc) items = items.filter((it) => matchesLoc(it.location, f.loc));
  if (f.role) items = items.filter((it) => (it.job_type ?? 'other') === f.role);
  if (f.src) items = items.filter((it) => it.source === f.src);
  if (f.post) items = items.filter((it) => (it.posting_type ?? 'direct') === f.post);
  if (f.days > 0) {
    const cutoff = Date.now() - f.days * 24 * 60 * 60 * 1000;
    items = items.filter((it) => {
      if (!it.first_seen) return false;
      const ts = Date.parse(it.first_seen);
      return Number.isFinite(ts) && ts >= cutoff;
    });
  }
  if (f.tiers.length > 0) items = items.filter((it) => f.tiers.includes(it.tier ?? 'unknown'));
  if (f.applied === 'applied') items = items.filter((it) => it.app_status != null);
  else if (f.applied === 'not_applied') items = items.filter((it) => it.app_status == null);
  else if (f.applied === 'company') items = items.filter((it) => it.company_app_status != null);
  else if (f.applied === 'not_company') items = items.filter((it) => it.company_app_status == null);
  if (!f.closed) items = items.filter((it) => (it.liveness_status ?? 'active') !== 'expired');
  const cmp = (a: DAItem, b: DAItem): number => {
    const dir = f.order === 'desc' ? -1 : 1;
    const num = (av: number | null | undefined, bv: number | null | undefined) => {
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return dir * (av - bv);
    };
    const text = (av: string | null | undefined, bv: string | null | undefined) =>
      dir * (av || '').localeCompare(bv || '', 'ja');
    switch (f.sort) {
      case 'job_id': return num(a.job_id, b.job_id);
      case 'company': return text(`${a.company} ${a.title}`, `${b.company} ${b.title}`);
      case 'source': return text(a.source, b.source);
      case 'score': return num(a.score, b.score);
      case 'recommend_score': return num(a.recommend_score, b.recommend_score);
      case 'apply_method': return text(methodLabel(a.apply_method), methodLabel(b.apply_method));
      case 'email': return text(itemEmail(a), itemEmail(b));
      case 'status': {
        const av = STATUS_ORDER.indexOf(a.status);
        const bv = STATUS_ORDER.indexOf(b.status);
        return dir * ((av === -1 ? STATUS_ORDER.length : av) - (bv === -1 ? STATUS_ORDER.length : bv));
      }
      case 'updated_at': return text(a.updated_at, b.updated_at);
      default: return 0;
    }
  };
  const sortedItems = f.sort ? [...items].sort(cmp) : items;
  const totalFiltered = sortedItems.length;
  const paged = sortedItems.slice((page - 1) * pageSize, page * pageSize);

  // 轉換瓶頸：包備妥了卻沒寄出——這是全管線轉換率最低的一段，用 banner 推最後一步
  const pendingSend = (counts['pack_ready'] || 0) + (counts['drafted'] || 0);
  const noEmailCount = data.items.filter(
    (it) => it.status === 'pack_ready' && !it.confirmed_email && it.emails.length === 0).length;

  return (
    <div style={riseIn}>
      <PageHeader title={t.da_title} subtitle={t.da_subtitle} />

      {pendingSend > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
          background: alpha('#C79A1B', 0.09), border: `1px solid ${alpha('#C79A1B', 0.35)}`,
          borderRadius: 12, padding: '12px 16px', marginBottom: 16, fontSize: 13, lineHeight: 1.7,
        }}>
          <span style={{ fontWeight: 700, color: '#8A6D00' }}>
            ⚡ {t.da_banner_ready.replace('{n}', String(pendingSend))}
          </span>
          <span style={{ color: C.sub }}>{t.da_banner_next}</span>
          {noEmailCount > 0 && (
            <span style={{ color: '#C0564A', fontSize: 12 }}>
              {t.da_banner_no_email.replace('{n}', String(noEmailCount))}
            </span>
          )}
          <span style={{ flex: 1 }} />
          <Pill active onClick={() => setF({ tab: 'pack_ready' })}>{t.da_banner_cta}</Pill>
        </div>
      )}

      {/* KPI：純展示（轉換漏斗），篩選走下方 tabs */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <ReportStat value={data.total} label={t.da_kpi_total} sub={t.da_kpi_total_sub} color={C.ink} />
        <ReportStat value={counts['pack_ready'] || 0} label={t.da_kpi_pack_ready} sub={t.da_kpi_pack_ready_sub} color="#C79A1B" />
        <ReportStat value={counts['drafted'] || 0} label={t.da_kpi_drafted} sub={t.da_kpi_drafted_sub} color="#6C7FD8" />
        <ReportStat value={counts['sent'] || 0} label={t.da_kpi_sent} sub={t.da_kpi_sent_sub} color="#3D9A6C" />
      </div>

      {/* 狀態層 tabs（字典固定順序，不再跟著後端 counts 的 key 順序漂移） */}
      <StatusTabs
        tabs={[
          { key: '', label: t.applications_filter_all, count: data.total },
          ...DA_STATUS_TABS.map((tb) => ({
            key: tb.key, label: t[tb.tKey], color: tb.color, count: counts[tb.key] || 0,
          })),
        ]}
        value={f.tab}
        onChange={(k) => setF({ tab: k })}
        hideEmpty
      />

      {/* 統一篩選列：搜尋 → 推薦度 → 範圍(地域/時間/企業類型/職種) → 來源/類型 → 應募狀態 → 更多（與職缺列表頁同順序） */}
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
        onClear={() => { resetF(); setPage(1); setExpanded(null); }}
      />

      {/* 操作區：手動觸發 + 批次操作（與篩選分離） */}
      <ActionBar>
        {selected.size > 0 && (
          <ActionButton tone="danger" onClick={batchSkip}>
            {t.da_batch_skip.replace('{n}', String(selected.size))}
          </ActionButton>
        )}
        <input
          value={manualId}
          onChange={(e) => setManualId(e.target.value.replace(/\D/g, ''))}
          placeholder={t.da_manual_placeholder}
          style={{
            width: 90, borderRadius: 9, border: `1.5px solid ${C.border}`,
            padding: '6px 12px', fontSize: 12, background: C.bg, color: C.ink, outline: 'none',
          }}
        />
        <ActionButton onClick={() => { if (manualId) { runJob(Number(manualId)); setManualId(''); } }}>
          {t.da_manual_run}
        </ActionButton>
      </ActionBar>

      {totalFiltered === 0 ? (
        <Empty>{t.da_empty}</Empty>
      ) : (
        <div className="data-table-scroll" style={{ ...softCard }}>
          <div style={{
            display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center',
            padding: '14px 26px', minWidth: 'min-content',
            borderBottom: '1.5px solid rgba(232,213,183,0.7)',
            fontSize: 12, fontWeight: 400, letterSpacing: 2, color: 'rgba(90,82,72,0.5)',
          }}>
            <input
              type="checkbox"
              title={t.da_select_all}
              checked={paged.length > 0 && paged.every((it) => selected.has(it.job_id))}
              onChange={(e) => {
                setSelected((prev) => {
                  const next = new Set(prev);
                  paged.forEach((it) => { if (e.target.checked) next.add(it.job_id); else next.delete(it.job_id); });
                  return next;
                });
              }}
              style={{ accentColor: '#C79A1B', cursor: 'pointer', margin: 0 }}
            />
            <SortTh active={f.sort === 'job_id'} order={ord} onClick={() => clickSort('job_id')}>ID</SortTh>
            <SortTh active={f.sort === 'company'} order={ord} onClick={() => clickSort('company')}>{t.da_col_company_role}</SortTh>
            <div>{t.jobs_table_salary}</div>
            <SortTh active={f.sort === 'source'} order={ord} onClick={() => clickSort('source')}>{t.da_col_source}</SortTh>
            <SortTh active={f.sort === 'score'} order={ord} onClick={() => clickSort('score')}>
              <span title={t.score_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.da_col_score}</span>
            </SortTh>
            <SortTh active={f.sort === 'recommend_score'} order={ord} onClick={() => clickSort('recommend_score')}>
              <span title={t.recommend_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.da_col_recommend}</span>
            </SortTh>
            <SortTh active={f.sort === 'apply_method'} order={ord} onClick={() => clickSort('apply_method')}>{t.da_col_method}</SortTh>
            <SortTh active={f.sort === 'email'} order={ord} onClick={() => clickSort('email')}>{t.da_col_email}</SortTh>
            <SortTh active={f.sort === 'status'} order={ord} onClick={() => clickSort('status')}>{t.da_col_status}</SortTh>
            <SortTh active={f.sort === 'updated_at'} order={ord} onClick={() => clickSort('updated_at')}>{t.da_col_updated}</SortTh>
          </div>

          {paged.map((it) => {
            const col = recommendColor(it.recommend_score);
            const open = expanded === it.job_id;
            const sColor = DA_STATUS_COLOR[it.status] ?? C.sub;
            const topEmail = it.confirmed_email || it.emails[0]?.email || '';
            return (
              <div key={it.job_id} style={{ borderBottom: '1px solid rgba(232,213,183,0.5)' }}>
                <div
                  onClick={() => { setExpanded(open ? null : it.job_id); setEmailDraft(it.confirmed_email || it.emails[0]?.email || ''); }}
                  style={{
                    display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center',
                    padding: '13px 26px', cursor: 'pointer', minWidth: 'min-content',
                    background: open ? C.card : undefined,
                    boxShadow: `inset 3px 0 0 ${sColor}`,
                  }}
                  onMouseEnter={(e) => { if (!open) e.currentTarget.style.background = C.card; }}
                  onMouseLeave={(e) => { if (!open) e.currentTarget.style.background = ''; }}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(it.job_id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleSelect(it.job_id)}
                    style={{ accentColor: '#C79A1B', cursor: 'pointer', margin: 0 }}
                  />
                  <span style={{ fontSize: 11, fontWeight: 600, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                    {it.job_id}
                  </span>
                  <JobIdentityCell
                    company={it.company} title={it.title} score={it.score} recommendScore={it.recommend_score}
                    openworkScore={it.openwork_score} openworkUrl={it.openwork_url}
                    employeeCount={it.employee_count} mentionsAi={it.mentions_ai}
                  />
                  <SalaryCell salaryMin={it.salary_min} salaryMax={it.salary_max} />
                  <SourceLogo source={it.source} />
                  <span style={{ fontSize: 13, fontWeight: 700, color: scoreColor(it.score), fontVariantNumeric: 'tabular-nums' }}>
                    {it.score ?? '—'}
                  </span>
                  <span style={{ fontSize: 17, fontWeight: 800, color: col, fontVariantNumeric: 'tabular-nums' }}>
                    {it.recommend_score ?? '—'}
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: C.sub }}>
                    {methodLabel(it.apply_method)}
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: it.confirmed_email ? C.ink : 'rgba(90,82,72,0.55)' }} title={topEmail}>
                    {topEmail || '—'}
                    {!it.confirmed_email && it.emails[0] && (
                      <span style={{ marginLeft: 6, fontSize: 10, color: 'rgba(90,82,72,0.5)' }}>
                        {Math.round(it.emails[0].confidence * 100)}%
                      </span>
                    )}
                  </span>
                  <span style={{
                    fontSize: 11, fontWeight: 700, color: sColor,
                    background: alpha(sColor, 0.12), borderRadius: 999, padding: '3px 10px',
                    justifySelf: 'start', whiteSpace: 'nowrap',
                  }}>
                    {it.running ? t.da_running : statusLabel(it.status)}
                  </span>
                  <span style={{ fontSize: 11, color: 'rgba(90,82,72,0.55)', whiteSpace: 'nowrap' }} title={it.updated_at || ''}>
                    {it.updated_at ? relativeDate(it.updated_at.slice(0, 10)) : '—'}
                  </span>
                </div>

                {open && (
                  <div style={{ padding: '4px 26px 18px', background: C.card }}>
                    {/* email 候選 + 確認輸入 */}
                    {it.emails.length > 0 && (
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                        {it.emails.slice(0, 5).map((e) => (
                          <Pill key={e.email} active={emailDraft === e.email} onClick={() => setEmailDraft(e.email)}>
                            {e.email} · {Math.round(e.confidence * 100)}%
                          </Pill>
                        ))}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
                      <input
                        value={emailDraft}
                        onChange={(e) => setEmailDraft(e.target.value)}
                        onBlur={() => { if (emailDraft && emailDraft !== it.confirmed_email) saveEmail(it.job_id, emailDraft); }}
                        placeholder={t.da_email_placeholder}
                        style={{
                          width: 320, borderRadius: 999, border: `1px solid ${C.border}`,
                          padding: '7px 14px', fontSize: 12, background: C.bg, color: C.ink, outline: 'none',
                        }}
                      />
                      {/* 主操作：金色實心強調 */}
                      <Pill
                        active
                        onClick={() => createDraft(it)}
                        style={busy === it.job_id || (!emailDraft && !it.confirmed_email)
                          ? { opacity: 0.4, pointerEvents: 'none', fontWeight: 700 } : { fontWeight: 700 }}
                      >
                        ✉️ {t.da_btn_draft}
                      </Pill>
                      {it.gmail_draft_id && (
                        <Pill onClick={() => window.open('https://mail.google.com/mail/u/0/#drafts', '_blank')}>
                          {t.da_open_gmail}
                        </Pill>
                      )}
                    </div>

                    <PanelActions
                      primary={[{ label: t.applications_open_detail, onClick: () => openJob(it.job_id) }]}
                      links={([
                        it.pack_dir ? { label: t.da_btn_open_pack, onClick: () => openFolder(it.pack_dir!) } : null,
                        it.careers_url ? { label: t.da_open_careers, onClick: () => window.open(it.careers_url!, '_blank') } : null,
                        it.form_url ? { label: t.da_open_form, onClick: () => window.open(it.form_url!, '_blank') } : null,
                        { label: t.applications_open_jd, onClick: () => window.open(it.url, '_blank') },
                      ].filter(Boolean)) as PanelAction[]}
                      right={[
                        { label: t.da_btn_rerun, onClick: () => runJob(it.job_id), disabled: busy === it.job_id || it.running },
                        { label: t.da_btn_skip, onClick: () => skip(it.job_id), tone: 'danger' },
                      ]}
                    />
                    {it.error && (
                      <div style={{ fontSize: 11, color: '#C0564A', marginTop: 10 }}>{it.error}</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          <Pager
            page={page}
            total={totalFiltered}
            size={pageSize}
            onPage={(p) => { setPage(p); setExpanded(null); }}
            onSize={(s) => { setPageSize(s); setPage(1); setExpanded(null); }}
            sizeOptions={[10, 25, 50]}
          />
        </div>
      )}
    </div>
  );
};
