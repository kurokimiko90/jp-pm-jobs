import React from 'react';
import { C } from '../theme';
import { Card, SectionTitle, Empty } from './ui';
import { useT } from '../i18n';
import { RateRow, type RateItem } from './RateRow';

/** 応募リクエスト × 書類通過（app_request_stats.build_apply_request）。 */
export interface ApplyRequestItem {
  job_id: number; company: string; outcome: string; status: string; rejection_stage: string | null;
  requested_at: string; applied_at: string; match: 'offer' | 'company';
}
export interface ApplyRequestStats {
  summary: RateItem & {
    jobs: number; withdrawn: number; mails: number; companies: number;
    not_applied: number; since: string | null;
  };
  control: RateItem & { jobs: number };
  items: ApplyRequestItem[];
  not_applied: { company: string; requested_at: string; title: string | null; mails: number }[];
}

/** リクエスト経由の応募の書類通過率。基準線は同時期・同経路（r-agent）のリクエストなし応募。 */
export const ApplyRequestCard: React.FC<{ data: ApplyRequestStats }> = ({ data }) => {
  const t = useT();
  const { summary: s, control } = data;
  const rows = [s, control].filter((r) => r.decided > 0);
  const scaleMax = Math.min(100, Math.max(20, ...rows.map((r) => r.ci_high ?? 0)) * 1.05);
  return (
    <Card>
      <SectionTitle right={<span style={{ fontSize: 11, color: C.sub }}>
        {t.app_req_note.replace('{since}', s.since ?? '—')}
      </span>}>{t.app_req_title}</SectionTitle>
      {s.mails === 0 ? <Empty>{t.app_req_empty}</Empty> : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            <RateRow label={t.app_req_requested} item={s} baseline={control.rate}
              scaleMax={scaleMax} insufficientText={t.app_stats_insufficient} />
            <RateRow label={t.app_req_control} item={control} baseline={control.rate}
              scaleMax={scaleMax} insufficientText={t.app_stats_insufficient} />
          </div>
          <div style={{ fontSize: 12, color: C.sub, marginTop: 10, lineHeight: 1.7 }}>
            {t.app_req_counts
              .replace('{mails}', String(s.mails)).replace('{companies}', String(s.companies))
              .replace('{jobs}', String(s.jobs)).replace('{passed}', String(s.passed))
              .replace('{failed}', String(s.decided - s.passed)).replace('{pending}', String(s.pending))
              .replace('{unknown}', String(s.unknown)).replace('{withdrawn}', String(s.withdrawn))}
          </div>
          <div style={{ fontSize: 11, color: C.sub, marginTop: 8, lineHeight: 1.6 }}>⚠ {t.app_req_caveat}</div>
        </>
      )}
    </Card>
  );
};
