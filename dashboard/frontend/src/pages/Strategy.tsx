import React, { useEffect, useState } from 'react';
import { get } from '../api';
import { C, scoreColor, alpha } from '../theme';
import { Empty, Pager, SourceLogo, SourcePostingFilter, PageHeader, JobIdentityCell } from '../components/ui';
import { useT } from '../i18n';

const COLS = '30px 42px 60px 50px 50px minmax(260px,1fr) 130px 80px 56px 80px';

interface StrategyData {
  waves: Record<string, any[]>;
  today_applied: number;
  total_applied: number;
  replied: number;
}

export const Strategy: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const STATUS_META_LOCAL: Record<string, { label: string; color: string }> = {
    planned: { label: t.strategy_status_planned, color: C.sub },
    prepped: { label: t.strategy_status_prepped, color: '#4A9076' },
    submitted: { label: t.strategy_status_submitted, color: C.successGreen },
    skipped: { label: t.strategy_status_skipped, color: C.errorRed },
  };
  const [data, setData] = useState<StrategyData | null>(null);
  const [expandedWaves, setExpandedWaves] = useState<Set<number>>(new Set([1, 2, 3]));
  const [srcF, setSrcF] = useState('');
  const [postTypeF, setPostTypeF] = useState('');

  useEffect(() => {
    const p = new URLSearchParams();
    if (srcF) p.set('source', srcF);
    if (postTypeF) p.set('posting_type', postTypeF);
    get(`/api/strategy?${p}`).then(setData);
  }, [srcF, postTypeF]);

  if (!data) return <Empty>{t.strategy_loading}</Empty>;

  const waveKeys = Object.keys(data.waves).map(Number).sort();
  if (waveKeys.length === 0) {
    return (
      <div style={{ padding: 64, textAlign: 'center' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: C.ink, marginBottom: 12 }}>{t.strategy_empty_title}</div>
          <div style={{ fontSize: 14, color: C.sub }}>
          {t.strategy_empty_desc}
          </div>
        </div>
    );
  }

  const toggleWave = (w: number) => {
    const next = new Set(expandedWaves);
    if (next.has(w)) next.delete(w); else next.add(w);
    setExpandedWaves(next);
  };

  const totalAll = Object.values(data.waves).flat().length;
  const submittedAll = Object.values(data.waves).flat().filter((r) => r.status === 'submitted').length;
  const replyRate = data.total_applied > 0 ? (data.replied / data.total_applied * 100).toFixed(0) : '—';

  return (
    <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <PageHeader title={t.strategy_title} subtitle={t.strategy_subtitle} />

      {/* KPI row */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 20, flexWrap: 'wrap' }}>
        {[
          { label: t.strategy_kpi_today, value: `${data.today_applied}/3`, color: data.today_applied >= 3 ? C.errorRed : C.ink },
          { label: t.strategy_kpi_total, value: `${submittedAll}/${totalAll}`, color: C.ink },
          { label: t.strategy_kpi_applied, value: String(data.total_applied), color: C.gold },
          { label: t.strategy_kpi_reply_rate, value: `${replyRate}%`, color: C.successGreen },
        ].map((kpi) => (
          <div key={kpi.label} style={{
            flex: '1 1 100px', minWidth: 100, background: C.bg, borderRadius: 12,
            padding: '14px 16px', border: `1px solid ${C.border}`,
          }}>
            <div style={{ fontSize: 24, fontWeight: 800, color: kpi.color, fontVariantNumeric: 'tabular-nums' }}>
              {kpi.value}
            </div>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.sub, marginTop: 2 }}>{kpi.label}</div>
          </div>
        ))}
      </div>

      {/* 0 家已投時的臨門一腳提示：波次規劃好了，卡在「按下寄出」這一步 */}
      {totalAll > 0 && submittedAll === 0 && (
        <div style={{
          background: alpha('#C79A1B', 0.09), border: `1px solid ${alpha('#C79A1B', 0.35)}`,
          borderRadius: 12, padding: '12px 16px', marginBottom: 16, fontSize: 13, lineHeight: 1.7, color: C.ink,
        }}>
          ⚡ {t.strategy_zero_hint.replace('{total}', String(totalAll))}
        </div>
      )}

      {/* Source + posting type filter */}
      <div style={{ marginBottom: 16 }}>
        <SourcePostingFilter source={srcF} postType={postTypeF} onSource={setSrcF} onPostType={setPostTypeF} />
      </div>

      {/* Waves */}
      {waveKeys.map((waveNum) => {
        const items = data.waves[waveNum];
        const submitted = items.filter((r) => r.status === 'submitted').length;
        const expanded = expandedWaves.has(waveNum);
        const barPct = items.length > 0 ? (submitted / items.length) * 100 : 0;

        return (
          <div key={waveNum} style={{
            background: C.bg, borderRadius: 20, boxShadow: '0 10px 40px rgba(90,82,72,0.13)',
            overflow: 'hidden', marginBottom: 16,
          }}>
            {/* Wave header */}
            <div
              className="strategy-wave-header"
              onClick={() => toggleWave(waveNum)}
              style={{
                display: 'flex', alignItems: 'center', gap: 14, padding: '16px 24px',
                cursor: 'pointer', borderBottom: expanded ? '1.5px solid rgba(232,213,183,0.7)' : 'none',
              }}
            >
              <span style={{ fontSize: 16, fontWeight: 700, color: C.ink }}>
                {t.strategy_wave_label} {waveNum}
              </span>
              <span style={{ fontSize: 13, fontWeight: 500, color: C.sub }}>
                ({items.length} {t.unit_company_count}) — {waveNum === 1 ? t.strategy_wave_1 : waveNum === 2 ? t.strategy_wave_2 : waveNum === 3 ? t.strategy_wave_3 : `${t.strategy_wave_label} ${waveNum}`}
              </span>
              <div style={{ flex: 1 }} />
              {/* Progress bar */}
              <div style={{ width: 120, height: 6, borderRadius: 3, background: C.border, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${barPct}%`, background: C.successGreen, borderRadius: 3 }} />
              </div>
              <span style={{ fontSize: 13, fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: C.ink }}>
                {submitted}/{items.length}
              </span>
              <span style={{ fontSize: 12, color: C.sub }}>{expanded ? '▴' : '▾'}</span>
            </div>

            {expanded && (
              <div className="data-table-scroll">
                {/* Table header */}
                <div style={{
                  display: 'grid', gridTemplateColumns: COLS, gap: 12, alignItems: 'center',
                  padding: '10px 24px', minWidth: 'min-content',
                  fontSize: 11, fontWeight: 400, letterSpacing: 2, color: 'rgba(90,82,72,0.5)',
                }}>
                  <div>{t.strategy_col_rank}</div>
                  <div>ID</div>
                  <div title={t.weighted_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.strategy_col_weighted}</div>
                  <div title={t.recommend_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.jobs_recommend}</div>
                  <div title={t.score_tooltip} style={{ cursor: 'help', textDecoration: 'underline dotted rgba(90,82,72,0.35)', textUnderlineOffset: 3 }}>{t.jobs_score}</div>
                  <div>{t.applications_col_company_role}</div>
                  <div>{t.jobs_table_posting}</div>
                  <div>{t.jobs_table_location}</div>
                  <div>{t.strategy_col_pack}</div>
                  <div>{t.strategy_col_status}</div>
                </div>

                {/* Rows */}
                {items.map((r: any) => {
                  const sm = STATUS_META_LOCAL[r.status] || STATUS_META_LOCAL.planned;
                  const locLabel = (r.location || '').includes('Tokyo') || (r.location || '').includes('東京')
                    ? t.strategy_loc_tokyo : (r.location || '').includes('Osaka') || (r.location || '').includes('大阪')
                    ? t.strategy_loc_osaka : (r.location || '').includes('Remote') || (r.location || '').includes('Hybrid')
                    ? t.strategy_loc_remote : (r.location || '').slice(0, 6) || '—';

                  return (
                    <div
                      key={r.job_id}
                      onClick={() => openJob(r.job_id)}
                      style={{
                        display: 'grid', gridTemplateColumns: COLS, gap: 12, alignItems: 'center',
                        padding: '12px 24px', borderTop: '1px solid rgba(232,213,183,0.4)',
                        cursor: 'pointer', minWidth: 'min-content',
                        opacity: r.status === 'submitted' ? 0.55 : 1,
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = C.bg; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = ''; }}
                    >
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                        {r.rank}
                      </span>
                      <span style={{ fontSize: 12, fontWeight: 500, color: 'rgba(90,82,72,0.5)', fontVariantNumeric: 'tabular-nums' }}>
                        {r.job_id}
                      </span>
                      <span style={{ fontSize: 16, fontWeight: 800, color: scoreColor(r.weighted), fontVariantNumeric: 'tabular-nums' }}>
                        {r.weighted?.toFixed(1)}
                      </span>
                      <span style={{ fontSize: 14, fontWeight: 700, color: scoreColor(r.recommend_score ?? 0), fontVariantNumeric: 'tabular-nums' }}>
                        {r.recommend_score ?? '—'}
                      </span>
                      <span style={{ fontSize: 14, fontWeight: 700, color: scoreColor(r.score ?? 0), fontVariantNumeric: 'tabular-nums' }}>
                        {r.score ?? '—'}
                      </span>
                      <JobIdentityCell
                        company={r.company} title={r.title} score={r.score} recommendScore={r.recommend_score}
                        openworkScore={r.openwork_score} openworkUrl={r.openwork_url}
                        employeeCount={r.employee_count} mentionsAi={r.mentions_ai}
                      />
                      <SourceLogo source={r.source ?? ''} postingType={r.posting_type} />
                      <div style={{ fontSize: 12, fontWeight: 500, color: 'rgba(90,82,72,0.7)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={r.location || ''}>
                        {locLabel}
                      </div>
                      <div>
                        <span style={{
                          fontSize: 11, fontWeight: 700, padding: '3px 8px', borderRadius: 4,
                          background: r.pack_ready ? `${alpha(C.successGreen, 0.1)}` : `${alpha(C.errorRed, 0.1)}`,
                          color: r.pack_ready ? C.successGreen : C.errorRed,
                        }}>
                          {r.pack_ready ? t.strategy_pack_ready : t.strategy_pack_missing}
                        </span>
                      </div>
                      <div>
                        <span style={{
                          fontSize: 11, fontWeight: 700, padding: '3px 8px', borderRadius: 4,
                          background: `${alpha(sm.color, 0.1)}`, color: sm.color,
                        }}>
                          {sm.label}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
