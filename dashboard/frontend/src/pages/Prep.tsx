import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { get, post, openFolder } from '../api';
import { C } from '../theme';
import { Pill, Badge, Pager, Empty, Cmd, AppStatusBadge, JobIdentityCell, PageHeader, useIsNarrow } from '../components/ui';
import { FilterBar } from '../components/FilterBar';
import { useHashFilters } from '../filters/useHashFilters';
import { useT } from '../i18n';

const PrepCard: React.FC<{ p: any; expanded: boolean; onToggle: () => void }> = ({ p, expanded, onToggle }) => {
  const t = useT();
  const isNarrow = useIsNarrow();
  const [tab, setTab] = useState<string | null>(null);
  const [content, setContent] = useState('');
  const [ddLoading, setDdLoading] = useState(false);
  const [ddDone, setDdDone] = useState(false);
  // group ごとの折疊。main（正典パック）だけ既定で開き、2ji/qa_upgrade/saishu 等の
  // 追加 group は既定で畳んでおく（面接が進むたび group が増えてサイドバーが伸びるため）。
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set(['main']));
  const toggleGroup = (key: string) => setOpenGroups((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  useEffect(() => {
    if (expanded && !tab && p.files.length) setTab(p.files[0].name);
  }, [expanded]);

  const isAudio = (name: string | null) => !!name && name.endsWith('.mp3');

  useEffect(() => {
    if (!tab || isAudio(tab)) { setContent(''); return; }
    get(`/api/prep/${p.dir}/file?name=${encodeURIComponent(tab)}`).then((d) => setContent(d.content));
  }, [tab]);

  const status = (p.app_status || p.company_app_status)
    ? <AppStatusBadge appStatus={p.app_status} companyAppStatus={p.company_app_status} />
    : <Badge color={C.draftGray}>{t.prep_unapplied}</Badge>;
  const fileLabel = (name: string): string => {
    const known: Record<string, string> = {
      '00_README.md': t.prep_file_readme,
      '01_company_brief.md': t.prep_file_company_brief,
      '02_shibou_doki.md': t.prep_file_shibou_doki,
      '03_shibou_doki.md': t.prep_file_shibou_doki,
      '03_interview_qa.md': t.prep_file_interview_qa,
      '01_interview_qa.md': t.prep_file_interview_qa,
      '02_checklist.md': t.prep_file_checklist,
      '05_checklist.md': t.prep_file_checklist,
      '06_numbers_card.md': t.prep_file_numbers_card,
      '04_slides.html': t.prep_file_slides,
      'qa_upgrade/01_review.md': t.prep_file_qa_review,
      'qa_upgrade/02_interview_qa_upgraded.md': t.prep_file_qa_upgraded,
      'qa_upgrade/03_drilldown_qa.md': t.prep_file_qa_drilldown,
      'qa_upgrade/04_audit.md': t.prep_file_qa_audit,
    };
    if (known[name]) return known[name];
    const base = name.split('/').pop() || name;
    return base.replace(/^\d+_/, '').replace(/\.(md|html|mp3)$/, '').replace(/_/g, ' ');
  };
  // group は後端の子目錄名そのまま。既知のものだけ訳語を当て、未知はディレクトリ名で出す
  const groupLabels: Record<string, string> = {
    main: t.prep_group_main,
    qa_upgrade: t.prep_group_qa_upgrade,
    '2ji': '2次面接',
    saishu: '最終面接',
  };
  const fileGroups: { key: string; label: string; files: any[] }[] = [];
  (p.files ?? []).forEach((f: any) => {
    const key = f.group || 'main';
    let g = fileGroups.find((x) => x.key === key);
    if (!g) { g = { key, label: groupLabels[key] || key, files: [] }; fileGroups.push(g); }
    g.files.push(f);
  });

  return (
    <div style={{ borderBottom: `1.5px solid rgba(232,213,183,0.7)` }}>
      <div
        onClick={onToggle}
        style={{
          display: 'grid', gridTemplateColumns: isNarrow ? 'minmax(0,1fr) auto 18px' : '1fr 100px 22px',
          gap: isNarrow ? 10 : 16, alignItems: 'center',
          padding: isNarrow ? '14px 16px' : '16px 28px', cursor: 'pointer',
        }}
      >
        <JobIdentityCell
          company={p.job?.company || p.dir.replace(/^\d+_/, '')} title={p.job?.title}
          score={p.job?.score} openworkScore={p.job?.openwork_score} openworkUrl={p.job?.openwork_url}
          employeeCount={p.job?.employee_count} mentionsAi={p.job?.mentions_ai}
        />
        <div>{status}</div>
        <div style={{ textAlign: 'center' }}>
          <span style={{ fontSize: 16, color: C.ink }}>›</span>
        </div>
      </div>

      {expanded && (
        <div style={{
          padding: isNarrow ? '4px 16px 20px' : '4px 28px 30px',
          animation: 'panelIn 320ms cubic-bezier(0.34,1.56,0.64,1) both',
        }}>
          {/* 準備清單 — 緊湊 pill 列，接在公司資訊列下方 */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '0 0 16px' }}>
            {p.job?.salary_min && (
              <Pill style={{ cursor: 'default' }}>
                {t.prep_salary_range.replace('{min}', String(p.job.salary_min)).replace('{max}', String(p.job.salary_max))}
              </Pill>
            )}
            <Pill onClick={() => openFolder(p.path)} style={{ textDecoration: 'underline', textDecorationStyle: 'dotted' }}>
              {t.prep_open_folder}
            </Pill>
            {p.has_slides && <Pill style={{ cursor: 'default' }}>{t.prep_slides}</Pill>}
            <Pill
              onClick={ddLoading ? undefined : () => {
                setDdLoading(true);
                post('/api/drilldown/generate', { source: 'prep', prep_dir: p.dir })
                  .then(() => setDdDone(true))
                  .catch((err: Error) => alert(err.message))
                  .finally(() => setDdLoading(false));
              }}
              active={ddDone}
              style={{ cursor: ddLoading ? 'wait' : 'pointer', opacity: ddLoading ? 0.6 : 1 }}
            >
              {ddLoading ? t.prep_status_generating : ddDone ? t.prep_status_done : t.prep_generate_drilldown}
            </Pill>
          </div>

          {/* 檔案樹（左） + 內容檢視（右） */}
          <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? '1fr' : '220px minmax(0,1fr)', gap: 16 }}>
            <div style={{ background: 'rgba(232,213,183,0.2)', borderRadius: 20, padding: '8px 18px 14px' }}>
              <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', padding: '14px 0 6px' }}>
                {t.prep_files}
              </div>
              {fileGroups.filter((g) => g.files.length).map((g) => {
                const isOpen = openGroups.has(g.key);
                return (
                <div key={g.key}>
                  <button
                    onClick={() => toggleGroup(g.key)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 6, width: '100%',
                      border: 'none', background: 'transparent', cursor: 'pointer',
                      fontSize: 11, fontWeight: 700, letterSpacing: 1.5, color: 'rgba(90,82,72,0.45)',
                      padding: '10px 0 2px', textAlign: 'left',
                    }}
                  >
                    <span style={{ fontSize: 10, width: 10 }}>{isOpen ? '▾' : '▸'}</span>
                    {g.label}
                    <span style={{ fontWeight: 400, opacity: 0.7 }}>（{g.files.length}）</span>
                  </button>
                  {isOpen && g.files.map((f: any, i: number) => (
                    <div
                      key={f.name}
                      onClick={() => setTab(f.name)}
                      style={{
                        display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer',
                        padding: '10px 8px', margin: '2px 0', borderRadius: 10,
                        background: tab === f.name ? 'rgba(245,200,66,0.22)' : 'transparent',
                      }}
                    >
                      <span
                        style={{
                          fontSize: 12, fontWeight: 800, color: C.ink,
                          background: tab === f.name ? C.gold : 'rgba(245,200,66,0.25)', width: 22, height: 22,
                          borderRadius: 999, display: 'flex', alignItems: 'center',
                          justifyContent: 'center', flexShrink: 0, marginTop: 1,
                          fontVariantNumeric: 'tabular-nums',
                        }}
                      >
                        {i + 1}
                      </span>
                      <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.5px', lineHeight: 1.6 }}>
                        {fileLabel(f.name)}
                      </span>
                    </div>
                  ))}
                </div>
                );
              })}
            </div>

            <div style={{ background: C.bg, borderRadius: 20, boxShadow: 'inset 0 0 0 1.5px rgba(232,213,183,0.8)', padding: '22px 26px', minHeight: 320 }}>
              {isAudio(tab) ? (
                <div style={{ padding: '10px 0' }}>
                  <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.5px', marginBottom: 14 }}>
                    {fileLabel(tab as string)}
                  </div>
                  <audio
                    controls
                    style={{ width: '100%' }}
                    src={`/api/prep/${p.dir}/audio?name=${encodeURIComponent(tab as string)}`}
                  />
                </div>
              ) : (
                <div className="md-body" style={{ fontSize: 13, lineHeight: 1.8, maxHeight: '70vh', overflowY: 'auto' }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export const Prep: React.FC = () => {
  const t = useT();
  const [data, setData] = useState<any>(null);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(9);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [f, updateF, resetF, dirty] = useHashFilters({ applied: '' });
  const setF = (patch: Parameters<typeof updateF>[0]) => { updateF(patch); setPage(1); };

  useEffect(() => { get(`/api/prep?page=${page}&size=${size}`).then(setData); }, [page, size]);

  const visibleItems = (data?.items ?? []).filter((p: any) => {
    if (f.applied === 'applied') return !!p.app_status;
    if (f.applied === 'not_applied') return !p.app_status;
    if (f.applied === 'company') return !!(p.app_status || p.company_app_status);
    if (f.applied === 'not_company') return !(p.app_status || p.company_app_status);
    return true;
  });

  if (!data) return <Empty>{t.prep_loading}</Empty>;
  if (data.items.length === 0) {
    return <Empty>{t.prep_no_prep} <Cmd>python prep.py &lt;job_id&gt;</Cmd></Empty>;
  }

  // 展開時改為單卡視圖（避免 grid 跳版），頂部麵包屑返回
  if (expanded) {
    const p = data.items.find((x: any) => x.dir === expanded);
    if (p) {
      return (
        <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
          <button onClick={() => setExpanded(null)} style={{
            border: 'none', background: 'none', color: C.sub, fontSize: 13,
            textAlign: 'left', padding: '0 0 10px 2px', cursor: 'pointer',
          }}>← {t.prep_all}</button>
          <PrepCard p={p} expanded onToggle={() => setExpanded(null)} />
        </div>
      );
    }
  }

  return (
    <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <PageHeader
        title={t.prep_title}
        subtitle={
          <>
            {t.prep_subtitle_full.replace('{desc}', t.prep_desc).replace('{total}', String(data.total))}
          </>
        }
      />

      {/* 統一篩選列：應募狀態（與其他列表頁同一組選項） */}
      <FilterBar
        applied={{ value: f.applied, onChange: (v) => setF({ applied: v }) }}
        dirty={dirty}
        onClear={() => { resetF(); setPage(1); }}
      />

      <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', overflow: 'hidden' }}>
        {visibleItems.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', fontSize: 14, color: 'rgba(90,82,72,0.6)', letterSpacing: 2 }}>{t.prep_empty_filtered}</div>
        ) : visibleItems.map((p: any) => (
          <PrepCard key={p.dir} p={p} expanded={false}
            onToggle={() => setExpanded(p.dir)} />
        ))}
        <Pager page={page} total={data.total} size={size} onPage={setPage} onSize={(s) => { setSize(s); setPage(1); }} sizeOptions={[9, 25, 50]} />
      </div>
    </div>
  );
};
