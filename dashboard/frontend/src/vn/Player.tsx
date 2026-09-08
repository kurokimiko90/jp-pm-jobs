import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { C, alpha, RADIUS, SHADOW } from '../theme';
import { useIsNarrow } from '../components/ui';
import {
  favorAt, flattenScript,
  type VNEmotion, type VNLang, type VNScript,
} from './types';
import { VN_UI } from './ui';
import { VNAvatar } from './Avatar';
import { ReportScreen } from './ReportScreen';

// 全屏視覺小說播放器：打字機、好感度、內心獨白、解説モード、章節選單、自動播放。

interface PlayerProps {
  script: VNScript;
  lang: VNLang;
  setLang: (l: VNLang) => void;
  kaisetsu: boolean;
  setKaisetsu: (v: boolean) => void;
  onExit: () => void;
}

type AutoMode = 0 | 1 | 2;

const TYPE_MS = 30;
const TYPE_MS_FAST = 14;
const SHOW_ALL = 99999;

const favorColor = (v: number): string =>
  v >= 70 ? C.gold : v >= 40 ? C.milkTea : C.rosePink;

/** 面試官迷你表情（好感度指示器用） */
const FavorFace: React.FC<{ favor: number }> = ({ favor }) => {
  const mouth = favor >= 70
    ? 'M7.5 13 Q11 16 14.5 13'
    : favor >= 40
      ? 'M8 13.5 L14 13.5'
      : 'M7.5 14.5 Q11 12 14.5 14.5';
  return (
    <svg viewBox="0 0 22 22" width={20} height={20} aria-hidden="true">
      <circle cx={11} cy={11} r={10} fill="#F2D8BE" stroke="#C9B18E" />
      <circle cx={7.5} cy={9} r={1.3} fill="#3A322B" />
      <circle cx={14.5} cy={9} r={1.3} fill="#3A322B" />
      <path d={mouth} stroke="#9C5340" strokeWidth={1.5} fill="none" strokeLinecap="round" />
    </svg>
  );
};

/** 面接室背景（CSS 幾何造型） */
const Room: React.FC = () => (
  <div style={{ position: 'absolute', inset: 0 }} aria-hidden="true">
    <div style={{
      position: 'absolute', inset: 0,
      background: 'linear-gradient(180deg,#EFE3D0 0%,#E8D8C0 58%,#D9C4A6 58.2%,#CBB08D 100%)',
    }} />
    {/* 窓 */}
    <div style={{
      position: 'absolute', left: '7%', top: '10%', width: '24%', height: '32%',
      borderRadius: 10, background: 'linear-gradient(160deg,#FAF0DD,#F0DCBC)',
      boxShadow: 'inset 0 0 0 6px #C9B18E', opacity: 0.95,
    }} />
    <div style={{
      position: 'absolute', left: 'calc(7% + 12%)', top: '10%', width: 6, height: '32%',
      background: '#C9B18E',
    }} />
    {/* 観葉植物 */}
    <div style={{ position: 'absolute', right: '4%', bottom: '20%', width: 40, height: 64 }}>
      <div style={{ position: 'absolute', bottom: 0, left: 8, width: 24, height: 26, background: '#B08A5E', borderRadius: '4px 4px 8px 8px' }} />
      <div style={{ position: 'absolute', bottom: 20, left: 0, width: 18, height: 40, background: '#7E9468', borderRadius: '50% 50% 20% 60%', transform: 'rotate(-16deg)' }} />
      <div style={{ position: 'absolute', bottom: 22, right: 0, width: 18, height: 44, background: '#8CA276', borderRadius: '50% 50% 60% 20%', transform: 'rotate(14deg)' }} />
    </div>
    {/* テーブル */}
    <div style={{
      position: 'absolute', left: '50%', transform: 'translateX(-50%)', bottom: 0,
      width: '116%', height: '21%',
      background: 'linear-gradient(180deg,#BC9E75 0%,#A9895F 100%)',
      borderRadius: '48% 48% 0 0 / 26% 26% 0 0',
      boxShadow: '0 -4px 14px rgba(90,82,72,0.18)',
    }} />
  </div>
);

export const VNPlayer: React.FC<PlayerProps> = ({
  script, lang, setLang, kaisetsu, setKaisetsu, onExit,
}) => {
  const ui = VN_UI[lang];
  const isNarrow = useIsNarrow();

  const flat = useMemo(() => flattenScript(script), [script]);
  const actStarts = useMemo(() => {
    const starts: number[] = [];
    let acc = 0;
    for (const act of script.acts) { starts.push(acc); acc += act.lines.length; }
    return starts;
  }, [script]);

  const [phase, setPhase] = useState<'play' | 'report'>('play');
  const [idx, setIdx] = useState(0);
  const [shown, setShown] = useState(0);
  const [auto, setAuto] = useState<AutoMode>(0);
  const [chapterOpen, setChapterOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [hintSeen, setHintSeen] = useState(false);
  const [voiceOn, setVoiceOn] = useState(true);
  const [audioDone, setAudioDone] = useState(true);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const line = flat[idx];
  const text = lang === 'ja' ? line.ja : line.zh;
  const chars = useMemo(() => Array.from(text), [text]);
  const done = shown >= chars.length;
  const isInner = line.type === 'inner';

  // 打字機（打完後 interval 空轉、setState 同值不觸發 re-render）
  useEffect(() => {
    if (phase !== 'play') return;
    const iv = setInterval(
      () => setShown((s) => (s < chars.length ? s + 1 : s)),
      auto === 2 ? TYPE_MS_FAST : TYPE_MS,
    );
    return () => clearInterval(iv);
  }, [chars, auto, phase]);

  // 語言切換：當前句瞬間顯示完整（不重打）
  const langRef = useRef(lang);
  useEffect(() => {
    if (langRef.current !== lang) { langRef.current = lang; setShown(SHOW_ALL); }
  }, [lang]);

  const goTo = useCallback((i: number, full = false) => {
    setIdx(i);
    setShown(full ? SHOW_ALL : 0);
  }, []);

  const advance = useCallback(() => {
    if (phase !== 'play' || chapterOpen || menuOpen) return;
    setHintSeen(true);
    if (!done) { setShown(SHOW_ALL); return; }
    if (idx + 1 < flat.length) goTo(idx + 1);
    else setPhase('report');
  }, [phase, chapterOpen, menuOpen, done, idx, flat.length, goTo]);

  const back = useCallback(() => {
    if (phase !== 'play' || chapterOpen || menuOpen) return;
    if (idx > 0) goTo(idx - 1, true);
  }, [phase, chapterOpen, menuOpen, idx, goTo]);

  // 桌機：空白鍵推進、← 上一句
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.code === 'Space' || e.code === 'Enter') { e.preventDefault(); advance(); }
      else if (e.code === 'ArrowLeft') back();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [advance, back]);

  // 逐句語音：idx 切換時播放該句音檔（與打字機解耦；點擊推進不等語音播完）
  const hasAudio = voiceOn && phase === 'play' && !!script.audioBase && !!line.audio;
  useEffect(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (!hasAudio) { setAudioDone(true); return; }
    setAudioDone(false);
    const audio = new Audio(`${script.audioBase}/${line.audio}`);
    audioRef.current = audio;
    audio.onended = () => setAudioDone(true);
    audio.onerror = () => setAudioDone(true);
    audio.play().catch(() => setAudioDone(true));
    return () => { audio.pause(); audio.onended = null; audio.onerror = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, voiceOn, phase]);

  // 自動播放：有語音時等 audio.onended（可整場聽完），無語音時走固定計時
  useEffect(() => {
    if (phase !== 'play' || auto === 0 || !done || chapterOpen) return;
    if (hasAudio && !audioDone) return;
    const to = setTimeout(advance, hasAudio ? 400 : auto === 1 ? 2000 : 1000);
    return () => clearTimeout(to);
  }, [phase, auto, done, chapterOpen, advance, hasAudio, audioDone]);

  // 好感度：導出值（該句顯示完才計入），回退/跳章自動一致
  const favor = favorAt(flat, done ? idx : idx - 1);

  const emotionOf = useCallback((who: 'interviewer' | 'candidate'): VNEmotion => {
    for (let i = idx; i >= 0; i--) {
      const l = flat[i];
      if ((who === 'interviewer') === (l.speaker === 'interviewer')) return l.emotion;
    }
    return 'neutral';
  }, [flat, idx]);

  const activeSide: 'interviewer' | 'candidate' =
    line.speaker === 'interviewer' ? 'interviewer' : 'candidate';

  // 情緒驅動演出：serious 微暗+zoom、relieved 回暖
  const mood = line.emotion;
  const stageFilter = mood === 'serious'
    ? 'brightness(0.9) saturate(0.88)'
    : mood === 'nervous'
      ? 'brightness(0.96)'
      : mood === 'relieved'
        ? 'brightness(1.04) saturate(1.06)'
        : 'none';

  const interviewerName = lang === 'ja' ? script.interviewerJa : script.interviewerZh;
  const act = script.acts[line.actIdx];
  const displayText = chars.slice(0, shown).join('');

  const pillBtn = (active: boolean): React.CSSProperties => ({
    borderRadius: RADIUS.pill,
    border: `1px solid ${alpha('#FFFFFF', 0.3)}`,
    background: active ? alpha(C.gold, 0.92) : alpha('#2E2822', 0.42),
    color: active ? '#3F382F' : '#F5EFE6',
    fontSize: 12,
    fontWeight: 700,
    padding: '6px 12px',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  });

  const avatarSize = isNarrow ? 118 : 176;
  const talking = !done && line.type === 'talk';
  const nextLine = flat[idx + 1];
  const nextLabel = !nextLine
    ? ui.nextReport
    : nextLine.type === 'talk' && nextLine.speaker === 'candidate'
      ? ui.nextAnswer
      : ui.nextHint;
  const sideStyle = (who: 'interviewer' | 'candidate'): React.CSSProperties => {
    const active = activeSide === who;
    // 聽者反應：候選人答完，面試官輕輕點頭
    const nodding = who === 'interviewer' && !active && done && line.speaker === 'candidate';
    return {
      transition: 'filter 450ms ease, opacity 450ms ease, transform 450ms ease',
      filter: active ? 'none' : 'grayscale(0.3) brightness(0.8)',
      opacity: active ? 1 : 0.55,
      transform: active ? 'scale(1.05)' : 'scale(0.96)',
      // 說話者輕微起伏（生命感）
      animation: active && talking
        ? 'vnBobActive 640ms ease-in-out infinite'
        : nodding ? 'vnNod 1100ms ease-in-out 1' : undefined,
    };
  };

  // portal 到 body：頁面根元素的 riseIn transform 會困住 position:fixed
  return createPortal(
    <div
      onClick={advance}
      style={{
        position: 'fixed', inset: 0, zIndex: 120, overflow: 'hidden',
        background: '#2E2822', cursor: 'pointer', userSelect: 'none',
        animation: 'vnFadeIn 400ms ease-out both',
      }}
    >
      {/* 舞台（情緒濾鏡 + 危機幕震動） */}
      <div style={{
        position: 'absolute', inset: 0,
        transition: 'filter 650ms ease, transform 750ms ease',
        filter: stageFilter,
        transform: mood === 'serious' ? 'scale(1.03)' : 'scale(1)',
        animation: line.fx === 'shake' ? 'vnShake 420ms ease-out' : undefined,
      }}>
        <Room />
        {/* 角色立繪：說話者高亮、非說話者微暗 */}
        <div style={{
          position: 'absolute', left: 0, right: 0,
          bottom: isNarrow ? '31%' : '24%',
          display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
          padding: isNarrow ? '0 8%' : '0 22%', pointerEvents: 'none',
        }}>
          <div style={sideStyle('interviewer')}>
            <VNAvatar
              persona="interviewer"
              emotion={emotionOf('interviewer')}
              talking={talking && activeSide === 'interviewer'}
              size={avatarSize}
            />
          </div>
          <div style={sideStyle('candidate')}>
            <VNAvatar
              persona="candidate"
              emotion={emotionOf('candidate')}
              talking={talking && activeSide === 'candidate'}
              size={avatarSize}
            />
          </div>
        </div>
      </div>

      {/* 頂欄：幕數 + 好感度 ／ 控制列 */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'absolute', top: 0, left: 0, right: 0,
          padding: 'max(10px, env(safe-area-inset-top)) 12px 8px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
          gap: 8, flexWrap: 'wrap', cursor: 'default',
        }}
      >
        {/* 左：幕標題＋句數進度＋練習目標＋好感度 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{
              borderRadius: RADIUS.pill, background: alpha('#2E2822', 0.55),
              color: '#F5EFE6', fontSize: 12, fontWeight: 700, padding: '6px 14px',
              letterSpacing: 1,
            }}>
              {lang === 'ja' ? act.titleJa : act.titleZh}
              <span style={{ marginLeft: 10, opacity: 0.75, fontVariantNumeric: 'tabular-nums' }}>
                {idx + 1} / {flat.length}
              </span>
            </span>
            {/* 好感度指示器（低調；面接パック自動台本は非表示 — 評分不編造） */}
            {!script.noFavor && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 7,
              borderRadius: RADIUS.pill, background: alpha('#2E2822', 0.55),
              padding: '4px 12px 4px 6px',
            }}>
              <FavorFace favor={favor} />
              <span style={{ width: 64, height: 5, borderRadius: 999, background: alpha('#FFFFFF', 0.22), overflow: 'hidden' }}>
                <span style={{
                  display: 'block', height: '100%', borderRadius: 999,
                  width: `${favor}%`, background: favorColor(favor),
                  transition: 'width 700ms cubic-bezier(0.22,1,0.36,1), background 700ms ease',
                }} />
              </span>
            </span>
            )}
            {/* 回答品質即時回饋：好感度增減浮出 */}
            {!script.noFavor && done && line.favorDelta != null && (
              <span
                key={line.id}
                style={{
                  borderRadius: RADIUS.pill,
                  background: line.favorDelta > 0 ? alpha(C.gold, 0.95) : alpha(C.rosePink, 0.95),
                  color: '#3F382F', fontSize: 12, fontWeight: 800, padding: '4px 10px',
                  fontVariantNumeric: 'tabular-nums',
                  animation: 'vnPop 360ms cubic-bezier(0.34,1.56,0.64,1) both',
                }}
              >
                {line.favorDelta > 0 ? `+${line.favorDelta}` : line.favorDelta}
              </span>
            )}
          </div>
          {(lang === 'ja' ? act.goalJa : act.goalZh) && (
            <span style={{
              alignSelf: 'flex-start',
              borderRadius: RADIUS.pill, background: alpha('#2E2822', 0.4),
              color: alpha('#F5EFE6', 0.9), fontSize: 11.5, fontWeight: 600,
              padding: '4px 12px', letterSpacing: 0.5,
            }}>
              🎯 {lang === 'ja' ? act.goalJa : act.goalZh}
            </span>
          )}
        </div>
        {/* 右：核心操作（次要功能收進 ≡） */}
        <div style={{ position: 'relative', display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {script.audioBase && (
            <button onClick={() => setVoiceOn(!voiceOn)} style={pillBtn(voiceOn)}>
              🔊 {ui.voice} {voiceOn ? 'ON' : 'OFF'}
            </button>
          )}
          <button onClick={() => setKaisetsu(!kaisetsu)} style={pillBtn(kaisetsu)}>
            💡 {ui.kaisetsu} {kaisetsu ? 'ON' : 'OFF'}
          </button>
          <span style={{
            display: 'inline-flex', borderRadius: RADIUS.pill, overflow: 'hidden',
            border: `1px solid ${alpha('#FFFFFF', 0.3)}`,
          }}>
            {(['ja', 'zh'] as const).map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                style={{
                  border: 'none', fontSize: 12, fontWeight: 700, padding: '6px 12px', cursor: 'pointer',
                  background: lang === l ? alpha(C.gold, 0.92) : alpha('#2E2822', 0.42),
                  color: lang === l ? '#3F382F' : '#F5EFE6',
                }}
              >
                {l === 'ja' ? 'JP' : '中'}
              </button>
            ))}
          </span>
          <button
            onClick={() => setAuto(((auto + 1) % 3) as AutoMode)}
            style={pillBtn(auto > 0)}
          >
            {auto === 0 ? `▶ ${ui.auto}` : auto === 1 ? `▶ ${ui.auto}` : `▶▶ ${ui.autoFast}`}
          </button>
          <button onClick={() => setMenuOpen(!menuOpen)} style={pillBtn(menuOpen)} aria-label={ui.menu}>
            ≡
          </button>
          {menuOpen && (
            <div style={{
              position: 'absolute', top: '110%', right: 0, zIndex: 20,
              display: 'flex', flexDirection: 'column', gap: 4,
              background: alpha('#2E2822', 0.92), borderRadius: RADIUS.md,
              padding: 8, minWidth: 160, boxShadow: SHADOW.md,
              animation: 'vnFadeUp 200ms ease-out both',
            }}>
              {([
                [ui.chapters, () => { setChapterOpen(true); setMenuOpen(false); }],
                [ui.prev, () => { setMenuOpen(false); if (idx > 0) goTo(idx - 1, true); }],
                [`⟲ ${ui.restart}`, () => { setMenuOpen(false); goTo(0); }],
                [`✕ ${ui.exit}`, onExit],
              ] as [string, () => void][]).map(([label, fn]) => (
                <button
                  key={label}
                  onClick={fn}
                  style={{
                    border: 'none', borderRadius: RADIUS.sm, background: 'transparent',
                    color: '#F5EFE6', fontSize: 13, fontWeight: 600, padding: '8px 12px',
                    textAlign: 'left', cursor: 'pointer',
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 學習面板：解説 ON 時常駐（考察重點＋回答框架），OFF 時不干擾觀影 */}
      {kaisetsu && !isNarrow && (act.tags || act.framework) && (
        <div style={{
          position: 'absolute', left: '50%', transform: 'translateX(-50%)', top: 108,
          width: 340, display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center',
          pointerEvents: 'none', animation: 'vnFadeIn 400ms ease-out both',
        }}>
          {act.tags && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'center' }}>
              {act.tags.map((t) => (
                <span key={t.ja} style={{
                  borderRadius: RADIUS.pill, background: alpha('#2E2822', 0.5),
                  color: '#F5EFE6', fontSize: 11.5, fontWeight: 700, padding: '4px 12px',
                }}>
                  {lang === 'ja' ? t.ja : t.zh}
                </span>
              ))}
            </div>
          )}
          {act.framework && (
            <div style={{
              width: '100%', borderRadius: RADIUS.md,
              background: alpha('#2E2822', 0.45), padding: '12px 16px',
            }}>
              <div style={{
                fontSize: 11.5, fontWeight: 700, letterSpacing: 1.5,
                color: alpha('#F5EFE6', 0.75), marginBottom: 6,
              }}>
                {lang === 'ja' ? act.framework.titleJa : act.framework.titleZh}
              </div>
              {act.framework.items.map((it) => (
                <div key={it.ja} style={{ fontSize: 12.5, lineHeight: 1.9, color: '#F5EFE6' }}>
                  ・{lang === 'ja' ? it.ja : it.zh}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 底部：解説卡 + 對話框 */}
      <div style={{
        position: 'absolute', left: 0, right: 0, bottom: 0,
        padding: '0 12px calc(12px + env(safe-area-inset-bottom))',
        display: 'flex', justifyContent: 'center',
      }}>
        <div style={{ width: '100%', maxWidth: 780, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* 影子練習提示：面試官問完、示範回答前，先自己開口 */}
          {kaisetsu && done && line.speaker === 'interviewer'
            && nextLine?.type === 'talk' && nextLine.speaker === 'candidate' && (
            <div style={{
              alignSelf: 'center',
              borderRadius: RADIUS.pill, background: alpha('#2E2822', 0.62),
              color: '#F5EFE6', fontSize: 12.5, fontWeight: 700, padding: '6px 16px',
              animation: 'vnFadeUp 260ms ease-out both',
            }}>
              {ui.shadowHint}
            </div>
          )}

          {/* 解説モード：ON 時示範回答結束後直接展開（不需再點） */}
          {done && kaisetsu && line.note && (
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                background: alpha('#FFF8F0', 0.97),
                border: `1.5px solid ${C.gold}`, borderRadius: RADIUS.md,
                padding: '12px 16px', boxShadow: SHADOW.md, cursor: 'default',
                animation: 'vnFadeUp 260ms ease-out both',
              }}
            >
              <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1.5, color: C.amber, marginBottom: 6 }}>
                💡 {ui.noteTitle}
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.75, color: C.ink }}>
                {lang === 'ja' ? line.note.ja : line.note.zh}
              </div>
            </div>
          )}

          {/* 對話框 / 內心獨白 */}
          {isInner ? (
            <div style={{
              minHeight: isNarrow ? 84 : 96,
              borderRadius: RADIUS.md,
              background: alpha('#2E2822', 0.38),
              padding: '20px 22px 24px',
              textAlign: 'center',
              fontStyle: 'italic',
              fontSize: isNarrow ? 14.5 : 16,
              lineHeight: 1.95,
              letterSpacing: 0.5,
              color: alpha('#F3EBFF', 0.88),
              position: 'relative',
            }}>
              {displayText}
              {done && (
                <span style={{
                  position: 'absolute', right: 14, bottom: 8,
                  borderRadius: RADIUS.pill, background: alpha('#2E2822', 0.5),
                  color: alpha('#F5EFE6', 0.95), fontSize: 12, fontWeight: 700,
                  fontStyle: 'normal', padding: '4px 12px',
                  animation: 'vnBounce 1.2s ease-in-out infinite',
                }}>
                  {nextLabel}
                </span>
              )}
            </div>
          ) : (
            <div style={{
              position: 'relative',
              minHeight: isNarrow ? 84 : 96,
              background: alpha('#FFF8F0', 0.96),
              border: `1.5px solid ${alpha(C.milkTea, 0.95)}`,
              borderRadius: 18,
              boxShadow: SHADOW.md,
              padding: '20px 22px 22px',
            }}>
              <span style={{
                position: 'absolute', top: -13, left: 18,
                borderRadius: RADIUS.pill,
                background: line.speaker === 'interviewer' ? C.appliedInk : C.gold,
                color: line.speaker === 'interviewer' ? '#FFF' : C.ink,
                fontSize: 12, fontWeight: 700, letterSpacing: 1, padding: '4px 14px',
                boxShadow: SHADOW.sm,
              }}>
                {line.speaker === 'interviewer' ? interviewerName : ui.me}
              </span>
              <div style={{
                fontSize: isNarrow ? 15 : 16.5,
                lineHeight: 1.9,
                letterSpacing: 0.4,
                color: '#3F382F',
              }}>
                {displayText}
              </div>
              {done && (
                <span style={{
                  position: 'absolute', right: 14, bottom: -12,
                  borderRadius: RADIUS.pill, background: C.gold,
                  color: C.ink, fontSize: 12, fontWeight: 700, padding: '5px 14px',
                  boxShadow: SHADOW.sm,
                  animation: 'vnBounce 1.2s ease-in-out infinite',
                }}>
                  {nextLabel}
                </span>
              )}
            </div>
          )}

          {/* 首句操作引導 */}
          {!hintSeen && (
            <div style={{
              alignSelf: 'center',
              borderRadius: RADIUS.pill, background: alpha('#2E2822', 0.62),
              color: '#F5EFE6', fontSize: 12.5, fontWeight: 600,
              padding: '6px 16px', letterSpacing: 0.5,
              animation: 'vnBlink 2s ease-in-out infinite',
            }}>
              👆 {ui.startHint}
            </div>
          )}
        </div>
      </div>

      {/* 章節選單 */}
      {chapterOpen && (
        <div
          onClick={(e) => { e.stopPropagation(); setChapterOpen(false); }}
          style={{
            position: 'absolute', inset: 0, zIndex: 10,
            background: alpha('#2E2822', 0.6),
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'default',
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '90%', maxWidth: 430, background: C.bg,
              borderRadius: RADIUS.lg, boxShadow: SHADOW.lg, padding: '22px 22px 18px',
              animation: 'vnFadeUp 280ms ease-out both',
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: C.sub, marginBottom: 14 }}>
              {ui.chaptersTitle}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {script.acts.map((a, ai) => (
                <button
                  key={a.id}
                  onClick={() => { setPhase('play'); goTo(actStarts[ai]); setChapterOpen(false); }}
                  style={{
                    textAlign: 'left', borderRadius: RADIUS.sm,
                    border: `1px solid ${alpha(C.milkTea, 0.6)}`,
                    background: ai === line.actIdx ? alpha(C.gold, 0.2) : C.card,
                    padding: '10px 14px', fontSize: 14, fontWeight: 600,
                    color: C.ink, cursor: 'pointer',
                  }}
                >
                  {lang === 'ja' ? a.titleJa : a.titleZh}
                </button>
              ))}
            </div>
            <div style={{ textAlign: 'right', marginTop: 12 }}>
              <button
                onClick={() => setChapterOpen(false)}
                style={{ border: 'none', background: 'none', color: C.sub, fontSize: 13, cursor: 'pointer' }}
              >
                {ui.close}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 結算：面接評価レポート */}
      {phase === 'report' && (
        <ReportScreen
          script={script}
          lang={lang}
          finalFavor={favorAt(flat, flat.length - 1)}
          onReplay={() => { setPhase('play'); goTo(0); }}
          onChapters={() => { setPhase('play'); setChapterOpen(true); }}
          onExit={onExit}
        />
      )}
    </div>,
    document.body,
  );
};
