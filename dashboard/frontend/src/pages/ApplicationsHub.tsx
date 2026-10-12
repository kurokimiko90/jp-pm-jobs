import React from 'react';
import { C, TYPE, SPACE } from '../theme';
import { useT } from '../i18n';
import { useHashRoute } from '../filters/useHashFilters';
import { Applications } from './Applications';
import { Strategy } from './Strategy';
import { DirectApply } from './DirectApply';
import { ApplicationsTimeline } from './ApplicationsTimeline';

type Tab = 'applications' | 'strategy' | 'direct' | 'timeline';

/** 「應募管線」— Applications（追蹤）+ Strategy（投遞波次規劃）+ DirectApply（官網直投）
 * 合併為單頁三視角。tab 進 hash 子路徑（#/applications/direct），可書籤、返回鍵可用。 */
export const ApplicationsHub: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const [route, navigate] = useHashRoute();
  const sub = route[1];
  const tab: Tab = sub === 'strategy' || sub === 'direct' || sub === 'timeline' ? sub : 'applications';

  const TABS: { key: Tab; label: string }[] = [
    { key: 'applications', label: t.nav_applications },
    { key: 'timeline', label: t.app_timeline_tab },
    { key: 'strategy', label: t.nav_strategy },
    { key: 'direct', label: t.da_tab },
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
            onClick={() => navigate(tb.key === 'applications' ? ['applications'] : ['applications', tb.key])}
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

      {tab === 'applications' && <Applications openJob={openJob} />}
      {tab === 'timeline' && <ApplicationsTimeline />}
      {tab === 'strategy' && <Strategy openJob={openJob} />}
      {tab === 'direct' && <DirectApply openJob={openJob} />}
    </div>
  );
};
