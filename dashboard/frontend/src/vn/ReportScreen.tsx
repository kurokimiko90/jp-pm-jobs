import React from 'react';
import { C, alpha, RADIUS, SHADOW } from '../theme';
import { type VNLang, type VNScript } from './types';
import { VN_UI } from './ui';

// 結算畫面：面接評価レポート。分數逐項彈出（stagger 動畫）。

interface ReportScreenProps {
  script: VNScript;
  lang: VNLang;
  finalFavor: number;
  onReplay: () => void;
  onChapters: () => void;
  onExit: () => void;
}

const SEG_COUNT = 5;

const favorColor = (v: number): string =>
  v >= 70 ? C.gold : v >= 40 ? C.milkTea : C.rosePink;

export const ReportScreen: React.FC<ReportScreenProps> = ({
  script, lang, finalFavor, onReplay, onChapters, onExit,
}) => {
  const ui = VN_UI[lang];
  const interviewer = lang === 'ja' ? script.interviewerJa : script.interviewerZh;

  const btn = (primary: boolean): React.CSSProperties => ({
    borderRadius: RADIUS.pill,
    border: primary ? 'none' : `1.5px solid ${C.border}`,
    background: primary ? C.gold : 'transparent',
    padding: '10px 26px',
    fontSize: 14,
    fontWeight: 700,
    letterSpacing: 1,
    cursor: 'pointer',
    color: C.ink,
  });

  return (
    <div style={{
      position: 'absolute',
      inset: 0,
      overflowY: 'auto',
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'center',
      padding: '28px 16px',
      background: alpha('#3A322B', 0.5),
    }}>
      <div style={{
        width: '100%',
        maxWidth: 620,
        background: C.bg,
        borderRadius: RADIUS.lg,
        boxShadow: SHADOW.lg,
        padding: '30px 30px 26px',
        animation: 'vnFadeUp 420ms cubic-bezier(0.34,1.2,0.64,1) both',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 20 }}>
          <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 3, color: C.sub }}>
            ─ {ui.reportTitle} ─
          </div>
          <div style={{ fontSize: 13, color: C.sub, marginTop: 8 }}>
            {ui.reportBy}：{interviewer}
          </div>
        </div>

        {/* 最終好感度（面接パック自動台本は非表示 — 評分不編造） */}
        {!script.noFavor && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '14px 18px',
          borderRadius: RADIUS.md,
          background: alpha(C.gold, 0.12),
          marginBottom: 22,
          animation: 'vnPop 500ms cubic-bezier(0.34,1.56,0.64,1) 200ms both',
        }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: C.ink }}>{ui.finalFavor}</span>
          <div style={{ flex: 1, height: 8, borderRadius: 999, background: alpha(C.milkTea, 0.5), overflow: 'hidden' }}>
            <div style={{
              width: `${finalFavor}%`,
              height: '100%',
              borderRadius: 999,
              background: favorColor(finalFavor),
              transition: 'width 900ms cubic-bezier(0.22,1,0.36,1)',
            }} />
          </div>
          <span style={{ fontSize: 24, fontWeight: 800, color: C.ink, fontVariantNumeric: 'tabular-nums' }}>
            {finalFavor}
          </span>
        </div>
        )}

        {/* 各面向五段評價 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 22 }}>
          {script.report.aspects.map((a, i) => (
            <div key={a.labelJa} style={{ animation: `vnFadeUp 380ms ease-out ${300 + i * 150}ms both` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 5 }}>
                <span style={{ fontSize: 14, fontWeight: 700, color: C.ink, minWidth: 110 }}>
                  {lang === 'ja' ? a.labelJa : a.labelZh}
                </span>
                <div style={{ display: 'flex', gap: 5 }}>
                  {Array.from({ length: SEG_COUNT }, (_, j) => (
                    <span
                      key={j}
                      style={{
                        width: 22,
                        height: 9,
                        borderRadius: 4,
                        background: j < a.score ? C.gold : alpha(C.milkTea, 0.5),
                        animation: j < a.score
                          ? `vnPop 320ms cubic-bezier(0.34,1.56,0.64,1) ${380 + i * 150 + j * 70}ms both`
                          : undefined,
                      }}
                    />
                  ))}
                </div>
                <span style={{ fontSize: 12, fontWeight: 700, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>
                  {a.score}/5
                </span>
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.7, color: C.sub }}>
                {lang === 'ja' ? a.commentJa : a.commentZh}
              </div>
            </div>
          ))}
        </div>

        {/* 總評 */}
        <div style={{
          borderTop: `1.5px dashed ${alpha(C.milkTea, 0.8)}`,
          paddingTop: 16,
          marginBottom: 24,
          animation: `vnFadeUp 380ms ease-out ${300 + script.report.aspects.length * 150 + 200}ms both`,
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 2, color: C.sub, marginBottom: 8 }}>
            {ui.overall}
          </div>
          <div style={{ fontSize: 14, lineHeight: 1.85, color: C.ink }}>
            {lang === 'ja' ? script.report.overallJa : script.report.overallZh}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
          <button onClick={onReplay} style={btn(true)}>{ui.replay}</button>
          <button onClick={onChapters} style={btn(false)}>{ui.chapters}</button>
          <button onClick={onExit} style={{ ...btn(false), border: 'none', color: C.sub }}>
            {ui.backToTitle}
          </button>
        </div>
      </div>
    </div>
  );
};
