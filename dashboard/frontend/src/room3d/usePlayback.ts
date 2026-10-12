// 面接室の台本再生：逐句音檔（/api/theater/audio）・自動送り・「回答を隠す」練習モード。
// 音檔の再生規則は vn/Player.tsx と同じ（onended で次へ、音檔なしは字数で時間を見積もる）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flattenScript, type FlatLine, type VNScript } from '../vn/types';
import { type InterviewerState, type InterviewerEmotion } from './interviewer';

const MS_PER_CHAR = 120;
const MIN_LINE_MS = 1400;
const AUTO_GAP_MS = 650;
/** 面接での日本語の話速の目安（約 390 字/分） */
const JA_CHARS_PER_SEC = 6.5;

export interface Playback {
  lines: FlatLine[];
  idx: number;
  line: FlatLine | null;
  finished: boolean;
  speaking: boolean;
  /** 候補者の番で、回答を隠している（本人が声に出して答える時間） */
  awaitingAnswer: boolean;
  waitStartedAt: number | null;
  auto: boolean;
  voiceOn: boolean;
  hideAnswers: boolean;
  interviewer: InterviewerState;
  /** 候補者行なら回答区間の先頭 index、それ以外は -1 */
  runStart: number;
  /** 何問目か（候補者の回答区間を 1 問と数える） */
  question: { n: number; total: number };
  /** 現在の回答区間の模範回答の長さ（秒）。音檔の実測が無い行は字数から推定 */
  modelSeconds: number | null;
  modelEstimated: boolean;
  setAuto(v: boolean): void;
  setVoiceOn(v: boolean): void;
  setHideAnswers(v: boolean): void;
  next(): void;
  prev(): void;
  jump(idx: number): void;
  reveal(): void;
  restart(): void;
}

/** idx を含む候補者発話の連続区間の先頭（同じ回答が複数行に分かれていても 1 回の「答える番」） */
export function answerRunStart(lines: readonly FlatLine[], idx: number): number {
  let i = idx;
  while (i > 0 && lines[i - 1].speaker === 'candidate') i--;
  return i;
}

export function usePlayback(script: VNScript | null, active: boolean): Playback {
  const lines = useMemo(() => (script ? flattenScript(script) : []), [script]);
  const [idx, setIdx] = useState(0);
  const [finished, setFinished] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [auto, setAuto] = useState(true);
  const [voiceOn, setVoiceOn] = useState(true);
  const [hideAnswers, setHideAnswers] = useState(true);
  const [revealed, setRevealed] = useState<ReadonlySet<number>>(new Set());
  const [waitStartedAt, setWaitStartedAt] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    setIdx(0);
    setFinished(false);
    setRevealed(new Set());
  }, [script]);

  const line = lines[idx] ?? null;
  const runStarts = useMemo(
    () => lines.flatMap((l, i) => (l.speaker === 'candidate' && lines[i - 1]?.speaker !== 'candidate' ? [i] : [])),
    [lines],
  );
  const runStart = line?.speaker === 'candidate' ? answerRunStart(lines, idx) : -1;
  const awaitingAnswer = !!line && active && !finished && hideAnswers
    && line.speaker === 'candidate' && !revealed.has(runStart);

  // 1 行ごとの発話：音檔があれば再生、なければ字数で見積もった時間だけ「話している」
  useEffect(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (!line || !active || finished || awaitingAnswer) {
      setSpeaking(false);
      setWaitStartedAt(awaitingAnswer ? Date.now() : null);
      return;
    }
    setWaitStartedAt(null);
    setSpeaking(true);
    if (voiceOn && script?.audioBase && line.audio) {
      const audio = new Audio(`${script.audioBase}/${line.audio}`);
      audioRef.current = audio;
      const done = () => setSpeaking(false);
      audio.onended = done;
      audio.onerror = done;
      audio.play().catch(done);
      return () => { audio.pause(); audio.onended = null; audio.onerror = null; };
    }
    const ms = line.durationMs ?? Math.max(MIN_LINE_MS, line.ja.length * MS_PER_CHAR);
    const to = setTimeout(() => setSpeaking(false), ms);
    return () => clearTimeout(to);
  }, [line, active, finished, awaitingAnswer, voiceOn, script]);

  const next = useCallback(() => {
    if (!lines.length) return;
    if (idx >= lines.length - 1) { setFinished(true); return; }
    setIdx(idx + 1);
  }, [idx, lines.length]);

  const prev = useCallback(() => {
    setFinished(false);
    setIdx((i) => Math.max(0, i - 1));
  }, []);

  const jump = useCallback((i: number) => {
    setFinished(false);
    setIdx(Math.max(0, Math.min(lines.length - 1, i)));
  }, [lines.length]);

  const reveal = useCallback(() => {
    if (runStart >= 0) setRevealed((s) => new Set(s).add(runStart));
  }, [runStart]);

  const restart = useCallback(() => {
    setFinished(false);
    setRevealed(new Set());
    setIdx(0);
  }, []);

  // 自動送り：話し終わって、答える番でもなければ次へ
  useEffect(() => {
    if (!active || !auto || finished || speaking || awaitingAnswer || !line) return;
    const to = setTimeout(next, AUTO_GAP_MS);
    return () => clearTimeout(to);
  }, [active, auto, finished, speaking, awaitingAnswer, line, next]);

  // 面接官の表情：直近の面接官行の emotion を保つ
  const interviewer = useMemo<InterviewerState>(() => {
    let emotion: InterviewerEmotion = 'neutral';
    for (let i = idx; i >= 0; i--) {
      if (lines[i]?.speaker === 'interviewer') { emotion = lines[i].emotion; break; }
    }
    const isCandidate = line?.speaker === 'candidate';
    return {
      talking: active && speaking && line?.speaker === 'interviewer',
      listening: active && isCandidate,
      emotion,
    };
  }, [active, speaking, line, lines, idx]);

  const question = useMemo(() => {
    let n = 0;
    for (const r of runStarts) if (r <= idx) n++;
    return { n, total: runStarts.length };
  }, [runStarts, idx]);

  const [modelSeconds, modelEstimated] = useMemo((): [number | null, boolean] => {
    if (runStart < 0) return [null, false];
    let ms = 0;
    let estimated = false;
    for (let i = runStart; i < lines.length && lines[i].speaker === 'candidate'; i++) {
      const l = lines[i];
      if (l.durationMs == null) estimated = true;
      ms += l.durationMs ?? (l.ja.length / JA_CHARS_PER_SEC) * 1000;
    }
    return [Math.round(ms / 1000), estimated];
  }, [lines, runStart]);

  return {
    runStart, question, modelSeconds, modelEstimated,
    lines, idx, line, finished, speaking, awaitingAnswer, waitStartedAt,
    auto, voiceOn, hideAnswers, interviewer,
    setAuto, setVoiceOn, setHideAnswers, next, prev, jump, reveal, restart,
  };
}
