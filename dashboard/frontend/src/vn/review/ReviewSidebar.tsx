import React from 'react';
import { C, alpha, RADIUS, SHADOW } from '../../theme';
import { type VNUIDict } from '../ui';
import { formatMinutes } from './reviewUtils';

// 復習頁側欄：單一劇本的進度條 + 幕級目次（✓已看／→目前／○待看 + 預估時間 + 收藏星）
// + 已收藏快捷區 + 搜尋。單一劇本 = 選好要復習的劇本後才顯示，不再混雜其他公司的幕。

export interface TocAct { id: string; label: string; seconds: number }
export interface TocGroup { scriptId: string; label: string; acts: TocAct[] }
export interface SearchHit { id: string; actLabel: string; snippet: string }

interface ReviewSidebarProps {
  ui: VNUIDict;
  group: TocGroup;
  activeId: string | null;
  bookmarks: Set<string>;
  onToggleBookmark: (id: string) => void;
  onJump: (id: string) => void;
  isNarrow: boolean;
  query: string;
  onQuery: (q: string) => void;
  hits: SearchHit[];
}

const statusIcon = (status: 'done' | 'current' | 'upcoming'): string =>
  status === 'done' ? '✓' : status === 'current' ? '→' : '○';

export const ReviewSidebar: React.FC<ReviewSidebarProps> = ({
  ui, group, activeId, bookmarks, onToggleBookmark, onJump,
  isNarrow, query, onQuery, hits,
}) => {
  const activeIdx = group.acts.findIndex((a) => a.id === activeId);
  const bookmarkedActs = group.acts.filter((a) => bookmarks.has(a.id));
  const totalSeconds = group.acts.reduce((acc, a) => acc + a.seconds, 0);
  const pct = activeIdx < 0 ? 0 : Math.round(((activeIdx + 0.5) / group.acts.length) * 100);
  const rowDir: React.CSSProperties['flexDirection'] = isNarrow ? 'row' : 'column';

  return (
    <div style={{
      position: 'sticky', top: 0, zIndex: 2,
      display: 'flex', flexDirection: 'column', gap: 10,
      maxHeight: isNarrow ? undefined : 'calc(100vh - 20px)',
      overflowY: isNarrow ? undefined : 'auto',
      background: C.bg, border: `1.5px solid ${alpha(C.gold, 0.5)}`,
      borderRadius: RADIUS.lg, boxShadow: SHADOW.sm,
      padding: isNarrow ? '10px 12px' : '14px 14px',
    }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {group.label}
        </div>
        <div style={{ fontSize: 11, color: C.sub }}>{pct}% · {formatMinutes(totalSeconds)}</div>
        <div style={{ height: 5, borderRadius: 999, background: alpha(C.milkTea, 0.5), overflow: 'hidden', marginTop: 4 }}>
          <div style={{ width: `${pct}%`, height: '100%', background: C.gold, transition: 'width 300ms ease' }} />
        </div>
      </div>

      <input
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder={`🔍 ${ui.reviewSearch}`}
        style={{
          border: `1px solid ${alpha(C.milkTea, 0.7)}`, borderRadius: RADIUS.pill,
          padding: '6px 12px', fontSize: 12.5, color: C.ink, background: C.card,
          outline: 'none',
        }}
      />
      {query && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 220, overflowY: 'auto' }}>
          {hits.length === 0 && (
            <div style={{ fontSize: 12, color: C.sub }}>—</div>
          )}
          {hits.slice(0, 20).map((h, i) => (
            <button
              key={i}
              onClick={() => onJump(h.id)}
              style={{
                textAlign: 'left', border: 'none', borderRadius: RADIUS.sm,
                background: alpha(C.lavender, 0.14), padding: '6px 10px',
                cursor: 'pointer', fontSize: 12,
              }}
            >
              <div style={{ fontWeight: 700, color: C.ink }}>{h.actLabel}</div>
              <div style={{ color: C.sub, fontSize: 11.5 }}>{h.snippet}</div>
            </button>
          ))}
        </div>
      )}

      {bookmarkedActs.length > 0 && (
        <div style={{ display: 'flex', flexDirection: rowDir, flexWrap: 'wrap', gap: 6 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.sub, flexBasis: isNarrow ? 'auto' : '100%' }}>
            ⭐ {ui.reviewBookmarked}
          </div>
          {bookmarkedActs.map((a) => (
            <button
              key={a.id}
              onClick={() => onJump(a.id)}
              style={{
                border: `1px solid ${C.gold}`, borderRadius: RADIUS.pill,
                background: alpha(C.gold, 0.18), padding: '4px 10px',
                fontSize: 11.5, fontWeight: 600, color: C.ink, cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}

      {!isNarrow && (
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 2, color: C.sub }}>
          🗂 {ui.reviewToc}
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: isNarrow ? 'row' : 'column', gap: 4, overflowX: isNarrow ? 'auto' : undefined }}>
        {group.acts.map((a, idx) => {
          const status = idx < activeIdx ? 'done' : idx === activeIdx ? 'current' : 'upcoming';
          const starred = bookmarks.has(a.id);
          return (
            <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
              <button
                onClick={() => onJump(a.id)}
                style={{
                  flex: 1, textAlign: 'left', borderRadius: RADIUS.sm,
                  border: `1px solid ${alpha(C.milkTea, 0.6)}`,
                  background: status === 'current' ? alpha(C.gold, 0.22) : C.card,
                  padding: '6px 10px', fontSize: 12.5, fontWeight: 600,
                  color: status === 'upcoming' ? C.sub : C.ink, cursor: 'pointer',
                  whiteSpace: isNarrow ? 'nowrap' : undefined,
                }}
              >
                {statusIcon(status)} {a.label}
                <span style={{ marginLeft: 6, fontSize: 10.5, color: C.sub, fontWeight: 400 }}>
                  {formatMinutes(a.seconds)}
                </span>
              </button>
              <button
                onClick={() => onToggleBookmark(a.id)}
                title="⭐"
                style={{
                  border: 'none', background: 'transparent', cursor: 'pointer',
                  fontSize: 13, opacity: starred ? 1 : 0.3, padding: '2px 4px',
                }}
              >
                ⭐
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
};
