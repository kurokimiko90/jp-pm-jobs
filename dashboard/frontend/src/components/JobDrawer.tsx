import React, { useEffect, useRef, useState } from 'react';
import {
  RadarChart, PolarGrid, PolarAngleAxis, Radar, ResponsiveContainer, Tooltip,
} from 'recharts';
import {
  Eye, Compass, PaperPlaneTilt, EnvelopeSimple, EnvelopeOpen, Circle, type Icon,
} from '@phosphor-icons/react';
import { get, post, openFolder } from '../api';
import { C, recommendColor, getTierLabel, relativeDate } from '../theme';
import { Badge, Pill } from './ui';
import { GapAnalysisPanel } from './GapAnalysisPanel';
import { JdViewer } from './JdViewer';
import { useT } from '../i18n';

const DIMS = ['salary_fit', 'market_keywords', 'role_fit', 'tier_preference', 'tech_overlap', 'domain'];

const StatusPicker: React.FC<{ job: any; onChanged: () => void; inputRef?: React.RefObject<HTMLInputElement> }> = ({ job, onChanged, inputRef }) => {
  const t = useT();
  const APP_STATUSES: [string, string][] = [
    ['applied', t.status_applied], ['casual', t.status_casual], ['recruiter', t.status_recruiter],
    ['tech', t.status_tech], ['onsite', t.status_onsite], ['offer', t.status_offer], ['rejected', t.status_rejected],
  ];
  const cur = job.application?.status;
  const [event, setEvent] = useState(job.application?.next_event || '');
  const [fuMsg, setFuMsg] = useState('');
  useEffect(() => setEvent(job.application?.next_event || ''), [job.application?.next_event]);

  const logFollowup = async () => {
    await post(`/api/followups/${job.id}`, {});
    setFuMsg(t.jobdrawer_followup_logged);
    onChanged();
  };
  const snoozeFollowup = async () => {
    const r = await post(`/api/followups/${job.id}/snooze`, { days: 3 });
    setFuMsg(t.jobdrawer_followup_snoozed.replace('{date}', r.next_followup));
    onChanged();
  };

  const set = async (s: string) => {
    if (s === cur) {
      if (confirm(t.applications_delete_current_confirm)) {
        await fetch(`/api/applications/${job.id}`, { method: 'DELETE' });
        onChanged();
      }
      return;
    }
    await post('/api/applications', { job_id: job.id, status: s, next_event: event });
    onChanged();
  };
  const saveEvent = async () => {
    if (!cur) return;
    await post('/api/applications', { job_id: job.id, status: cur, next_event: event });
    onChanged();
  };
  return (
    <div style={{ background: C.bg, borderRadius: 8, padding: 12, marginBottom: 12 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>
        {t.applications_stage_title}{cur ? '' : t.applications_stage_unapplied}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {APP_STATUSES.map(([s, label]) => (
          <Pill key={s} active={cur === s} onClick={() => set(s)}
            style={s === 'rejected' && cur !== 'rejected' ? { color: C.sub } : undefined}>
            {label}
          </Pill>
        ))}
      </div>
      {job.application && (
        <>
          <input
            ref={inputRef}
            value={event} onChange={(e) => setEvent(e.target.value)} onBlur={saveEvent}
            onKeyDown={(e) => e.key === 'Enter' && saveEvent()}
            placeholder={t.applications_next_event_placeholder}
            style={{
              width: '100%', marginTop: 8, borderRadius: 999, border: `1px solid ${C.border}`,
              padding: '6px 12px', fontSize: 12, background: C.card, color: C.ink, outline: 'none',
            }}
          />
          <div style={{ fontSize: 11, color: '#7A7168', marginTop: 6 }}>
            {t.applications_applied_meta.replace('{applied}', job.application.applied_at).replace('{updated}', job.application.last_updated)}
            {job.application.notes ? ` ・ ${job.application.notes}` : ''}
          </div>
          {cur !== 'rejected' && cur !== 'offer' && (
            <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <Pill onClick={logFollowup}>{t.jobdrawer_followup_done}</Pill>
              <Pill onClick={snoozeFollowup}>{t.jobdrawer_followup_snooze}</Pill>
              {fuMsg && <span style={{ fontSize: 11, color: '#7A7168' }}>{fuMsg}</span>}
            </div>
          )}
        </>
      )}
    </div>
  );
};

const ContribBars: React.FC<{ bd: any; weights: Record<string, number> }> = ({ bd, weights }) => {
  const t = useT();
  const rows = DIMS.map((k) => ({
    k,
    raw: Number(bd?.[k] ?? 0),
    w: weights[k] ?? 0,
    contrib: (Number(bd?.[k] ?? 0) * (weights[k] ?? 0)) / 100,
  }));
  const labels: Record<string, string> = {
    salary_fit: t.scoring_dim_salary_fit,
    market_keywords: t.scoring_dim_market_keywords,
    role_fit: t.scoring_dim_role_fit,
    tier_preference: t.scoring_dim_tier_preference,
    tech_overlap: t.scoring_dim_tech_overlap,
    domain: t.scoring_dim_domain,
    remote_visa: t.scoring_dim_remote_visa,
  };
  const max = Math.max(...rows.map((r) => r.contrib), 1);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {rows.map((r) => (
        <div key={r.k} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <span style={{ width: 110, color: C.sub }}>{labels[r.k] || r.k}</span>
          <div style={{ flex: 1, height: 10, background: C.bg, borderRadius: 999 }}>
            <div style={{
              width: `${(r.contrib / max) * 100}%`, height: '100%',
              background: C.gold, borderRadius: 999,
            }} />
          </div>
          <span style={{ width: 90, textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: C.ink }}>
            {r.raw.toFixed(0)} × {r.w}% = <b>{r.contrib.toFixed(1)}</b>
          </span>
        </div>
      ))}
    </div>
  );
};

const TIMELINE_ICON: Record<string, Icon> = {
  discovered: Eye, gap: Compass, applied: PaperPlaneTilt, followup: EnvelopeSimple, mail: EnvelopeOpen,
};

const Timeline: React.FC<{ events: { date: string; kind: string; label: string }[] }> = ({ events }) => {
  const t = useT();
  if (!events.length) {
    return <div style={{ color: C.sub, fontSize: 13, padding: '20px 0', textAlign: 'center' }}>{t.jobdrawer_timeline_empty}</div>;
  }
  const categoryLabels: Record<string, string> = {
    interview: t.inbox_category_interview_invite,
    scheduling: t.inbox_category_scheduling,
    initial: t.inbox_category_initial_contact,
    offer: t.inbox_category_offer,
    rejection: t.inbox_category_rejection,
  };
  return (
    <div>
      {events.map((e, i) => {
        const IconComp = TIMELINE_ICON[e.kind] ?? Circle;
        const label = e.kind === 'discovered'
          ? t.jobdrawer_timeline_discovered
          : e.kind === 'gap'
            ? t.jobdrawer_timeline_gap
            : e.kind === 'applied'
              ? t.jobdrawer_timeline_applied
              : e.kind === 'followup'
                ? `${t.jobdrawer_timeline_followup}${(e as any).note ? `：${(e as any).note}` : ''}`
                : e.kind === 'mail'
                  ? `${categoryLabels[(e as any).category] || (e as any).category || t.jobdrawer_timeline_mail}${(e as any).subject ? `：${(e as any).subject}` : ''}`
                  : e.label;
        return (
        <div key={i} style={{ display: 'flex', gap: 10 }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 20, flexShrink: 0 }}>
            <IconComp size={14} weight={e.kind === 'applied' ? 'fill' : 'regular'} color={e.kind === 'applied' ? C.gold : C.sub} style={{ marginTop: 2 }} />
            {i < events.length - 1 && <span style={{ flex: 1, width: 1, background: C.border, marginTop: 4 }} />}
          </div>
          <div style={{ flex: 1, minWidth: 0, paddingBottom: 16 }}>
            <div style={{ fontSize: 11, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>{e.date}</div>
            <div style={{ fontSize: 13, color: C.ink, marginTop: 2, lineHeight: 1.5 }}>{label}</div>
          </div>
        </div>
        );
      })}
    </div>
  );
};

type Tab = 'summary' | 'gap' | 'docs' | 'timeline';
const TABS: { key: Tab; label: string }[] = [
  { key: 'summary', label: 'summary' },
  { key: 'gap', label: 'gap' },
  { key: 'docs', label: 'docs' },
  { key: 'timeline', label: 'timeline' },
];
const TAB_KEYS: Record<string, Tab> = { '1': 'summary', '2': 'gap', '3': 'docs', '4': 'timeline' };

export const JobDrawer: React.FC<{ jobId: number; onClose: () => void }> = ({ jobId, onClose }) => {
  const t = useT();
  const [job, setJob] = useState<any>(null);
  const [scoring, setScoring] = useState<any>(null);
  const [view, setView] = useState<'bars' | 'radar'>('bars');
  const [showHelp, setShowHelp] = useState(false);
  const [gapLoading, setGapLoading] = useState(false);
  const [prepLoading, setPrepLoading] = useState(false);
  const [tab, setTab] = useState<Tab>('summary');
  const nextEventRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setJob(null);
    setTab('summary');
    get(`/api/jobs/${jobId}`).then(setJob);
    get('/api/scoring').then(setScoring);
  }, [jobId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      const tgt = e.target as HTMLElement;
      if (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA') return;
      const next = TAB_KEYS[e.key];
      if (next) setTab(next);
    };
    window.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [onClose]);

  const reload = () => get(`/api/jobs/${jobId}`).then(setJob);

  const runGap = async () => {
    setGapLoading(true);
    try {
      await post(`/api/gap/${jobId}`, {});
      await reload();
    } catch (e: unknown) {
      alert(t.jobdrawer_gap_failed.replace('{message}', e instanceof Error ? e.message : String(e)));
    } finally {
      setGapLoading(false);
    }
  };

  const runPrep = async () => {
    setPrepLoading(true);
    try {
      await post(`/api/prep/${jobId}/generate`, {});
      await reload();
    } catch (e: unknown) {
      alert(t.jobdrawer_prep_failed.replace('{message}', e instanceof Error ? e.message : String(e)));
    } finally {
      setPrepLoading(false);
    }
  };

  const bd = job?.score_breakdown;
  const radarData = bd ? DIMS.map((k) => ({ dim: k, value: Number(bd[k] ?? 0) })) : [];
  const recommendScore = job?.gap_analysis?.recommend_score;

  // 頂部固定 CTA：依狀態變化（無 gap → 有 gap 無 pack → 有 pack 未投 → 已投）
  let cta: { label: string; onClick: () => void; loading?: boolean } | null = null;
  if (job) {
    if (!job.gap_analysis) {
      cta = { label: gapLoading ? t.jobdrawer_cta_running_gap : t.jobdrawer_cta_run_gap, onClick: runGap, loading: gapLoading };
    } else if (!job.prep_dir) {
      cta = { label: prepLoading ? t.jobdrawer_cta_running_prep : t.jobdrawer_cta_run_prep, onClick: runPrep, loading: prepLoading };
    } else if (!job.application) {
      cta = { label: t.jobdrawer_cta_record_stage, onClick: () => setTab('summary') };
    } else {
      cta = { label: t.jobdrawer_cta_record_next_event, onClick: () => { setTab('summary'); setTimeout(() => nextEventRef.current?.focus(), 50); } };
    }
  }

  return (
    <>
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 49, background: 'rgba(90,82,72,.25)',
    }} />
    <div onClick={(e) => e.stopPropagation()} style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: 'clamp(min(480px, 100vw), 38vw, 640px)', maxWidth: '100vw', zIndex: 50,
      background: C.card, boxShadow: '-4px 0 24px rgba(90,82,72,.18)',
      display: 'flex', flexDirection: 'column',
    }}>
      {!job ? <div style={{ color: C.sub, padding: 20 }}>{t.jobs_loading}</div> : (
        <>
          {/* 頂部固定：company/title + 雙分數 + CTA */}
          <div style={{ padding: '20px 20px 14px', borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ minWidth: 0, flex: 1, paddingRight: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 15, fontWeight: 700, lineHeight: 1.4 }}>{job.title}</span>
                  {job.url && (
                    <button
                      onClick={() => window.open(job.url, '_blank')}
                      style={{
                        flexShrink: 0, border: `1px solid ${C.border}`, borderRadius: 999,
                        padding: '3px 9px', background: C.bg, color: C.ink,
                        fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap',
                      }}
                    >
                      {t.applications_open_jd} ↗
                    </button>
                  )}
                </div>
                <div style={{ color: C.sub, fontSize: 13, marginTop: 2 }}>
                  {job.company || '—'} ・ {job.location || '—'}
                </div>
                {job.first_seen && (
                  <div style={{ color: C.sub, fontSize: 12, marginTop: 4 }} title={job.first_seen}>
                    {t.jobdrawer_first_seen.replace('{date}', relativeDate(job.first_seen))}
                  </div>
                )}
              </div>
              <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 18, color: C.sub, flexShrink: 0 }}>✕</button>
            </div>

            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '12px 0' }}>
              <span style={{ fontSize: 34, fontWeight: 700, color: recommendColor(recommendScore), fontVariantNumeric: 'tabular-nums' }}>
                {recommendScore ?? '—'}
              </span>
              <span style={{ fontSize: 15, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>
                {t.jobdrawer_market_score.replace('{score}', String(job.score ?? '—'))}
              </span>
            </div>

            {cta && (
              <button
                onClick={cta.onClick}
                disabled={!!cta.loading}
                style={{
                  width: '100%', border: 'none', borderRadius: 999, padding: '10px 16px',
                  background: C.gold, color: C.ink, fontSize: 13, fontWeight: 700,
                  cursor: cta.loading ? 'default' : 'pointer', opacity: cta.loading ? 0.6 : 1,
                }}
              >
                {cta.label}
              </button>
            )}
          </div>

          {/* Tab bar */}
          <div style={{ display: 'flex', gap: 0, borderBottom: `1px solid ${C.border}`, flexShrink: 0, padding: '0 20px' }}>
            {TABS.map((tb) => (
              <button
                key={tb.key}
                onClick={() => setTab(tb.key)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  padding: '10px 14px', fontSize: 13, fontWeight: tab === tb.key ? 700 : 400,
                  color: tab === tb.key ? C.ink : C.sub,
                  borderBottom: tab === tb.key ? `2px solid ${C.gold}` : '2px solid transparent',
                  marginBottom: -1,
                }}
              >
                {tb.key === 'summary' ? t.jobdrawer_tab_summary : tb.key === 'gap' ? t.jobdrawer_tab_gap : tb.key === 'docs' ? t.jobdrawer_tab_docs : t.jobdrawer_tab_timeline}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
            {tab === 'summary' && (
              <>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
                  <Badge color={C.progressIndigo}>{getTierLabel(job.tier || 'unknown')}</Badge>
                  <Badge color={job.source_kind === 'career-ops' ? C.progressIndigo : C.sub}>
                    {job.source}（{job.source_kind}）
                  </Badge>
                  {job.jp_level && <Badge>{job.jp_level}</Badge>}
                  {job.remote && <Badge>{job.remote}</Badge>}
                  {(job.salary_min || job.salary_max) && (
                    <Badge color={C.successGreen}>{job.salary_min ?? '?'}–{job.salary_max ?? '?'}{t.analytics_salary_unit}</Badge>
                  )}
                  {!job.application && (
                    <Pill
                      onClick={async () => {
                        if (!confirm(t.jobdrawer_ignore_confirm)) return;
                        await post(`/api/jobs/${jobId}/blacklist`, { on: true });
                        onClose();
                      }}
                      style={{ marginLeft: 'auto', color: C.sub }}
                    >
                      {t.jobdrawer_ignore_job}
                    </Pill>
                  )}
                </div>

                <StatusPicker job={job} onChanged={reload} inputRef={nextEventRef} />

                {bd && typeof bd === 'object' && (
                  <div style={{ background: C.bg, borderRadius: 8, padding: 12 }}>
                    <div style={{ display: 'flex', gap: 6, marginBottom: 10, alignItems: 'center' }}>
                      <Pill active={view === 'bars'} onClick={() => setView('bars')}>{t.jobdrawer_view_bars}</Pill>
                      <Pill active={view === 'radar'} onClick={() => setView('radar')}>{t.jobdrawer_view_radar}</Pill>
                      <button onClick={() => setShowHelp(!showHelp)} title={t.jobdrawer_score_help}
                        style={{ marginLeft: 'auto', border: `1px solid ${C.border}`, borderRadius: 999, width: 26, height: 26, background: C.card, color: '#7A7168', fontSize: 13 }}>?</button>
                    </div>
                    {view === 'bars' && scoring && <ContribBars bd={bd} weights={scoring.weights} />}
                    {view === 'radar' && (
                      <div style={{ height: 220 }}>
                        <ResponsiveContainer>
                          <RadarChart data={radarData}>
                            <PolarGrid stroke={C.border} />
                            <PolarAngleAxis dataKey="dim" tick={{ fontSize: 10, fill: C.sub }} />
                            <Radar dataKey="value" stroke={C.gold} fill={C.gold} fillOpacity={0.35} />
                            <Tooltip />
                          </RadarChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                    {showHelp && scoring && (
                      <div style={{ fontSize: 11, color: C.sub, marginTop: 10, lineHeight: 1.6 }}>
                        {DIMS.map((k) => (
                          <div key={k}><b style={{ color: C.ink }}>{k} ({scoring.weights[k]}%)</b> — {scoring.dim_labels[k]}</div>
                        ))}
                        <div style={{ marginTop: 4 }}>
                          {t.jobdrawer_score_penalties
                            .replace('{engOnly}', String(scoring.penalties.eng_only.factor))
                            .replace('{pmGate}', String(scoring.penalties.pm_gate.factor))}
                        </div>
                      </div>
                    )}
                    {bd.matched_keywords?.length > 0 && (
                      <div style={{ marginTop: 10, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {bd.matched_keywords.map((k: string) => <Badge key={k} color={C.successGreen}>{k}</Badge>)}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

            {tab === 'gap' && (
              job.gap_analysis && typeof job.gap_analysis === 'object' ? (
                <GapAnalysisPanel analysis={job.gap_analysis as Record<string, unknown>} />
              ) : (
                <Pill
                  onClick={runGap}
                  style={{ background: C.gold, color: C.ink, ...(gapLoading ? { opacity: 0.6, pointerEvents: 'none' as const } : {}) }}
                >
                  {gapLoading ? t.jobdrawer_cta_running_gap : t.jobdrawer_cta_run_gap}
                </Pill>
              )
            )}

            {tab === 'docs' && (
              <div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
                  {job.prep_dir
                    ? <Pill onClick={() => openFolder(job.prep_dir)}>{t.jobdrawer_open_prep_folder}</Pill>
                    : <Pill
                        onClick={runPrep}
                        style={{ background: C.gold, color: C.ink, ...(prepLoading ? { opacity: 0.6, pointerEvents: 'none' as const } : {}) }}
                      >
                        {prepLoading ? t.jobdrawer_cta_running_prep : t.jobdrawer_cta_run_prep}
                      </Pill>}
                  {job.tailored_resume_path && <Pill onClick={() => openFolder(job.tailored_resume_path)}>{t.jobdrawer_open_tailored_resume}</Pill>}
                </div>
                {job.raw_jd && (
                  <>
                    <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>{t.jobdrawer_jd_fulltext}</div>
                    <JdViewer raw={job.raw_jd} />
                  </>
                )}
              </div>
            )}

            {tab === 'timeline' && <Timeline events={job.timeline || []} />}
          </div>
        </>
      )}
    </div>
    </>
  );
};
