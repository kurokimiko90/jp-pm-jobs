// 「あなたの番」の間だけマイクで自分の回答を録音し、模範回答の後に聞き比べる。
// 録音はブラウザのメモリ（Blob URL）にだけ置く — サーバーにも外部にも送らない。
import { useCallback, useEffect, useRef, useState } from 'react';

export interface Take {
  url: string;
  seconds: number;
}

export interface AnswerRecorder {
  enabled: boolean;
  recording: boolean;
  error: string | null;
  takes: ReadonlyMap<number, Take>;
  toggle(): Promise<void>;
}

/**
 * @param runKey  いま答えている回答区間の先頭 index（答える番でなければ null）
 */
export function useAnswerRecorder(runKey: number | null): AnswerRecorder {
  const [enabled, setEnabled] = useState(false);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [takes, setTakes] = useState<ReadonlyMap<number, Take>>(new Map());
  const streamRef = useRef<MediaStream | null>(null);
  const urlsRef = useRef<string[]>([]);

  const toggle = useCallback(async () => {
    if (enabled) {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      setEnabled(false);
      return;
    }
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      setError(null);
      setEnabled(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [enabled]);

  // 答える番に入ったら録音開始、抜けたら（模範回答を開いたら）停止して保存
  useEffect(() => {
    const stream = streamRef.current;
    if (!enabled || runKey == null || !stream || typeof MediaRecorder === 'undefined') return;
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    const started = performance.now();
    let cancelled = false;
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      setRecording(false);
      if (cancelled || !chunks.length) return;
      const url = URL.createObjectURL(new Blob(chunks, { type: recorder.mimeType }));
      urlsRef.current.push(url);
      const seconds = Math.round((performance.now() - started) / 1000);
      setTakes((m) => new Map(m).set(runKey, { url, seconds }));
    };
    recorder.start();
    setRecording(true);
    return () => {
      // 1 秒未満は誤操作扱いで捨てる
      cancelled = performance.now() - started < 1000;
      if (recorder.state !== 'inactive') recorder.stop();
    };
  }, [enabled, runKey]);

  useEffect(() => () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    urlsRef.current.forEach((u) => URL.revokeObjectURL(u));
  }, []);

  return { enabled, recording, error, takes, toggle };
}
