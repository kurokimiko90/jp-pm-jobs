import React, { useMemo, useRef, useState } from 'react';
import { C } from '../theme';
import { useT } from '../i18n';

/**
 * JD 全文ビューア。
 *
 * `scrapers/recruiter_agent.py` の `structure_jd_lines()` が入れた
 * `## 大区分` / `**項目**` マーカーを読み、目次チップ + 折りたたみ可能な
 * セクションとして描画する。マーカーが無い JD（メール由来の短文など）は
 * そのまま 1 ブロックの本文として出すので、どの source でも壊れない。
 *
 * 本文は Markdown ではなく `pre-wrap` で描画する。r-agent の本文は
 * 「【必須】」「・」「■」で改行を意味付けしており、Markdown だと単一改行が
 * 潰れて 1 段落に繋がってしまうため。
 */

interface JdField {
  label: string | null;
  body: string;
}

interface JdSection {
  title: string | null;
  fields: JdField[];
}

interface JdAnchor {
  id: string;
  label: string;
  sectionIndex: number;
  isKey: boolean;
}

/**
 * 応募判断で最初に読む項目。目次に出し、ラベルも強調する。
 * 「年収」はドロワー上部の見出しと重複するので入れない（詳細は「給与」側）。
 * 後半は Indeed 側（`scrapers/indeed_jp.py` の `structure_indeed_jd()`）の語彙。
 */
const KEY_FIELDS = new Set([
  '仕事内容', '求める能力・経験', '企業・求人の特色', '給与',
  '想定年収', '年収・給与', '必須', '必須資格・経験',
]);

/** JobDrawer のタブ内 padding。sticky な目次を左右いっぱいに敷くための相殺値。 */
const GUTTER = 20;
/** 目次の高さが測れないときの退避値（チップ 2 行ぶん）。 */
const NAV_OFFSET_FALLBACK = 76;

const SECTION_RE = /^##\s+(.+)$/;
const FIELD_RE = /^\*\*(.+)\*\*$/;

export function parseJd(raw: string): JdSection[] {
  const sections: JdSection[] = [];
  let title: string | null = null;
  let fields: JdField[] = [];
  let label: string | null = null;
  let bodyLines: string[] = [];

  const flushField = (): void => {
    if (label !== null || bodyLines.length) {
      fields = [...fields, { label, body: bodyLines.join('\n').trim() }];
    }
    label = null;
    bodyLines = [];
  };
  const flushSection = (): void => {
    flushField();
    if (title !== null || fields.length) sections.push({ title, fields });
    title = null;
    fields = [];
  };

  for (const line of raw.split('\n')) {
    const trimmed = line.trim();
    const sectionMatch = SECTION_RE.exec(trimmed);
    const fieldMatch = FIELD_RE.exec(trimmed);
    if (sectionMatch) {
      flushSection();
      title = sectionMatch[1].trim();
    } else if (fieldMatch) {
      flushField();
      label = fieldMatch[1].trim();
    } else if (trimmed) {
      bodyLines = [...bodyLines, trimmed];
    }
  }
  flushSection();
  // 値が空のラベル（テンプレート上あるだけの「制度」等）と、その結果中身が
  // 無くなったセクション（`【選考・企業概要】` タブ見出しなど）は落とす。
  return sections
    .map((s) => ({ ...s, fields: s.fields.filter((f) => f.body.length > 0) }))
    .filter((s) => s.fields.length > 0);
}

function buildAnchors(sections: JdSection[]): JdAnchor[] {
  const anchors: JdAnchor[] = [];
  sections.forEach((section, si) => {
    if (section.title) {
      anchors.push({ id: `jd-s${si}`, label: section.title, sectionIndex: si, isKey: false });
    }
    section.fields.forEach((field, fi) => {
      if (field.label && KEY_FIELDS.has(field.label)) {
        anchors.push({ id: `jd-s${si}f${fi}`, label: field.label, sectionIndex: si, isKey: true });
      }
    });
  });
  return anchors;
}

const NavChip: React.FC<{ anchor: JdAnchor; onJump: (a: JdAnchor) => void }> = ({ anchor, onJump }) => (
  <button
    onClick={() => onJump(anchor)}
    style={{
      border: `1px solid ${anchor.isKey ? C.gold : C.border}`,
      background: anchor.isKey ? C.gold : C.bg,
      color: C.ink,
      borderRadius: 999,
      padding: '3px 10px',
      fontSize: 11,
      fontWeight: anchor.isKey ? 700 : 500,
      cursor: 'pointer',
      whiteSpace: 'nowrap',
    }}
  >
    {anchor.label}
  </button>
);

export const JdViewer: React.FC<{ raw: string }> = ({ raw }) => {
  const t = useT();
  const sections = useMemo(() => parseJd(raw), [raw]);
  const anchors = useMemo(() => buildAnchors(sections), [sections]);
  const [closed, setClosed] = useState<ReadonlySet<number>>(new Set());
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  const navRef = useRef<HTMLDivElement>(null);

  const titled = sections.filter((s) => s.title !== null);
  const allClosed = titled.length > 0 && closed.size >= titled.length;

  const toggleSection = (index: number): void => {
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const toggleAll = (): void => {
    setClosed(allClosed ? new Set() : new Set(sections.map((_, i) => i).filter((i) => sections[i].title !== null)));
  };

  const jump = (anchor: JdAnchor): void => {
    // 折りたたまれた先へ飛ぶ場合は開いてから。展開は次フレームで反映される。
    setClosed((prev) => {
      if (!prev.has(anchor.sectionIndex)) return prev;
      const next = new Set(prev);
      next.delete(anchor.sectionIndex);
      return next;
    });
    requestAnimationFrame(() => {
      const el = refs.current[anchor.id];
      if (!el) return;
      // 目次はチップの折り返しで 1〜3 行に変わるため、固定値ではなく実測して逃がす。
      // そうしないと飛んだ先の見出しが sticky な目次の裏に隠れる。
      const offset = navRef.current?.offsetHeight ?? NAV_OFFSET_FALLBACK;
      el.style.scrollMarginTop = `${offset + 10}px`;
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  return (
    <div>
      {anchors.length > 0 && (
        <div
          ref={navRef}
          style={{
            position: 'sticky', top: 0, zIndex: 2, background: C.card,
            margin: `0 -${GUTTER}px 12px`, padding: `8px ${GUTTER}px 10px`,
            borderBottom: `1px solid ${C.border}`,
            display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center',
          }}
        >
          {anchors.map((a) => <NavChip key={a.id} anchor={a} onJump={jump} />)}
          <button
            onClick={toggleAll}
            style={{
              marginLeft: 'auto', border: 'none', background: 'none', color: C.sub,
              fontSize: 11, cursor: 'pointer', whiteSpace: 'nowrap', padding: '3px 0',
            }}
          >
            {allClosed ? t.jobdrawer_jd_expand_all : t.jobdrawer_jd_collapse_all}
          </button>
        </div>
      )}

      {sections.map((section, si) => {
        const isClosed = closed.has(si);
        // 見出しの無い先頭ブロック（カード要約：年収/勤務地/休日など）は
        // ドロワー上部と重複するので、控えめなメタ表示にする。
        // ただし大区分が 1 つも無い JD（Indeed の英文求人など）は全体が
        // 見出し無しになるため、本文まで小さい灰字に潰さない。
        if (section.title === null && titled.length > 0) {
          return (
            <div key={si} style={{ marginBottom: 14, fontSize: 11.5, color: C.sub, lineHeight: 1.7 }}>
              {section.fields.map((f, fi) => (
                <div key={fi} ref={(el) => { refs.current[`jd-s${si}f${fi}`] = el; }}
                  style={{ marginBottom: 4 }}>
                  {f.label && <span style={{ fontWeight: 700, color: C.ink, marginRight: 6 }}>{f.label}</span>}
                  {/* 条件タグ（「フレックス勤務」等）が 1 行 1 個で縦に伸びるので横に畳む */}
                  <span>{f.body.split('\n').join(' ・ ')}</span>
                </div>
              ))}
            </div>
          );
        }
        return (
          <div
            key={si}
            ref={(el) => { refs.current[`jd-s${si}`] = el; }}
            style={{
              marginBottom: 12,
              ...(section.title === null ? {} : {
                border: `1px solid ${C.border}`, borderRadius: 10, overflow: 'hidden',
              }),
            }}
          >
            {section.title !== null && (
              <button
                onClick={() => toggleSection(si)}
                style={{
                  width: '100%', display: 'flex', alignItems: 'center', gap: 8,
                  background: C.bg, border: 'none', borderLeft: `3px solid ${C.gold}`,
                  padding: '9px 12px', cursor: 'pointer', textAlign: 'left',
                  fontSize: 13, fontWeight: 700, color: C.ink,
                }}
              >
                <span style={{ color: C.sub, fontSize: 10 }}>{isClosed ? '▶' : '▼'}</span>
                {section.title}
              </button>
            )}
            {!isClosed && (
              <div style={{ padding: section.title === null ? 0 : '10px 12px 12px' }}>
                {section.fields.map((f, fi) => {
                  const isKey = !!f.label && KEY_FIELDS.has(f.label);
                  return (
                    <div
                      key={fi}
                      ref={(el) => { refs.current[`jd-s${si}f${fi}`] = el; }}
                      style={{ marginBottom: 12 }}
                    >
                      {f.label && (
                        <div style={{
                          fontSize: 11.5, fontWeight: 700, marginBottom: 3,
                          color: isKey ? C.ink : C.sub,
                          ...(isKey ? { borderBottom: `2px solid ${C.gold}`, display: 'inline-block', paddingBottom: 1 } : {}),
                        }}>
                          {f.label}
                        </div>
                      )}
                      {f.body && (
                        <div style={{ fontSize: 12.5, lineHeight: 1.75, color: C.ink, whiteSpace: 'pre-wrap' }}>
                          {f.body}
                        </div>
                      )}
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
