import React from 'react';
import { C } from '../theme';
import { Card, SectionTitle, Empty } from './ui';
import { useT } from '../i18n';
import { RateRow, type RateItem } from './RateRow';

/** 年収回答 × 書類通過（app_analytics.build_salary_ask）。 */
export interface SalaryAskItem {
  job_id: number; company: string; outcome: string; status: string; rejection_stage: string | null;
  replied_at: string; desired_man: number | null; minimum_man: number | null;
}
export interface SalaryAsk {
  summary: RateItem & { jobs: number; withdrawn: number; no_application: number; since: string | null };
  control: RateItem & { jobs: number };
  by_desired: RateItem[];
  by_minimum: RateItem[];
  items: SalaryAskItem[];
  no_application: { job_id: number; company: string; replied_at: string; desired_man: number | null; minimum_man: number | null }[];
  later?: { job_id: number; company: string; replied_at: string; stage: string; desired_man: number | null; minimum_man: number | null }[];
}

/** 年収を答えた応募の書類通過率。基準線は「同時期に応募し年収を聞かれなかった応募」。 */
export const SalaryAskCard: React.FC<{ data: SalaryAsk }> = ({ data }) => {
  const t = useT();
  const { summary: s, control } = data;
  const baseline = control.rate;
  // 軸は標本十分な行で決める（n=1 の CI は 80% 近くまで伸び、主力の差を潰す）
  const rows = [...data.by_desired, ...data.by_minimum, s, control].filter((r) => r.decided > 0);
  const scaleRows = rows.filter((r) => !r.insufficient);
  const scaleMax = Math.min(100, Math.max(20, ...(scaleRows.length ? scaleRows : rows).map((r) => r.ci_high ?? 0)) * 1.05);
  const amountLabel = (tpl: string, key: string): string =>
    key === 'unknown' ? t.app_salary_unknown : tpl.replace('{n}', key);
  const failed = s.decided - s.passed;
  const sub = (title: string, items: RateItem[], tpl: string) => (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.sub, letterSpacing: '0.04em', margin: '14px 0 8px' }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        {items.filter((it) => it.decided > 0).map((it) => (
          <RateRow key={it.key} label={amountLabel(tpl, it.key)} item={it} baseline={baseline}
            scaleMax={scaleMax} insufficientText={t.app_stats_insufficient} />
        ))}
      </div>
    </div>
  );

  return (
    <Card>
      <SectionTitle right={<span style={{ fontSize: 11, color: C.sub }}>
        {t.app_salary_note.replace('{since}', s.since ?? '—')}
      </span>}>{t.app_salary_title}</SectionTitle>
      {s.jobs === 0 ? <Empty>{t.app_salary_empty}</Empty> : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            <RateRow label={t.app_salary_replied} item={s} baseline={baseline}
              scaleMax={scaleMax} insufficientText={t.app_stats_insufficient} />
            <RateRow label={t.app_salary_control} item={control} baseline={baseline}
              scaleMax={scaleMax} insufficientText={t.app_stats_insufficient} />
          </div>
          <div style={{ fontSize: 12, color: C.sub, marginTop: 10, lineHeight: 1.7 }}>
            {t.app_salary_counts
              .replace('{jobs}', String(s.jobs)).replace('{passed}', String(s.passed))
              .replace('{failed}', String(failed)).replace('{pending}', String(s.pending))
              .replace('{unknown}', String(s.unknown)).replace('{withdrawn}', String(s.withdrawn))}
            {s.no_application > 0 && ` · ${t.app_salary_no_app.replace('{n}', String(s.no_application))}`}
          </div>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 20,
          }}>
            {sub(t.app_salary_by_desired, data.by_desired, t.app_salary_desired_label)}
            {sub(t.app_salary_by_minimum, data.by_minimum, t.app_salary_minimum_label)}
          </div>
          <div style={{ fontSize: 11, color: C.sub, marginTop: 12, lineHeight: 1.6 }}>⚠ {t.app_salary_caveat}</div>
        </>
      )}
    </Card>
  );
};

