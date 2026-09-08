import React from 'react';
import { C, alpha, recommendColor } from '../theme';
import { Badge, SectionLabel } from './ui';
import { useT } from '../i18n';

type Importance = 'must' | 'important' | 'preferred';
type RequirementStatus = 'matched' | 'partial' | 'gap' | 'unassessed';

interface RequirementRow {
  index: number;
  requirement: string;
  importance: Importance;
  status: RequirementStatus;
}

interface DimensionRow {
  key: string;
  score: number;
  weight: number | null;
}

const IMPORTANCE_ORDER: Importance[] = ['must', 'important', 'preferred'];
const DIMENSION_ORDER = [
  'salary', 'role_fit', 'company_product_stage', 'requirements',
  'domain', 'evidence', 'work_conditions', 'culture_risk',
];

const stringList = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : [];

const finiteNumber = (value: unknown): number | null => {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

const normalizeRequirements = (analysis: Record<string, unknown>): RequirementRow[] => {
  const requirements = stringList(analysis.requirements);
  const rawAssessments = Array.isArray(analysis.requirement_assessments)
    ? analysis.requirement_assessments
    : [];
  const byIndex = new Map<number, { importance: Importance; status: Exclude<RequirementStatus, 'unassessed'> }>();

  rawAssessments.forEach((raw) => {
    if (!raw || typeof raw !== 'object') return;
    const item = raw as Record<string, unknown>;
    const index = finiteNumber(item.index);
    if (index == null || !Number.isInteger(index) || index < 1 || index > requirements.length || byIndex.has(index)) return;
    const importanceValue = String(item.importance || '').toLowerCase();
    const statusValue = String(item.status || '').toLowerCase();
    if (statusValue !== 'matched' && statusValue !== 'partial' && statusValue !== 'gap') return;
    const importance: Importance = importanceValue === 'must' || importanceValue === 'preferred'
      ? importanceValue
      : 'important';
    const status: Exclude<RequirementStatus, 'unassessed'> = statusValue;
    byIndex.set(index, { importance, status });
  });

  return requirements.map((requirement, i) => {
    const index = i + 1;
    const assessment = byIndex.get(index);
    return {
      index,
      requirement,
      importance: assessment?.importance ?? 'important',
      status: assessment?.status ?? 'unassessed',
    };
  });
};

const sectionStyle: React.CSSProperties = {
  paddingTop: 16,
  borderTop: `1px solid ${C.border}`,
};

const GapSection: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section style={sectionStyle}>
    <SectionLabel style={{ marginBottom: 10 }}>{title}</SectionLabel>
    {children}
  </section>
);

export const GapAnalysisPanel: React.FC<{ analysis: Record<string, unknown> }> = ({ analysis }) => {
  const t = useT();
  const requirements = normalizeRequirements(analysis);
  const matchedEvidence = stringList(analysis.matched);
  const gaps = stringList(analysis.gaps);
  const reason = typeof analysis.recommend_reason === 'string' ? analysis.recommend_reason : '';
  const score = finiteNumber(analysis.recommend_score);
  const verdict = String(analysis.verdict || '').toLowerCase();
  const breakdown = analysis.recommend_breakdown && typeof analysis.recommend_breakdown === 'object'
    ? analysis.recommend_breakdown as Record<string, unknown>
    : null;
  const confidence = finiteNumber(breakdown?.confidence);
  const blockers = stringList(breakdown?.hard_blockers);
  const dimensionSource = breakdown?.dimensions && typeof breakdown.dimensions === 'object'
    ? breakdown.dimensions as Record<string, unknown>
    : {};

  const dimensionLabels: Record<string, string> = {
    salary: t.jobdrawer_gap_dim_salary,
    role_fit: t.jobdrawer_gap_dim_role,
    company_product_stage: t.jobdrawer_gap_dim_stage,
    requirements: t.jobdrawer_gap_dim_requirements,
    domain: t.jobdrawer_gap_dim_domain,
    evidence: t.jobdrawer_gap_dim_evidence,
    work_conditions: t.jobdrawer_gap_dim_conditions,
    culture_risk: t.jobdrawer_gap_dim_culture,
  };
  const dimensions: DimensionRow[] = DIMENSION_ORDER.flatMap((key) => {
    const raw = dimensionSource[key];
    if (!raw || typeof raw !== 'object') return [];
    const item = raw as Record<string, unknown>;
    const dimensionScore = finiteNumber(item.score);
    if (dimensionScore == null) return [];
    return [{ key, score: Math.max(0, Math.min(100, dimensionScore)), weight: finiteNumber(item.weight) }];
  });

  const verdictLabel = verdict === 'go' ? t.verdict_go
    : verdict === 'improve' ? t.verdict_improve
    : verdict === 'skip' ? t.verdict_skip
    : t.jobdrawer_gap_analyzed;
  const verdictColor = blockers.length > 0 || verdict === 'skip' ? C.errorRed
    : verdict === 'go' ? C.successGreen : C.amber;

  const statusMeta: Record<RequirementStatus, { label: string; symbol: string; color: string }> = {
    matched: { label: t.jobdrawer_gap_status_matched, symbol: '✓', color: C.successGreen },
    partial: { label: t.jobdrawer_gap_status_partial, symbol: '◐', color: C.amber },
    gap: { label: t.jobdrawer_gap_status_gap, symbol: '!', color: C.errorRed },
    unassessed: { label: t.jobdrawer_gap_status_unassessed, symbol: '·', color: C.sub },
  };
  const importanceLabels: Record<Importance, string> = {
    must: t.jobdrawer_gap_importance_must,
    important: t.jobdrawer_gap_importance_important,
    preferred: t.jobdrawer_gap_importance_preferred,
  };
  const counts = (['matched', 'partial', 'gap', 'unassessed'] as RequirementStatus[])
    .reduce<Record<RequirementStatus, number>>((acc, status) => {
      acc[status] = requirements.filter((row) => row.status === status).length;
      return acc;
    }, { matched: 0, partial: 0, gap: 0, unassessed: 0 });
  const hasAssessments = counts.matched + counts.partial + counts.gap > 0;
  const assessmentSummary = t.jobdrawer_gap_requirement_summary
    .replace('{matched}', String(counts.matched))
    .replace('{partial}', String(counts.partial))
    .replace('{gap}', String(counts.gap));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <section style={{
        padding: 16, borderRadius: 12,
        background: alpha(verdictColor, 0.08), border: `1px solid ${alpha(verdictColor, 0.25)}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {score != null && (
            <span style={{ fontSize: 34, lineHeight: 1, fontWeight: 800, color: recommendColor(score), fontVariantNumeric: 'tabular-nums' }}>
              {Math.round(score)}
            </span>
          )}
          <Badge color={verdictColor}>{verdictLabel}</Badge>
          {confidence != null && (
            <span style={{ marginLeft: 'auto', fontSize: 12, color: C.sub, whiteSpace: 'nowrap' }}>
              {t.jobdrawer_gap_confidence.replace('{score}', String(Math.round(confidence)))}
            </span>
          )}
        </div>
        {reason && (
          <p style={{ margin: '10px 0 0', fontSize: 13, lineHeight: 1.75, color: C.ink }}>
            {reason}
          </p>
        )}
        {blockers.length > 0 && (
          <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px solid ${alpha(C.errorRed, 0.25)}` }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.errorRed, marginBottom: 5 }}>
              {t.jobdrawer_gap_hard_blockers}
            </div>
            {blockers.map((blocker) => (
              <div key={blocker} style={{ fontSize: 12, lineHeight: 1.6, color: C.ink }}>! {blocker}</div>
            ))}
          </div>
        )}
      </section>

      {requirements.length > 0 && (
        <GapSection title={t.jobdrawer_gap_requirements}>
          {hasAssessments && (
            <>
              <div style={{ fontSize: 12, color: C.sub, marginBottom: 7 }}>{assessmentSummary}</div>
              <div
                role="img"
                aria-label={assessmentSummary}
                style={{ display: 'flex', height: 8, overflow: 'hidden', borderRadius: 999, background: C.border, marginBottom: 9 }}
              >
                {(Object.keys(statusMeta) as RequirementStatus[]).map((status) => counts[status] > 0 && (
                  <span key={status} style={{ flex: counts[status], background: statusMeta[status].color }} />
                ))}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                {(Object.keys(statusMeta) as RequirementStatus[]).map((status) => counts[status] > 0 && (
                  <span key={status} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: C.sub }}>
                    <span aria-hidden="true" style={{ color: statusMeta[status].color, fontWeight: 700 }}>{statusMeta[status].symbol}</span>
                    {statusMeta[status].label} {counts[status]}
                  </span>
                ))}
              </div>
            </>
          )}

          {hasAssessments ? IMPORTANCE_ORDER.map((importance) => {
            const rows = requirements.filter((row) => row.importance === importance);
            if (rows.length === 0) return null;
            const matchedCount = rows.filter((row) => row.status === 'matched').length;
            return (
              <div key={importance} style={{ marginTop: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 5 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: C.ink }}>{importanceLabels[importance]}</span>
                  <span style={{ fontSize: 11, color: C.sub }}>
                    {t.jobdrawer_gap_group_score.replace('{matched}', String(matchedCount)).replace('{total}', String(rows.length))}
                  </span>
                </div>
                {rows.map((row) => {
                  const meta = statusMeta[row.status];
                  return (
                    <div key={row.index} style={{
                      display: 'grid', gridTemplateColumns: '22px minmax(0,1fr) auto', gap: 8, alignItems: 'start',
                      padding: '9px 0', borderTop: `1px solid ${alpha(C.border, 0.65)}`,
                    }}>
                      <span aria-hidden="true" style={{
                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                        width: 20, height: 20, borderRadius: 999, color: meta.color,
                        background: alpha(meta.color, 0.12), fontSize: 12, fontWeight: 700,
                      }}>{meta.symbol}</span>
                      <span style={{ fontSize: 12, lineHeight: 1.55, color: C.ink }}>{row.requirement}</span>
                      <span style={{ fontSize: 10, fontWeight: 700, color: meta.color, whiteSpace: 'nowrap', paddingTop: 2 }}>
                        {meta.label}
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          }) : (
            <div>
              {requirements.map((row) => (
                <div key={row.index} style={{ display: 'flex', gap: 8, padding: '8px 0', borderTop: `1px solid ${alpha(C.border, 0.65)}` }}>
                  <span aria-hidden="true" style={{ color: C.gold }}>•</span>
                  <span style={{ fontSize: 12, lineHeight: 1.55, color: C.ink }}>{row.requirement}</span>
                </div>
              ))}
            </div>
          )}
        </GapSection>
      )}

      {(matchedEvidence.length > 0 || gaps.length > 0) && (
        <div style={{ ...sectionStyle, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 16 }}>
          {matchedEvidence.length > 0 && (
            <section>
              <SectionLabel style={{ marginBottom: 8 }}>{t.jobdrawer_gap_evidence}</SectionLabel>
              {matchedEvidence.map((item, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, padding: '6px 0', fontSize: 12, lineHeight: 1.6, color: C.ink }}>
                  <span aria-hidden="true" style={{ color: C.successGreen, fontWeight: 700 }}>✓</span>{item}
                </div>
              ))}
            </section>
          )}
          {gaps.length > 0 && (
            <section>
              <SectionLabel style={{ marginBottom: 8 }}>{t.jobdrawer_gap_missing}</SectionLabel>
              {gaps.map((item, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, padding: '6px 0', fontSize: 12, lineHeight: 1.6, color: C.ink }}>
                  <span aria-hidden="true" style={{ color: C.errorRed, fontWeight: 700 }}>!</span>{item}
                </div>
              ))}
            </section>
          )}
        </div>
      )}

      {dimensions.length > 0 && (
        <GapSection title={t.jobdrawer_gap_dimensions}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {dimensions.map((row) => {
              const color = row.score >= 75 ? C.successGreen : row.score >= 50 ? C.amber : C.errorRed;
              return (
                <div key={row.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(108px,1.15fr) minmax(90px,2fr) 34px', gap: 8, alignItems: 'center' }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 12, color: C.ink }}>{dimensionLabels[row.key]}</div>
                    {row.weight != null && (
                      <div style={{ fontSize: 10, color: C.sub }}>{t.jobdrawer_gap_weight.replace('{weight}', String(Math.round(row.weight)))}</div>
                    )}
                  </div>
                  <div style={{ height: 8, borderRadius: 999, background: alpha(C.border, 0.75), overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${row.score}%`, borderRadius: 999, background: color }} />
                  </div>
                  <span style={{ fontSize: 12, fontWeight: 700, color, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {Math.round(row.score)}
                  </span>
                </div>
              );
            })}
          </div>
        </GapSection>
      )}
    </div>
  );
};
