import React from 'react';
import { C, TYPE, SPACE } from '../theme';
import { useT } from '../i18n';
import { useHashRoute } from '../filters/useHashFilters';
import { Dojo } from './Dojo';
import { Theater } from './Theater';

type Tab = 'practice' | 'theater';

/** 「面試準備」— 面試練習（Dojo）+ 面試劇場（Theater）合併為單頁雙視角。
 * tab 進 hash 子路徑（#/interview-prep/theater），可書籤、返回鍵可用。 */
export const InterviewPrepHub: React.FC = () => {
  const t = useT();
  const [route, navigate] = useHashRoute();
  const sub = route[1];
  const tab: Tab = sub === 'practice' ? 'practice' : 'theater';

  const TABS: { key: Tab; label: string }[] = [
    { key: 'theater', label: t.nav_theater },
    { key: 'practice', label: t.nav_dojo },
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
            onClick={() => navigate(tb.key === 'theater' ? ['interview-prep'] : ['interview-prep', tb.key])}
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

      {tab === 'practice' && <Dojo />}
      {tab === 'theater' && <Theater />}
    </div>
  );
};
