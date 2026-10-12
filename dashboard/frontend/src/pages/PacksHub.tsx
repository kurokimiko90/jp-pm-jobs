import React from 'react';
import { C, TYPE, SPACE } from '../theme';
import { useT } from '../i18n';
import { useHashRoute } from '../filters/useHashFilters';
import { Tailored } from './Tailored';
import { Prep } from './Prep';

type Tab = 'apply' | 'interview';

/** 「文件包」— 投遞包（Tailored）+ 面試包（Prep）合併為單頁雙視角。
 * tab 進 hash 子路徑（#/packs/interview），可書籤、返回鍵可用。 */
export const PacksHub: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const [route, navigate] = useHashRoute();
  const sub = route[1];
  const tab: Tab = sub === 'interview' ? 'interview' : 'apply';

  const TABS: { key: Tab; label: string }[] = [
    { key: 'apply', label: t.nav_tailored },
    { key: 'interview', label: t.nav_prep },
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
            onClick={() => navigate(tb.key === 'apply' ? ['packs'] : ['packs', tb.key])}
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

      {tab === 'apply' && <Tailored openJob={openJob} />}
      {tab === 'interview' && <Prep />}
    </div>
  );
};
