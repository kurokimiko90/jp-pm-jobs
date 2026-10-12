import { useEffect, useRef, useState } from 'react';
import { type VNLang, type VNScript } from '../vn/types';
import { type Playback } from './usePlayback';
import { type AnswerRecorder, type Take } from './useAnswerRecorder';
import { actLabel, type Room3DText } from './ui';

interface SeatPanelProps {
  script: VNScript;
  pb: Playback;
  rec: AnswerRecorder;
  subs: boolean;
  setSubs: (v: boolean) => void;
  lang: VNLang;
  ui: Room3DText;
  onStand: () => void;
  onReview: () => void;
}

function useElapsed(since: number | null): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (since == null) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [since]);
  return since == null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

/** 長さの目安：模範回答があれば比率、無ければ 30〜120 秒を基準にする */
export function paceOf(mine: number, model: number | null): 'short' | 'long' | 'ok' {
  if (model) {
    if (mine < model * 0.6) return 'short';
    if (mine > Math.max(model * 1.6, 120)) return 'long';
    return 'ok';
  }
  return mine < 30 ? 'short' : mine > 120 ? 'long' : 'ok';
}

function TakeRow({ take, model, estimated, ui }: { take: Take; model: number | null; estimated: boolean; ui: Room3DText }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  useEffect(() => () => audio.current?.pause(), []);
  const play = () => {
    if (playing) { audio.current?.pause(); setPlaying(false); return; }
    audio.current = new Audio(take.url);
    audio.current.onended = () => setPlaying(false);
    audio.current.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
  };
  const pace = paceOf(take.seconds, model);
  const paceText = pace === 'short' ? ui.paceShort : pace === 'long' ? ui.paceLong : ui.paceOk;
  return (
    <div className={`r3d-take r3d-take--${pace}`}>
      <button className="r3d-btn" onClick={play}>{playing ? '■' : '▶'} {ui.yourTake} {fmt(take.seconds)}</button>
      {model != null && <span className="r3d-take-model">{ui.modelTake}{estimated ? ui.estimated : ''} {fmt(model)}</span>}
      <span className="r3d-take-pace">{paceText}</span>
    </div>
  );
}

/** 面接席の字幕・操作。幕が変わると幕タイトルを数秒だけ出す */
export function SeatPanel({ script, pb, rec, subs, setSubs, lang, ui, onStand, onReview }: SeatPanelProps) {
  const { line, idx, lines } = pb;
  const elapsed = useElapsed(pb.waitStartedAt);
  const act = line ? script.acts[line.actIdx] : null;
  const actIdx = line?.actIdx ?? -1;
  const [bannerAct, setBannerAct] = useState<number | null>(null);
  const [peekIdx, setPeekIdx] = useState<number | null>(null);
  useEffect(() => {
    if (actIdx < 0) return;
    setBannerAct(actIdx);
    const to = setTimeout(() => setBannerAct(null), 3200);
    return () => clearTimeout(to);
  }, [actIdx]);

  if (pb.finished) {
    return (
      <div className="r3d-center">
        <div className="r3d-card r3d-glass">
          <h3>{ui.finished}</h3>
          <p>{ui.finishedSub}</p>
          <div className="row">
            <button className="r3d-btn r3d-btn--gold" onClick={onReview}>{ui.toReview}</button>
            <button className="r3d-btn" onClick={pb.restart}>{ui.restart}</button>
            <button className="r3d-btn" onClick={onStand}>{ui.standUp}</button>
          </div>
        </div>
      </div>
    );
  }
  if (!line || !act) return null;

  const speakerLabel = line.type === 'inner' ? ui.inner : line.speaker === 'candidate' ? ui.you : ui.interviewer;
  const speakerCls = line.type === 'inner' ? ' r3d-speaker--inner' : line.speaker === 'candidate' ? ' r3d-speaker--you' : '';
  const text = lang === 'ja' ? line.ja : line.zh;
  const actTitle = lang === 'ja' ? act.titleJa : act.titleZh;
  const actGoal = lang === 'ja' ? act.goalJa : act.goalZh;
  const hideSub = !subs && line.speaker === 'interviewer' && peekIdx !== idx;
  const take = pb.runStart >= 0 && !pb.awaitingAnswer ? rec.takes.get(pb.runStart) : undefined;
  const q = pb.question.n > 0 ? ui.question.replace('{n}', String(pb.question.n)).replace('{total}', String(pb.question.total)) : '';

  return (
    <>
      {bannerAct === line.actIdx && (
        <div key={`act-${line.actIdx}`} className="r3d-act r3d-glass">
          <strong>{actLabel(ui, line.actIdx, actTitle)}</strong>
          {actGoal && <span>🎯 {actGoal}</span>}
        </div>
      )}
      {/* 滑鼠點過的按鈕不留 focus — 否則之後按 Enter 會再按一次那顆（例：關掉錄音），而不是「下一句」 */}
      <div className="r3d-seat" onPointerUpCapture={(e) => (e.target as HTMLElement).closest('button')?.blur()}>
        <div className="r3d-sub r3d-glass" aria-live="polite">
          <div className="r3d-sub-head">
            <span className={`r3d-speaker${speakerCls}`}>{speakerLabel}</span>
            <span className="r3d-sub-act">{actLabel(ui, line.actIdx, actTitle)}</span>
            <span className="r3d-sub-meta">{q}{q && '　'}{idx + 1} / {lines.length}</span>
          </div>
          {pb.awaitingAnswer ? (
            <div className="r3d-turn">
              <span className={rec.recording ? 'mic mic--rec' : 'mic'} aria-hidden="true" />
              <span>{rec.recording ? ui.recording : ui.yourTurn}</span>
              <span className="time">{ui.elapsed} {fmt(elapsed)}</span>
              <span className="grow" />
              <button className="r3d-btn r3d-btn--gold" onClick={pb.reveal}>{ui.showAnswer}　⏎</button>
            </div>
          ) : hideSub ? (
            <div className="r3d-turn">
              <span className="r3d-sub-hidden">🎧 {ui.subsOff}</span>
              <span className="grow" />
              <button className="r3d-btn" onClick={() => setPeekIdx(idx)}>{ui.peek}</button>
            </div>
          ) : (
            <div className={`r3d-sub-text${line.type === 'inner' ? ' r3d-sub-text--inner' : ''}`}>
              {text}
              {line.simulated && <span className="r3d-sub-sim">simulated</span>}
            </div>
          )}
          {take && <TakeRow take={take} model={pb.modelSeconds} estimated={pb.modelEstimated} ui={ui} />}
          <div className="r3d-progress"><div style={{ width: `${((idx + 1) / lines.length) * 100}%` }} /></div>
        </div>
        <div className="r3d-ctrl">
          <button className="r3d-btn" onClick={pb.prev} disabled={idx === 0}>←<span className="lbl"> {ui.prev}</span></button>
          <button className="r3d-btn r3d-btn--gold" onClick={pb.awaitingAnswer ? pb.reveal : pb.next}>
            {pb.awaitingAnswer ? ui.showAnswer : ui.next} →
          </button>
          <select
            className="r3d-btn" value={line.actIdx} aria-label="act"
            onChange={(e) => pb.jump(lines.findIndex((l) => l.actIdx === Number(e.target.value)))}
          >
            {script.acts.map((a, i) => (
              <option key={a.id} value={i}>{actLabel(ui, i, lang === 'ja' ? a.titleJa : a.titleZh)}</option>
            ))}
          </select>
          <span className="grow" />
          <button className="r3d-btn" aria-pressed={pb.hideAnswers} title={ui.hideAnswers} onClick={() => pb.setHideAnswers(!pb.hideAnswers)}>🎤<span className="lbl"> {ui.hideAnswers}</span></button>
          <button className="r3d-btn" aria-pressed={rec.enabled} title={rec.error ? `${ui.micDenied}: ${rec.error}` : ui.record} onClick={rec.toggle}>⏺<span className="lbl"> {ui.record}</span></button>
          <button className="r3d-btn" aria-pressed={subs} title={ui.subs} onClick={() => setSubs(!subs)}>💬<span className="lbl"> {ui.subs}</span></button>
          {script.audioBase && (
            <button className="r3d-btn" aria-pressed={pb.voiceOn} title={ui.voice} onClick={() => pb.setVoiceOn(!pb.voiceOn)}>🔊<span className="lbl"> {ui.voice}</span></button>
          )}
          <button className="r3d-btn" aria-pressed={pb.auto} title={ui.auto} onClick={() => pb.setAuto(!pb.auto)}>⏩<span className="lbl"> {ui.auto}</span></button>
        </div>
        {rec.error && <div className="r3d-error r3d-glass">{ui.micDenied}: {rec.error}</div>}
      </div>
    </>
  );
}
