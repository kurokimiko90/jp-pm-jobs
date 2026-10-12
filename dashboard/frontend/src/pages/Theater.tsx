import React, { useEffect, useState } from 'react';
import { Lightbulb, BookOpen, Headphones, Archive, SpeakerHigh, Play, CaretRight, FilmSlate, DiceFive } from '@phosphor-icons/react';
import './theater.css';
import { PageHeader } from '../components/ui';
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

  // 文言側に「▶」が含まれる（VN プレイヤーと共用）→ アイコンと二重にならないよう除く
  const playLabel = ui.play.replace(/\s*▶\s*$/, '');

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
      animation: 'riseIn 240ms var(--ease-out) both',
    }}>
      <style>{KEYFRAMES}</style>

      <PageHeader
        title={ui.pageTitle}
        subtitle={ui.pageSub}
        right={
          <div className="th-toolbar">
            <div className="th-seg" role="group" aria-label="language">
              <button type="button" aria-pressed={lang === 'ja'} onClick={() => setLang('ja')}>JP</button>
              <button type="button" aria-pressed={lang === 'zh'} onClick={() => setLang('zh')}>中</button>
            </div>
            <button type="button" className="th-tool" aria-pressed={kaisetsu} onClick={() => setKaisetsu(!kaisetsu)}>
              <Lightbulb size={16} weight={kaisetsu ? 'fill' : 'regular'} /> {ui.kaisetsu}
              <span className="th-tool-state">{kaisetsu ? 'ON' : 'OFF'}</span>
            </button>
            <button type="button" className="th-tool" onClick={() => setReviewMode(true)}>
              <BookOpen size={16} /> {ui.reviewMode}
            </button>
          </div>
        }
      />

      {/* 各社面接パック（自動台本＋逐句語音） */}
      {(packs.length > 0 || archived.length > 0) && (
        <section className="th-section">
          <h2 className="th-label"><Headphones size={15} weight="bold" /> {ui.packSection}</h2>
          <div className="th-grid">
            {packs.map((p) => (
              <div
                key={p.dirname}
                role="button"
                tabIndex={0}
                className={`th-card th-card--pack${building && building !== p.dirname ? ' th-card--dim' : ''}`}
                onClick={() => { if (!building) playPack(p); }}
                onKeyDown={(ev) => { if (!building && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); playPack(p); } }}
                aria-busy={building === p.dirname}
              >
                <button
                  type="button"
                  className="th-archive"
                  onClick={(e) => { e.stopPropagation(); archivePack(p.dirname, false); }}
                  title={ui.archive}
                  aria-label={ui.archive}
                >
                  <Archive size={16} />
                </button>
                <div className="th-meta">
                  {p.questions != null && <span><b className="num-display">{p.questions}</b>問</span>}
                  <span className={p.audio_ready > 0 ? 'th-audio' : 'th-audio th-audio--none'}>
                    {p.audio_ready > 0 ? <><SpeakerHigh size={13} weight="fill" /> {p.audio_ready}句</> : ui.packAudioNone}
                  </span>
                </div>
                <div className="th-title">{p.company}</div>
                <div className="th-sub">{p.job_title || p.dirname}</div>
                <span className="th-play">
                  {building === p.dirname ? ui.packBuilding : <><Play size={14} weight="fill" /> {playLabel}</>}
                </span>
              </div>
            ))}
          </div>
          {archived.length > 0 && (
            <div className="th-archived">
              <button type="button" className="th-archived-toggle" onClick={() => setShowArchived(!showArchived)} aria-expanded={showArchived}>
                <CaretRight size={12} weight="bold" className={showArchived ? 'th-caret th-caret--open' : 'th-caret'} />
                {ui.archivedSection}<span className="th-count">{archived.length}</span>
              </button>
              {showArchived && (
                <ul className="th-archived-list">
                  {archived.map((a) => (
                    <li key={a.dirname}>
                      <span className="th-archived-name">{a.company}<small>{a.dirname}</small></span>
                      <button type="button" className="th-tool" onClick={() => archivePack(a.dirname, true)}>{ui.restore}</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      )}

      {/* 腳本選擇 */}
      <section className="th-section">
        <h2 className="th-label"><FilmSlate size={15} weight="bold" /> {ui.pickTitle}</h2>
        <div className="th-grid">
          {VN_SCRIPTS.map((s, i) => {
            const lineCount = s.acts.reduce((acc, a) => acc + a.lines.length, 0);
            return (
              <button key={s.id} type="button" className="th-card" onClick={() => setPlayingIdx(i)}>
                <div className="th-meta">
                  {s.sample && <span className="th-tag">{ui.sampleTag}</span>}
                  <span><b className="num-display">{s.acts.length}</b>{ui.acts}・<b className="num-display">{lineCount}</b>{ui.lines}</span>
                </div>
                <div className="th-title">{lang === 'ja' ? s.titleJa : s.titleZh}</div>
                <div className="th-sub">{ui.reportBy}：{lang === 'ja' ? s.interviewerJa : s.interviewerZh}</div>
                <span className="th-play"><Play size={14} weight="fill" /> {playLabel}</span>
              </button>
            );
          })}
          <button
            type="button"
            className="th-card th-card--random"
            onClick={() => setPlayingIdx(Math.floor(Math.random() * VN_SCRIPTS.length))}
          >
            <DiceFive size={28} weight="duotone" />
            <span className="th-title">{ui.random}</span>
          </button>
        </div>
        <p className="th-hint">{ui.startHint}</p>
      </section>

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
