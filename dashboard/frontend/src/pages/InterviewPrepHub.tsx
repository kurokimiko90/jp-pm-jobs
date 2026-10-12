import React, { Suspense, lazy } from 'react';
import { C, TYPE, SPACE } from '../theme';
import { useT } from '../i18n';
import { useHashRoute } from '../filters/useHashFilters';
import { Dojo } from './Dojo';
import { Theater } from './Theater';

// three.js 約 600KB — 3D タブを開いた時だけ読み込む
const Room3D = lazy(() => import('../room3d/Room3D'));

type Tab = 'practice' | 'theater' | 'room3d';

/** 「面試準備」— 面試劇場（Theater）+ 3D 面接ルーム（Room3D）+ 面試練習（Dojo）。
 * tab 進 hash 子路徑（#/interview-prep/theater），可書籤、返回鍵可用。 */
export const InterviewPrepHub: React.FC = () => {
  const t = useT();
  const [route, navigate] = useHashRoute();
  const sub = route[1];
  const tab: Tab = sub === 'practice' || sub === 'room3d' ? sub : 'theater';

  const TABS: { key: Tab; label: string }[] = [
    { key: 'theater', label: t.nav_theater },
    { key: 'room3d', label: t.nav_room3d },
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
      {tab === 'room3d' && (
        <Suspense fallback={<div style={{ color: C.sub, padding: SPACE.lg }}>Loading 3D…</div>}>
          <Room3D />
        </Suspense>
      )}
    </div>
  );
};
