import React, { useEffect, useMemo, useState } from 'react';
import { FileText } from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { get } from '../api';
import { C, getTierLabel, alpha } from '../theme';
import { Empty, Pager, SourceLogo, JobIdentityCell, PageHeader, SortTh } from '../components/ui';
import { FilterBar } from '../components/FilterBar';
import { StatusTabs } from '../components/StatusTabs';
import { useHashFilters } from '../filters/useHashFilters';
import { LANG_OPTIONS, PACK_STATUS_TABS } from '../filters/dict';
import { useT } from '../i18n';

interface PackFile {
  name: string;
  path: string;
  label: string;
  kind: 'md' | 'html';
}

interface Completeness {
  total: number;
  has_brief: boolean;
  has_shibou: boolean;
}

interface ApplyPack {
  job_id: number;
  slug: string;
  title: string;
  company: string;
  score: number | null;
  tier: string;
  apply_files: PackFile[];
  tailored_files: PackFile[];
  completeness: Completeness;
  recommend_score: number | null;
  posting_type: string | null;
  source: string | null;
  location: string | null;
  gap_verdict: string | null;
  gap_matched: string[] | null;
  gap_gaps: string[] | null;
  gap_reason: string | null;
  created_at: string;
  updated_at: string;
  app_status: string | null;
  company_app_status: string | null;
  company_applied_job_id: number | null;
  employee_count: number | null;
  mentions_ai: number | null;
  openwork_score: number | null;
  openwork_url: string | null;
}

const RESUME_FILE: PackFile = {
  name: 'shokumu',
  path: '__resume__',
  label: 'shokumu',
  kind: 'html',
};

// ---- 判定（優先讀 gap verdict，fallback 用 recommend_score）----
const verdict = (t: ReturnType<typeof useT>, rec: number | null, gapVerdict?: string | null) => {
  if (gapVerdict === 'go') return { key: 'go', label: t.verdict_go, color: C.successGreen };
  if (gapVerdict === 'improve') return { key: 'improve', label: t.verdict_improve, color: '#C79A1B' };
  if (gapVerdict === 'skip') return { key: 'skip', label: t.verdict_skip, color: C.sub };
  if (rec != null && rec >= 75) return { key: 'go', label: t.verdict_go, color: C.successGreen };
  if (rec != null && rec >= 60) return { key: 'improve', label: t.verdict_improve, color: '#C79A1B' };
  return { key: 'skip', label: t.verdict_skip, color: C.sub };
};

// ---- 言語判定（apply 目錄 _en 後綴）----
const packLang = (p: ApplyPack): 'en' | 'jp' => {
  const paths = [...p.apply_files, ...p.tailored_files].map((f) => f.path);
  return paths.some((x) => /_en(\/|\.|$)/.test(x)) ? 'en' : 'jp';
};

// ---- ファイル内容ビューア（md / html / 職務経歴書）----
const FileViewer: React.FC<{ file: PackFile; shokumuPath?: string }> = ({ file, shokumuPath }) => {
  const t = useT();
  const [content, setContent] = useState('');

  useEffect(() => {
    if (file.kind === 'md') {
      get(`/api/apply-file?path=${encodeURIComponent(file.path)}`).then((d) => setContent(d.content));
    }
  }, [file.path]);

  if (file.path === '__resume__') {
    const src = shokumuPath
      ? `/api/apply-file?path=${encodeURIComponent(shokumuPath)}`
      : '/api/resume/shokumu/html';
    return (
      <iframe
        src={src}
        style={{ width: '100%', height: 'calc(100vh - 320px)', minHeight: 560, border: 'none', borderRadius: 14, background: '#fff' }}
        title={t.tailored_base_resume}
      />
    );
  }
  if (file.kind === 'html') {
    return (
      <iframe
        src={`/api/apply-file?path=${encodeURIComponent(file.path)}`}
        style={{ width: '100%', height: 480, border: 'none', borderRadius: 14, background: '#fff' }}
        title={file.label}
      />
    );
  }
  return (
    <div className="md-body" style={{ fontSize: 13, lineHeight: 1.8, padding: '4px 4px 8px', maxHeight: 520, overflowY: 'auto' }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
};

const labelText = (t: ReturnType<typeof useT>, label: string): string => {
  const map: Record<string, string> = {
    documents: t.tailored_documents,
    shokumu: t.tailored_base_resume,
    'cover note': t.tailored_cover_note,
    company_brief: t.tailored_company_brief,
    shibou_doki: t.tailored_shibou,
    README: 'README',
  };
  return map[label] || label.replace(/_/g, ' ');
};

// ============ 展開行（パッケージ詳細）============
const PackDetail: React.FC<{ pack: ApplyPack; openJob: (id: number) => void }> = ({ pack, openJob }) => {
  const t = useT();
  const [active, setActive] = useState<PackFile | null>(null);
  const perJobShokumu = pack.apply_files.find((f) => f.label === 'shokumu' || f.path.includes('04_shokumu'));
  const allFiles = [...pack.apply_files, ...pack.tailored_files];

  const toggle = (f: PackFile) => setActive((prev) => (prev?.path === f.path ? null : f));

  const fileRow = (f: PackFile, idx: number) => {
    const isActive = active?.path === f.path;
    return (
      <div
        key={f.path}
        onClick={() => toggle(f)}
        style={{
          display: 'grid', gridTemplateColumns: '30px 1fr auto auto', gap: 12, alignItems: 'center',
          padding: '11px 4px', borderTop: idx === 0 ? 'none' : '1px solid rgba(232,213,183,0.5)', cursor: 'pointer',
        }}
      >
        <span style={{
          width: 30, height: 30, borderRadius: 9, background: 'rgba(232,213,183,0.55)',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'ui-monospace, monospace', fontSize: 13, fontWeight: 700, color: C.ink, flex: 'none',
        }}>{idx + 1}</span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13, fontWeight: 500, color: C.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.name}</div>
          <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.6)', marginTop: 2 }}>{labelText(t, f.label)}</div>
        </div>
        <span style={{
          fontSize: 11, fontWeight: 500, letterSpacing: 1, color: 'rgba(90,82,72,0.55)',
          background: 'rgba(232,213,183,0.35)', padding: '4px 10px', borderRadius: 999, whiteSpace: 'nowrap',
        }}>{f.kind === 'html' ? 'HTML' : 'MD'}</span>
        <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: 1, color: isActive ? C.gold : C.ink, whiteSpace: 'nowrap' }}>
          {isActive ? t.tailored_close : t.tailored_open}
        </span>
      </div>
    );
  };

  return (
    <div style={{ padding: '4px 28px 28px', animation: 'panelIn 320ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <div style={{ background: 'rgba(232,213,183,0.22)', borderRadius: 22, padding: '22px 24px' }}>
        {/* meta row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            {(() => { const v = verdict(t, pack.recommend_score, pack.gap_verdict); return (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, letterSpacing: 1, color: v.color, background: `${alpha(v.color, 0.1)}`, padding: '5px 12px', borderRadius: 999 }}>
              <span style={{ width: 7, height: 7, borderRadius: 999, background: v.color }} />{v.label}
              </span>
            ); })()}
            <span style={{ fontSize: 13, fontWeight: 500, color: 'rgba(90,82,72,0.7)' }}>
              {t.tailored_total_files.replace('{tier}', getTierLabel(pack.tier)).replace('{count}', String(allFiles.length))}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13, fontWeight: 500, color: 'rgba(90,82,72,0.7)', background: 'rgba(255,248,240,0.85)', padding: '7px 13px', borderRadius: 10 }}>
              #{pack.job_id} {pack.slug}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); openJob(pack.job_id); }}
              style={{
                padding: '7px 16px', borderRadius: 10, border: `1.5px solid ${C.gold}`,
                background: 'transparent', cursor: 'pointer', fontSize: 13, fontWeight: 700,
                letterSpacing: 1, color: C.gold, whiteSpace: 'nowrap',
              }}
            >
              {t.tailored_job_detail}
            </button>
          </div>
        </div>

        {/* file list */}
        <div style={{ background: C.bg, borderRadius: 18, padding: '6px 18px' }}>
          {allFiles.length ? allFiles.map(fileRow) : (
            <div style={{ padding: '16px 4px', fontSize: 13, color: C.sub }}>{t.tailored_no_files}</div>
          )}
          {/* 職務経歴書 — 常に閲覧可 */}
          <div
            onClick={() => toggle(RESUME_FILE)}
            style={{ display: 'grid', gridTemplateColumns: '30px 1fr auto auto', gap: 12, alignItems: 'center', padding: '11px 4px', borderTop: '1px solid rgba(232,213,183,0.5)', cursor: 'pointer' }}
          >
            <span style={{ width: 30, height: 30, borderRadius: 9, background: 'rgba(245,200,66,0.3)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <FileText size={16} color={C.ink} />
            </span>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>{perJobShokumu ? t.tailored_custom_resume : t.tailored_base_resume}</div>
              <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.6)', marginTop: 2 }}>2 page HTML</div>
            </div>
            <span style={{ fontSize: 11, fontWeight: 500, letterSpacing: 1, color: 'rgba(90,82,72,0.55)', background: 'rgba(232,213,183,0.35)', padding: '4px 10px', borderRadius: 999 }}>HTML</span>
            <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: 1, color: active?.path === '__resume__' ? C.gold : C.ink, whiteSpace: 'nowrap' }}>
              {active?.path === '__resume__' ? t.tailored_close : t.tailored_open}
            </span>
          </div>
        </div>

        {/* gap analysis summary */}
        {(pack.gap_matched || pack.gap_gaps || pack.gap_reason) && (
          <div style={{ marginTop: 16, background: C.bg, borderRadius: 18, padding: '16px 20px' }}>
            <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.5)', marginBottom: 12 }}>
              {t.tailored_gap_analysis}
            </div>
            {pack.gap_reason && (
              <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.7, color: C.ink, marginBottom: 14, padding: '10px 14px', background: 'rgba(245,200,66,0.10)', borderRadius: 12 }}>
                {pack.gap_reason}
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 16 }}>
              {pack.gap_matched && pack.gap_matched.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1, color: C.successGreen, marginBottom: 6 }}>
                    {t.tailored_matched}
                  </div>
                  {pack.gap_matched.map((m, i) => (
                    <div key={i} style={{ fontSize: 12, lineHeight: 1.65, color: 'rgba(90,82,72,0.8)', padding: '3px 0', borderTop: i === 0 ? 'none' : '1px solid rgba(232,213,183,0.4)' }}>
                      {m}
                    </div>
                  ))}
                </div>
              )}
              {pack.gap_gaps && pack.gap_gaps.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1, color: C.errorRed, marginBottom: 6 }}>
                    {t.tailored_gaps}
                  </div>
                  {pack.gap_gaps.map((g, i) => (
                    <div key={i} style={{ fontSize: 12, lineHeight: 1.65, color: 'rgba(90,82,72,0.8)', padding: '3px 0', borderTop: i === 0 ? 'none' : '1px solid rgba(232,213,183,0.4)' }}>
                      {g}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* viewer */}
        {active && (
          <div style={{ marginTop: 16, background: C.bg, borderRadius: 18, padding: '14px 18px' }}>
            <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1, color: 'rgba(90,82,72,0.55)', marginBottom: 8 }}>
              {active.path === '__resume__' ? (perJobShokumu ? t.tailored_custom_resume : t.tailored_base_resume) : active.name}
            </div>
            <FileViewer file={active} shokumuPath={perJobShokumu?.path} />
          </div>
        )}
      </div>
    </div>
  );
};

// ============ メイン ============
type LangFilter = 'all' | 'jp' | 'en';
type SortKey = 'recommend' | 'recommend_asc' | 'updated' | 'updated_asc';

export const Tailored: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const [data, setData] = useState<{ packs: ApplyPack[] } | null>(null);
  const [f, updateF, resetF, dirty] = useHashFilters({
    tab: 'all', q: '', lang: 'all', src: '', post: '', applied: '', sort: 'recommend',
  });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const setF = (patch: Parameters<typeof updateF>[0]) => { updateF(patch); setPage(1); };

  useEffect(() => { get('/api/apply-packs').then(setData); }, []);

  const packs = data?.packs ?? [];

  // 派生フラグ
  const flags = useMemo(() => {
    const m = new Map<number, { lang: 'en' | 'jp'; hasCustom: boolean; done: boolean }>();
    packs.forEach((p) => {
      const hasCustom = p.tailored_files.length > 0;
      const done = p.completeness.has_brief && p.completeness.has_shibou && hasCustom;
      m.set(p.job_id, { lang: packLang(p), hasCustom, done });
    });
    return m;
  }, [packs]);

  const stats = useMemo(() => {
    let custom = 0, brief = 0, shibou = 0, done = 0;
    packs.forEach((p) => {
      if (p.tailored_files.length) custom++;
      if (p.completeness.has_brief) brief++;
      if (p.completeness.has_shibou) shibou++;
      if (flags.get(p.job_id)?.done) done++;
    });
    return { total: packs.length, custom, brief, shibou, done };
  }, [packs, flags]);

  const filtered = useMemo(() => {
    const q = f.q.trim().toLowerCase();
    const list = packs.filter((p) => {
      const fl = flags.get(p.job_id)!;
      if (f.lang !== 'all' && fl.lang !== f.lang) return false;
      if (f.src && (p.source || '') !== f.src) return false;
      if (f.post && (p.posting_type || 'direct') !== f.post) return false;
      if (f.applied === 'applied' && !p.app_status) return false;
      if (f.applied === 'not_applied' && p.app_status) return false;
      if (f.applied === 'company' && !p.app_status && !p.company_app_status) return false;
      if (f.applied === 'not_company' && (p.app_status || p.company_app_status)) return false;
      if (f.tab === 'custom' && !fl.hasCustom) return false;
      if (f.tab === 'brief' && !p.completeness.has_brief) return false;
      if (f.tab === 'shibou' && !p.completeness.has_shibou) return false;
      if (f.tab === 'done' && !fl.done) return false;
      if (q && !(`${p.company} ${p.title} ${p.slug} ${p.job_id}`.toLowerCase().includes(q))) return false;
      return true;
    });
    const cmp = (a: ApplyPack, b: ApplyPack): number => {
      switch (f.sort as SortKey) {
        case 'recommend': return (b.recommend_score ?? -1) - (a.recommend_score ?? -1);
        case 'recommend_asc': return (a.recommend_score ?? -1) - (b.recommend_score ?? -1);
        case 'updated': return (b.updated_at ?? '').localeCompare(a.updated_at ?? '');
        case 'updated_asc': return (a.updated_at ?? '').localeCompare(b.updated_at ?? '');
        default: return 0;
      }
    };
    return [...list].sort(cmp);
  }, [packs, flags, f]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  if (!data) return <Empty>{t.tailored_loading}</Empty>;
  if (!packs.length) return <Empty>{t.tailored_no_prep}</Empty>;

  const COLS = '30px 40px minmax(320px,1fr) 56px 130px 100px 72px 80px 100px 90px 90px 26px';

  const toggleSort = (base: 'recommend' | 'updated') => {
    setF({ sort: f.sort === base ? `${base}_asc` : base });
  };

  return (
    <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <PageHeader
        title={t.nav_tailored}
        subtitle={
          <>
            {t.tailored_subtitle.replace('{total}', String(stats.total)).replace('{shown}', String(filtered.length))}
          </>
        }
      />

      {/* 狀態層 tabs（獨立一行，取代統計卡兼篩選；計數即概覽） */}
      <StatusTabs
        tabs={[
          { key: 'all', label: t.tailored_stat_all, count: stats.total },
          ...PACK_STATUS_TABS.map((tb) => ({
            key: tb.key, label: t[tb.tKey], color: tb.color,
            count: stats[tb.key as keyof typeof stats],
          })),
        ]}
        value={f.tab}
        onChange={(k) => setF({ tab: k })}
      />

      {/* 統一篩選列：搜尋 → 範圍(語言) → 來源/類型 → 應募狀態 */}
      <FilterBar
        search={{ value: f.q, onChange: (v) => setF({ q: v }), placeholder: t.tailored_search_placeholder }}
        scope={[
          {
            options: LANG_OPTIONS.map((o) => ({ key: o.key, label: t[o.tKey] })),
            value: f.lang, onChange: (v: string) => setF({ lang: v }),
          },
        ]}
        sourcePosting={{
          source: f.src, postType: f.post,
          onSource: (v) => setF({ src: v }), onPostType: (v) => setF({ post: v }),
        }}
        applied={{ value: f.applied, onChange: (v) => setF({ applied: v }) }}
        dirty={dirty}
        onClear={() => { resetF(); setPage(1); }}
      />

      {/* table */}
      <div className="data-table-scroll" style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', overflow: 'hidden' }}>
        <div style={{
          display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center', padding: '15px 28px',
          minWidth: 'min-content',
          borderBottom: '1.5px solid rgba(232,213,183,0.7)', fontSize: 12, letterSpacing: 2, color: 'rgba(90,82,72,0.5)',
        }}>
          <div>#</div><div>ID</div><div>{t.tailored_table_company_role}</div><div>{t.tailored_table_language}</div><div>{t.tailored_table_posting}</div><div>{t.tailored_table_location}</div>
          <SortTh active={f.sort.startsWith('recommend')} order={f.sort === 'recommend_asc' ? 'asc' : 'desc'} onClick={() => toggleSort('recommend')}>{t.tailored_sort_recommend}</SortTh>
          <div>{t.tailored_table_verdict}</div><div>{t.tailored_table_status}</div><div>{t.tailored_table_created}</div>
          <SortTh active={f.sort.startsWith('updated')} order={f.sort === 'updated_asc' ? 'asc' : 'desc'} onClick={() => toggleSort('updated')}>{t.tailored_table_updated}</SortTh>
          <div></div>
        </div>

        {paged.length === 0 && (
          <div style={{ padding: 64, textAlign: 'center', fontSize: 15, color: 'rgba(90,82,72,0.6)', letterSpacing: 2 }}>
            {t.tailored_empty_filtered}
          </div>
        )}

        {paged.map((p, idx) => {
          const f = flags.get(p.job_id)!;
          const v = verdict(t, p.recommend_score, p.gap_verdict);
          const isOpen = expanded === p.job_id;
          return (
            <div key={p.job_id} style={{ borderBottom: '1px solid rgba(232,213,183,0.5)' }}>
              <div
                onClick={() => setExpanded(isOpen ? null : p.job_id)}
                style={{
                  display: 'grid', gridTemplateColumns: COLS, gap: 16, alignItems: 'center', padding: '15px 28px',
                  cursor: 'pointer', minWidth: 'min-content', background: isOpen ? 'rgba(232,213,183,0.16)' : 'transparent',
                }}
              >
                {/* # */}
                <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                  {(page - 1) * pageSize + idx + 1}
                </span>
                {/* ID */}
                <span style={{ fontSize: 12, fontWeight: 500, color: 'rgba(90,82,72,0.5)', fontVariantNumeric: 'tabular-nums' }}>
                  {p.job_id}
                </span>
                {/* 会社・ポジション — 推薦列表的共用樣板 */}
                <JobIdentityCell
                  company={p.company} title={p.title} score={p.score} recommendScore={p.recommend_score}
                  openworkScore={p.openwork_score} openworkUrl={p.openwork_url}
                  employeeCount={p.employee_count} mentionsAi={p.mentions_ai}
                />
                {/* 言語 */}
                <div>
                  <span style={{
                    fontSize: 11, fontWeight: 700, letterSpacing: 1, padding: '4px 10px', borderRadius: 999,
                    background: f.lang === 'en' ? 'rgba(125,161,224,0.18)' : 'rgba(245,200,66,0.22)',
                    color: f.lang === 'en' ? C.progressIndigo : '#B58A12',
                  }}>{f.lang === 'en' ? 'EN' : 'JP'}</span>
                </div>
                {/* 投稿 */}
                <SourceLogo source={p.source ?? ''} postingType={p.posting_type} />
                {/* 勤務地 */}
                <div style={{ fontSize: 13, fontWeight: 500, letterSpacing: 0.5, color: 'rgba(90,82,72,0.75)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={p.location || ''}>
                  {p.location || '—'}
                </div>
                {/* 推薦度 */}
                <div>
                  <span style={{
                    fontSize: 14, fontWeight: 800, fontVariantNumeric: 'tabular-nums',
                    color: v.color,
                  }}>{p.recommend_score != null ? p.recommend_score : '—'}</span>
                </div>
                {/* 判定 */}
                <div>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, letterSpacing: 1, color: v.color }}>
                    <span style={{ width: 7, height: 7, borderRadius: 999, background: v.color }} />{v.label}
                  </span>
                </div>
                {/* ステータス */}
                <div>
                  <span style={{
                    fontSize: 11, fontWeight: 700, letterSpacing: 1, padding: '4px 11px', borderRadius: 999,
                    background: f.done ? `${alpha(C.successGreen, 0.1)}` : 'rgba(232,213,183,0.45)',
                    color: f.done ? C.successGreen : 'rgba(90,82,72,0.7)',
                  }}>{f.done ? t.tailored_status_done : f.hasCustom ? t.tailored_status_preparing : t.tailored_status_todo}</span>
                </div>
                {/* 作成日 */}
                <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.55)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                  {p.created_at?.slice(5) ?? '—'}
                </div>
                {/* 更新日 */}
                <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.55)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                  {p.updated_at?.slice(5) ?? '—'}
                </div>
                {/* chevron */}
                <div style={{ textAlign: 'center' }}>
                  <span style={{ display: 'inline-block', fontSize: 18, color: 'rgba(90,82,72,0.4)', transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 200ms' }}>›</span>
                </div>
              </div>
              {isOpen && <PackDetail pack={p} openJob={openJob} />}
            </div>
          );
        })}

        <Pager page={page} total={filtered.length} size={pageSize} onPage={setPage} onSize={(s) => { setPageSize(s); setPage(1); }} sizeOptions={[10, 20, 50]} />
      </div>
    </div>
  );
};
