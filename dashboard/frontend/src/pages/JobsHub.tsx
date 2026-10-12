import React from 'react';
import { C, TYPE, SPACE } from '../theme';
import { useT } from '../i18n';
import { useHashRoute } from '../filters/useHashFilters';
import { Jobs } from './Jobs';
import { Recommend } from './Recommend';
import { Reports } from './Reports';

type Tab = 'all' | 'recommend' | 'reports';

/** 「職缺判定」— Jobs / Recommend / Reports 三頁合併為單頁三視角。
 * tab 進 hash 子路徑（#/jobs/recommend），可書籤、返回鍵可用。 */
export const JobsHub: React.FC<{ openJob: (id: number) => void; selected: number | null }> = ({ openJob, selected }) => {
  const t = useT();
  const [route, navigate] = useHashRoute();
  const sub = route[1];
  const tab: Tab = sub === 'recommend' || sub === 'reports' ? sub : 'all';

  const TABS: { key: Tab; label: string }[] = [
    { key: 'all', label: t.nav_jobs },
    { key: 'recommend', label: t.nav_recommend },
    { key: 'reports', label: t.nav_reports },
  ];

  return (
    <div>
      <div className="hub-tabs" style={{
        display: 'flex', gap: SPACE.lg, borderBottom: `1px solid ${C.border}`,
        marginBottom: SPACE.xl,
      }}>
        {TABS.map((tb) => (
          <button
            key={tb.key}
            onClick={() => navigate(tb.key === 'all' ? ['jobs'] : ['jobs', tb.key])}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              padding: `${SPACE.sm}px 2px ${SPACE.sm - 2}px`,
              fontSize: TYPE.body, fontWeight: tab === tb.key ? 700 : 400,
              color: tab === tb.key ? C.ink : C.sub,
              borderBottom: tab === tb.key ? `2px solid ${C.gold}` : '2px solid transparent',
              marginBottom: -1,
            }}
          >
            {tb.label}
          </button>
        ))}
      </div>

      {tab === 'all' && <Jobs openJob={openJob} selected={selected} />}
      {tab === 'recommend' && <Recommend openJob={openJob} />}
      {tab === 'reports' && <Reports openJob={openJob} />}
    </div>
  );
};
