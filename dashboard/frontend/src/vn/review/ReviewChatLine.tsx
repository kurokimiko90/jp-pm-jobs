import React, { useState } from 'react';
import { C, alpha, RADIUS } from '../../theme';
import { type VNLang, type VNLine, type VNScript } from '../types';
import { VN_UI } from '../ui';
import { highlightTokens } from './reviewUtils';

// 復習頁的單句台詞卡：角色配色沿用 VNPlayer 既有語言（面試官=appliedInk／候選人=金）、
// 心の声預設收合、關鍵詞高亮、情緒 emoji。

interface ReviewChatLineProps {
  line: VNLine;
  lang: VNLang;
  kaisetsu: boolean;
  showInner: boolean;
  script: VNScript;
  readingMode: 'full' | 'mine';
}

const EMOTION_EMOJI: Record<string, string> = {
  smile: '😊', think: '🤔', serious: '😐', nervous: '😰', relieved: '😌',
};

const speakerStyle = (speaker: VNLine['speaker']) => {
  if (speaker === 'interviewer') {
    return { bg: alpha(C.appliedInk, 0.09), accent: C.appliedInk, label: C.appliedInk };
  }
  if (speaker === 'inner') {
    return { bg: alpha(C.lavender, 0.16), accent: C.lavender, label: '#6E5F8E' };
  }
  return { bg: alpha(C.gold, 0.16), accent: C.gold, label: '#7A5C12' };
};

const Highlighted: React.FC<{ text: string }> = ({ text }) => (
  <>
    {highlightTokens(text).map((t, i) => {
      if (t.kind === 'plain') return <React.Fragment key={i}>{t.text}</React.Fragment>;
      const color = t.kind === 'digit' ? '#B8860B' : '#5B7FB0';
      return (
        <span key={i} style={{ color, fontWeight: 700 }}>{t.text}</span>
      );
    })}
  </>
);

export const ReviewChatLine: React.FC<ReviewChatLineProps> = ({
  line, lang, kaisetsu, showInner, script, readingMode,
}) => {
  const ui = VN_UI[lang];
  const [forceOpen, setForceOpen] = useState(false);

  if (readingMode === 'mine' && line.speaker !== 'candidate') return null;

  const style = speakerStyle(line.speaker);
  const label = line.speaker === 'interviewer' ? ui.favorLabel
    : line.speaker === 'inner' ? ui.innerName : ui.me;
  const text = lang === 'ja' ? line.ja : line.zh;
  const emoji = line.type === 'talk' ? EMOTION_EMOJI[line.emotion] : undefined;

  const playLine = () => {
    if (!script.audioBase || !line.audio) return;
    new Audio(`${script.audioBase}/${line.audio}`).play().catch(() => {});
  };

  if (line.type === 'inner') {
    const open = showInner || forceOpen;
    return (
      <div style={{
        borderRadius: RADIUS.sm, background: style.bg,
        borderLeft: `3px solid ${style.accent}`, padding: '6px 12px',
      }}>
        {!open ? (
          <button
            onClick={() => setForceOpen(true)}
            style={{
              border: 'none', background: 'transparent', cursor: 'pointer',
              fontSize: 12, color: style.label, fontWeight: 700, padding: 0,
            }}
          >
            ▶ {label}
          </button>
        ) : (
          <div style={{ fontSize: 13, fontStyle: 'italic', opacity: 0.85, color: C.ink }}>
            <span style={{ fontSize: 11.5, fontWeight: 700, color: style.label, marginRight: 8 }}>
              {label}
            </span>
            <Highlighted text={text} />
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex', gap: 10, alignItems: 'flex-start',
      borderRadius: RADIUS.sm, background: style.bg,
      borderLeft: `3px solid ${style.accent}`, padding: '8px 12px',
    }}>
      <span style={{
        flexShrink: 0, minWidth: 52, fontSize: 11.5, fontWeight: 700,
        color: style.label, paddingTop: 2,
      }}>
        {label}{emoji ? ` ${emoji}` : ''}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, lineHeight: 1.7, color: C.ink }}>
          <Highlighted text={text} />
          {script.audioBase && line.audio && (
            <button
              onClick={playLine}
              title="🔊"
              style={{
                border: 'none', background: 'transparent', cursor: 'pointer',
                fontSize: 12, marginLeft: 8, opacity: 0.7,
              }}
            >
              🔊
            </button>
          )}
        </div>
        {line.simulated && kaisetsu && (
          <div style={{ marginTop: 4, fontSize: 11.5, color: C.sub, fontStyle: 'italic' }}>
            ⚠ {lang === 'ja' ? '模擬回答（実際の面接官の回答を優先）' : '模擬回答，實際請以真人為準'}
          </div>
        )}
        {kaisetsu && line.note && (
          <div style={{
            marginTop: 4, fontSize: 12.5, lineHeight: 1.6, color: '#7A5C12',
            background: alpha(C.gold, 0.12), borderRadius: RADIUS.sm,
            padding: '6px 10px',
          }}>
            💡 {lang === 'ja' ? line.note.ja : line.note.zh}
          </div>
        )}
      </div>
    </div>
  );
};
