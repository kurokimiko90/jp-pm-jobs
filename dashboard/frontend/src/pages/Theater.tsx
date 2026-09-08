import React, { useEffect, useState } from 'react';
import { C, alpha, RADIUS, SHADOW } from '../theme';
import { get, post } from '../api';
import { useLang } from '../i18n';
import { VN_SCRIPTS } from '../vn/scripts';
import { type VNLang, type VNScript } from '../vn/types';
import { VN_UI } from '../vn/ui';
import { VNPlayer } from '../vn/Player';
import { ReviewPage } from '../vn/ReviewPage';

// 面接シアター：視覺小說式 PM 模擬面試。
// 手寫台本（src/vn/scripts.ts）＋ 各社面接パック自動台本（/api/theater、逐句語音）。

interface TheaterPack {
  dirname: string;
  job_id: number | null;
  company: string;
  job_title: string;
  questions: number | null;
  script_ready: boolean;
  audio_ready: number;
}

const KEYFRAMES = `
@keyframes vnFadeIn { from { opacity: 0 } to { opacity: 1 } }
@keyframes vnFadeUp { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: translateY(0) } }
@keyframes vnPop { 0% { opacity: 0; transform: scale(0.55) } 70% { transform: scale(1.12) } 100% { opacity: 1; transform: scale(1) } }
@keyframes vnShake {
  0%, 100% { transform: scale(1.03) translate(0, 0) }
  20% { transform: scale(1.03) translate(-3px, 1px) }
  40% { transform: scale(1.03) translate(3px, -1px) }
  60% { transform: scale(1.03) translate(-2px, -1px) }
  80% { transform: scale(1.03) translate(2px, 1px) }
}
@keyframes vnBlink { 0%, 100% { opacity: 1 } 50% { opacity: 0.15 } }
@keyframes vnTalkMouth { from { transform: scaleY(1) } to { transform: scaleY(0.45) } }
@keyframes vnBobActive {
  0%, 100% { transform: scale(1.05) translateY(0) }
  50% { transform: scale(1.05) translateY(2px) }
}
@keyframes vnBounce { 0%, 100% { transform: translateY(0) } 50% { transform: translateY(3px) } }
@keyframes vnNod {
  0%, 100% { transform: scale(0.96) translateY(0) }
  25% { transform: scale(0.96) translateY(3px) }
  50% { transform: scale(0.96) translateY(0) }
  75% { transform: scale(0.96) translateY(3px) }
}
`;

export const Theater: React.FC = () => {
  const { lang: dashLang } = useLang();
  // 語言、進度、好感度全走 useState（不落 localStorage）
  const [lang, setLang] = useState<VNLang>(dashLang === 'zh' ? 'zh' : 'ja');
  const [kaisetsu, setKaisetsu] = useState(false);
  const [playingIdx, setPlayingIdx] = useState<number | null>(null);
  const [packs, setPacks] = useState<TheaterPack[]>([]);
  const [archived, setArchived] = useState<{ dirname: string; company: string }[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [packScript, setPackScript] = useState<VNScript | null>(null);
  const [building, setBuilding] = useState<string | null>(null);
  const [reviewMode, setReviewMode] = useState(false);
  const ui = VN_UI[lang];

  const refresh = () =>
    get('/api/theater/scripts')
      .then((r) => { setPacks(r.packs ?? []); setArchived(r.archived ?? []); })
      .catch(() => {});

  useEffect(() => { refresh(); }, []);

  const archivePack = (dirname: string, restore: boolean) =>
    post('/api/theater/archive', { dirname, restore })
      .then(refresh)
      .catch((e) => alert(e instanceof Error ? e.message : String(e)));

  const playPack = async (p: TheaterPack) => {
    if (building) return;
    setBuilding(p.dirname);
    try {
      if (!p.script_ready || p.audio_ready === 0) {
        await post('/api/theater/build', { dirname: p.dirname });
      }
      const r = await get(`/api/theater/script?dirname=${encodeURIComponent(p.dirname)}`);
      setPackScript(r.script);
    } catch (e) {
      alert(`${ui.packLoadFailed}: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBuilding(null);
    }
  };

  const segBtn = (active: boolean): React.CSSProperties => ({
    borderRadius: RADIUS.pill,
    border: active ? 'none' : `1.5px solid ${alpha(C.milkTea, 0.6)}`,
    background: active ? C.gold : C.bg,
    padding: '7px 16px',
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: 0.5,
    cursor: 'pointer',
    color: active ? C.ink : C.sub,
    transition: 'all 200ms ease',
  });

  if (reviewMode) {
    return (
      <div style={{ animation: 'riseIn 180ms ease-out both' }}>
        <style>{KEYFRAMES}</style>
        <ReviewPage
          lang={lang}
          setLang={setLang}
          kaisetsu={kaisetsu}
          setKaisetsu={setKaisetsu}
          onExit={() => setReviewMode(false)}
        />
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 20,
      animation: 'riseIn 180ms ease-out both',
    }}>
      <style>{KEYFRAMES}</style>

      <div>
        <h1 style={{ fontSize: 30, fontWeight: 700, letterSpacing: 2, margin: '0 0 4px' }}>
          {ui.pageTitle}
        </h1>
        <div style={{ fontSize: 14, letterSpacing: 1, color: C.sub }}>
          {ui.pageSub}
        </div>
      </div>

      {/* 語言 / 解説モード 開關 / 復習モード入口 */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={() => setLang('ja')} style={segBtn(lang === 'ja')}>JP</button>
        <button onClick={() => setLang('zh')} style={segBtn(lang === 'zh')}>中</button>
        <span style={{ width: 10 }} />
        <button onClick={() => setKaisetsu(!kaisetsu)} style={segBtn(kaisetsu)}>
          💡 {ui.kaisetsu}{kaisetsu ? ' ON' : ' OFF'}
        </button>
        <span style={{ width: 10 }} />
        <button onClick={() => setReviewMode(true)} style={segBtn(false)}>
          📖 {ui.reviewMode}
        </button>
      </div>

      {/* 各社面接パック（自動台本＋逐句語音） */}
      {(packs.length > 0 || archived.length > 0) && (
        <>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: C.sub }}>
            🎧 {ui.packSection}
          </div>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))',
            gap: 12,
          }}>
            {packs.map((p) => (
              <div
                key={p.dirname}
                role="button"
                onClick={() => { if (!building) playPack(p); }}
                style={{
                  position: 'relative',
                  textAlign: 'left',
                  background: C.bg,
                  border: `1.5px solid ${alpha(C.gold, 0.55)}`,
                  borderRadius: RADIUS.md,
                  boxShadow: SHADOW.sm,
                  padding: 18,
                  cursor: building ? 'wait' : 'pointer',
                  color: C.ink,
                  opacity: building && building !== p.dirname ? 0.55 : 1,
                  transition: 'all 200ms ease',
                }}
              >
                {/* 封存（過期面試從列表移出；檔案保留可還原） */}
                <button
                  onClick={(e) => { e.stopPropagation(); archivePack(p.dirname, false); }}
                  title={ui.archive}
                  style={{
                    position: 'absolute', top: 10, right: 10,
                    border: 'none', background: 'transparent', cursor: 'pointer',
                    fontSize: 14, color: C.sub, padding: 4, lineHeight: 1,
                  }}
                >
                  📦
                </button>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap', paddingRight: 28 }}>
                  {p.questions != null && (
                    <span style={{ fontSize: 12, color: C.sub }}>{p.questions}問</span>
                  )}
                  <span style={{
                    borderRadius: RADIUS.pill, fontSize: 11, fontWeight: 700, padding: '3px 10px',
                    background: p.audio_ready > 0 ? alpha(C.gold, 0.22) : alpha(C.milkTea, 0.35),
                    color: p.audio_ready > 0 ? '#7A5C12' : C.sub,
                  }}>
                    {p.audio_ready > 0 ? `🔊 ${p.audio_ready}句` : ui.packAudioNone}
                  </span>
                </div>
                <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>{p.company}</div>
                <div style={{
                  fontSize: 12, color: C.sub, marginBottom: 12,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {p.job_title || p.dirname}
                </div>
                <span style={{
                  display: 'inline-block', borderRadius: RADIUS.pill,
                  background: C.gold, color: C.ink, fontSize: 13, fontWeight: 700,
                  padding: '7px 18px',
                }}>
                  {building === p.dirname ? ui.packBuilding : ui.play}
                </span>
              </div>
            ))}
          </div>
          {/* 已封存（收合；點展開可還原） */}
          {archived.length > 0 && (
            <div>
              <button
                onClick={() => setShowArchived(!showArchived)}
                style={{
                  border: 'none', background: 'transparent', cursor: 'pointer',
                  fontSize: 12.5, fontWeight: 700, color: C.sub, padding: '2px 0',
                }}
              >
                {showArchived ? '▾' : '▸'} 📦 {ui.archivedSection}（{archived.length}）
              </button>
              {showArchived && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                  {archived.map((a) => (
                    <div key={a.dirname} style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      fontSize: 13, color: C.sub,
                    }}>
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.company}
                        <span style={{ opacity: 0.6, marginLeft: 8, fontSize: 11.5 }}>{a.dirname}</span>
                      </span>
                      <button
                        onClick={() => archivePack(a.dirname, true)}
                        style={{
                          borderRadius: RADIUS.pill, border: `1px solid ${alpha(C.milkTea, 0.7)}`,
                          background: 'transparent', color: C.ink, fontSize: 12, fontWeight: 600,
                          padding: '4px 14px', cursor: 'pointer',
                        }}
                      >
                        {ui.restore}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* 腳本選擇 */}
      <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: C.sub }}>
        {ui.pickTitle}
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))',
        gap: 12,
      }}>
        {VN_SCRIPTS.map((s, i) => {
          const lineCount = s.acts.reduce((acc, a) => acc + a.lines.length, 0);
          return (
            <button
              key={s.id}
              onClick={() => setPlayingIdx(i)}
              style={{
                textAlign: 'left',
                background: C.bg,
                border: `1.5px solid ${alpha(C.milkTea, 0.6)}`,
                borderRadius: RADIUS.md,
                boxShadow: SHADOW.sm,
                padding: 18,
                cursor: 'pointer',
                color: C.ink,
                transition: 'all 200ms ease',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                {s.sample && (
                  <span style={{
                    borderRadius: RADIUS.pill, background: alpha(C.lavender, 0.25),
                    color: '#6E5F8E', fontSize: 11, fontWeight: 700, padding: '3px 10px',
                  }}>
                    {ui.sampleTag}
                  </span>
                )}
                <span style={{ fontSize: 12, color: C.sub }}>
                  {s.acts.length}{ui.acts}・{lineCount}{ui.lines}
                </span>
              </div>
              <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
                {lang === 'ja' ? s.titleJa : s.titleZh}
              </div>
              <div style={{ fontSize: 12, color: C.sub, marginBottom: 12 }}>
                {ui.reportBy}：{lang === 'ja' ? s.interviewerJa : s.interviewerZh}
              </div>
              <span style={{
                display: 'inline-block', borderRadius: RADIUS.pill,
                background: C.gold, color: C.ink, fontSize: 13, fontWeight: 700,
                padding: '7px 18px',
              }}>
                {ui.play}
              </span>
            </button>
          );
        })}
        {/* おまかせ（隨機抽一套） */}
        <button
          onClick={() => setPlayingIdx(Math.floor(Math.random() * VN_SCRIPTS.length))}
          style={{
            background: alpha(C.gold, 0.12),
            border: `1.5px dashed ${C.gold}`,
            borderRadius: RADIUS.md,
            padding: 18,
            cursor: 'pointer',
            color: C.ink,
            fontSize: 15,
            fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            minHeight: 120,
          }}
        >
          🎲 {ui.random}
        </button>
      </div>

      <div style={{ fontSize: 12, color: C.sub, letterSpacing: 0.5 }}>
        {ui.startHint}
      </div>

      {playingIdx != null && (
        <VNPlayer
          key={playingIdx}
          script={VN_SCRIPTS[playingIdx]}
          lang={lang}
          setLang={setLang}
          kaisetsu={kaisetsu}
          setKaisetsu={setKaisetsu}
          onExit={() => setPlayingIdx(null)}
        />
      )}
      {packScript && (
        <VNPlayer
          key={packScript.id}
          script={packScript}
          lang={lang}
          setLang={setLang}
          kaisetsu={kaisetsu}
          setKaisetsu={setKaisetsu}
          onExit={() => setPackScript(null)}
        />
      )}
    </div>
  );
};
