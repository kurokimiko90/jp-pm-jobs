// 3D 面接ルーム：面接パック／提案パックの成果物を 4 部屋に展示し、面接室で台本を本番さながらに再生する。
// three.js 引擎は RoomWorld（React と分離）。台本・音檔は /api/theater、展示物は /api/room3d。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { get, post } from '../api';
import { useLang } from '../i18n';
import { useHashRoute } from '../filters/useHashFilters';
import { type VNLang, type VNScript } from '../vn/types';
import { RoomWorld, type Pick, type WorldMode } from './RoomWorld';
import { BOARDS, type RoomId } from './layout';
import { mdPreview } from './textures';
import { usePlayback } from './usePlayback';
import { useAnswerRecorder } from './useAnswerRecorder';
import { mockScript } from './mock';
import { MiniMap } from './MiniMap';
import { SeatPanel } from './SeatPanel';
import { ReaderPanel, SlidePanel } from './ReaderPanel';
import { ROOM3D_UI, roomLabel } from './ui';
import './room3d.css';

interface Pack { dirname: string; company: string; job_title: string; script_ready: boolean; audio_ready: number }
interface Station { id: string; room: RoomId; titleJa: string; titleZh: string; available: boolean; source: string | null; body: string }
interface Content { dirname: string; company: string; stations: Station[]; slides: string[] }
type ScriptState = 'idle' | 'loading' | 'ready' | 'missing' | 'building' | 'error';

/** 振り返り室の「面接サマリー」— 台本の report と幕構成から組み立てる（新しい事実は足さない） */
function summaryStation(script: VNScript | null, lang: VNLang, title: string): Station {
  if (!script) return { id: 'summary', room: 'review', titleJa: title, titleZh: title, available: false, source: null, body: '' };
  const overall = lang === 'ja' ? script.report.overallJa : script.report.overallZh;
  const acts = script.acts.map((a, i) => {
    const goal = lang === 'ja' ? a.goalJa : a.goalZh;
    return `- **${lang === 'ja' ? a.titleJa : a.titleZh}**（${a.lines.length}）${goal ? ` — ${goal}` : ''}`;
  });
  const aspects = script.report.aspects.map((a) => `- ${lang === 'ja' ? a.labelJa : a.labelZh}：${'★'.repeat(a.score)}　${lang === 'ja' ? a.commentJa : a.commentZh}`);
  const body = [`# ${title}`, overall, aspects.length ? '## 評価\n' + aspects.join('\n') : '', '## 構成', ...acts].filter(Boolean).join('\n\n');
  return { id: 'summary', room: 'review', titleJa: title, titleZh: title, available: true, source: 'theater/script.json', body };
}

/** 本番模擬の出題数（実際の 1 次面接 45〜60 分の分量） */
const MOCK_QUESTIONS = 10;
const LAST_PACK_KEY = 'room3d.lastPack';
// 前回開いた面接パック（タブを開き直しても同じ会社に戻る）。storage 不可でも動く
const readLastPack = (): string | null => {
  try { return localStorage.getItem(LAST_PACK_KEY); } catch { return null; }
};
const writeLastPack = (v: string): void => {
  try { localStorage.setItem(LAST_PACK_KEY, v); } catch { /* private mode 等 */ }
};

export default function Room3D() {
  const { lang: dashLang } = useLang();
  const [lang, setLang] = useState<VNLang>(dashLang === 'zh' ? 'zh' : 'ja');
  const ui = ROOM3D_UI[lang];
  const [route, navigate] = useHashRoute();
  const dirname = route[2] ? decodeURIComponent(route[2]) : null;

  const [packs, setPacks] = useState<Pack[] | null>(null);
  const [content, setContent] = useState<Content | null>(null);
  const [script, setScript] = useState<VNScript | null>(null);
  const [scriptState, setScriptState] = useState<ScriptState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<WorldMode>('walk');
  const [room, setRoom] = useState<RoomId>('lobby');
  const [hover, setHover] = useState<Pick | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  const [slideOpen, setSlideOpen] = useState(false);
  const [slideIdx, setSlideIdx] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);

  const stageRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<RoomWorld | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  // 全文パネルを開いている間は面接を一時停止（音声が裏で進まないように）
  // 全問練習 or 本番模擬（中盤から MOCK_QUESTIONS 問だけ抽出、開始ごとに出題が変わる）
  const [mockSeed, setMockSeed] = useState<number | null>(null);
  const playScript = useMemo(
    () => (script && mockSeed != null ? mockScript(script, MOCK_QUESTIONS, mockSeed) : script),
    [script, mockSeed],
  );
  const pb = usePlayback(playScript, mode === 'seat' && scriptState === 'ready' && !panelOpen);
  const rec = useAnswerRecorder(pb.awaitingAnswer ? pb.runStart : null);
  const [subs, setSubs] = useState(true);

  // ---- 資料 ----
  useEffect(() => {
    get('/api/theater/scripts')
      .then((r) => setPacks(r.packs ?? []))
      .catch((e) => { setPacks([]); setError(String(e instanceof Error ? e.message : e)); });
  }, []);

  useEffect(() => {
    if (dirname || !packs?.length) return;
    const last = readLastPack();
    const pick = packs.find((p) => p.dirname === last) ?? packs[0];
    navigate(['interview-prep', 'room3d', pick.dirname]);
  }, [dirname, packs, navigate]);

  const loadScript = useCallback(async (name: string) => {
    setScriptState('loading');
    try {
      const r = await get(`/api/theater/script?dirname=${encodeURIComponent(name)}`);
      setScript(r.script);
      setScriptState('ready');
    } catch {
      setScript(null);
      setScriptState('missing');
    }
  }, []);

  useEffect(() => {
    if (!dirname) return;
    writeLastPack(dirname);
    setContent(null);
    setScript(null);
    setError(null);
    setReading(null);
    setSlideIdx(0);
    worldRef.current?.goToRoom('lobby');
    get(`/api/room3d/content/${encodeURIComponent(dirname)}`)
      .then(setContent)
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
    loadScript(dirname);
  }, [dirname, loadScript]);

  const buildScript = async () => {
    if (!dirname) return;
    setScriptState('building');
    try {
      await post('/api/theater/build', { dirname });
      await loadScript(dirname);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      setScriptState('error');
    }
  };

  const stations = useMemo<Station[]>(() => {
    const base = content?.stations ?? [];
    return [...base, summaryStation(script, lang, ui.summary)];
  }, [content, script, lang, ui.summary]);
  // 看板の社名：台本メタの正式名（「株式会社サンプル」）を優先、無ければ dirname 由来
  const company = packs?.find((p) => p.dirname === dirname)?.company ?? content?.company ?? '';
  const jobTitle = packs?.find((p) => p.dirname === dirname)?.job_title ?? '';
  // 幕番号は再生中の台本（本番模擬なら抽出版）基準
  const agendaAct = pb.line?.actIdx ?? -1;
  const stationById = useMemo(() => new Map(stations.map((s) => [s.id, s])), [stations]);

  // ---- 3D 引擎 ----
  const startMock = useCallback(() => {
    setMockSeed(Math.floor(Math.random() * 1e9));
    setReading(null);
    setSlideOpen(false);
    stageRef.current?.focus({ preventScroll: true });
    worldRef.current?.sit();
  }, []);
  const startFull = useCallback(() => {
    setMockSeed(null);
    setReading(null);
    setSlideOpen(false);
    stageRef.current?.focus({ preventScroll: true });
    worldRef.current?.sit();
  }, []);

  const startInterview = useCallback(() => {
    setReading(null);
    setSlideOpen(false);
    stageRef.current?.focus({ preventScroll: true });
    worldRef.current?.sit();
  }, []);

  const onPick = useCallback((pick: Pick) => {
    const w = worldRef.current;
    if (!w) return;
    if (pick.type === 'board') w.focusBoard(pick.id, () => setReading(pick.id));
    else if (pick.type === 'screen') { if (slidesRef.current.length) setSlideOpen(true); }
    else startInterview();
  }, [startInterview]);
  const slidesRef = useRef<string[]>([]);
  slidesRef.current = content?.slides ?? [];
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const world = new RoomWorld(stage, {
      onPick: (p) => onPickRef.current(p),
      onHover: setHover,
      onRoom: setRoom,
      onMode: setMode,
    });
    worldRef.current = world;
    (window as unknown as { __room3d?: RoomWorld }).__room3d = world; // E2E / 截圖用
    return () => { world.dispose(); worldRef.current = null; };
  }, []);

  useEffect(() => {
    const w = worldRef.current;
    if (!w) return;
    for (const b of BOARDS) {
      const s = stationById.get(b.id);
      const title = s ? (lang === 'ja' ? s.titleJa : s.titleZh) : b.id;
      w.setBoard(b.id, {
        title,
        kicker: `${roomLabel(b.room, lang)}　${company}`,
        lines: s?.available ? mdPreview(s.body, 9) : [],
        available: !!s?.available,
        emptyLabel: ui.empty,
        hint: s?.available ? ui.hint : (s?.source ?? ui.empty),
      });
    }
    w.setSigns((id) => roomLabel(id, lang), ui.counter.replace('{company}', company));
  }, [stationById, company, lang, ui]);

  useEffect(() => {
    const w = worldRef.current;
    const slides = content?.slides ?? [];
    if (!w) return;
    if (!slides.length) {
      if (!content) { w.setSlide(null, ui.loading); return; }
      w.setAgenda({
        kicker: ui.agenda,
        company,
        jobTitle,
        acts: (playScript?.acts ?? []).map((a) => (lang === 'ja' ? a.titleJa : a.titleZh)),
        current: mode === 'seat' ? agendaAct : -1,
        footer: ui.slideNone,
      });
      return;
    }
    const img = new Image();
    img.onload = () => w.setSlide(img, `${ui.slides} ${slideIdx + 1} / ${slides.length}`);
    img.onerror = () => w.setSlide(null, ui.loadFailed);
    img.src = slides[slideIdx];
  }, [content, slideIdx, ui, company, jobTitle, playScript, lang, mode, agendaAct]);

  useEffect(() => { worldRef.current?.setInterviewer(pb.interviewer); }, [pb.interviewer]);
  useEffect(() => {
    worldRef.current?.setInputEnabled(!reading && !slideOpen);
    setPanelOpen(!!reading || slideOpen);
  }, [reading, slideOpen]);

  useEffect(() => {
    const onFs = () => setFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, []);
  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else stageRef.current?.requestFullscreen().catch(() => {});
  };

  // 面接席のキー操作（Space/Enter/→ = 次 or 模範回答、← = 戻る）。
  // 開始ボタンが消えると focus が body に落ちるので window で拾う
  useEffect(() => {
    if (mode !== 'seat' || reading || slideOpen || pb.finished) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable)) return;
      // ボタンに focus がある時の Space/Enter はボタン自身の click に任せる
      if (el?.tagName === 'BUTTON' && (e.code === 'Space' || e.code === 'Enter')) return;
      if (e.code === 'Space' || e.code === 'Enter' || e.code === 'ArrowRight') {
        e.preventDefault();
        if (pb.awaitingAnswer) pb.reveal(); else pb.next();
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        pb.prev();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, reading, slideOpen, pb]);

  const goReview = () => worldRef.current?.goToRoom('review');
  const readingStation = reading ? stationById.get(reading) : null;
  const hoverLabel = hover?.type === 'board'
    ? (() => { const s = stationById.get(hover.id); return s ? `${lang === 'ja' ? s.titleJa : s.titleZh} — ${ui.clickToRead}` : null; })()
    : hover?.type === 'screen' ? (content?.slides.length ? `${ui.slides} — ${ui.clickToRead}` : null)
    : hover?.type === 'seat' ? ui.clickToSit : null;
  const available = useMemo(() => new Set(stations.filter((s) => s.available).map((s) => s.id)), [stations]);
  const getPose = useCallback(() => worldRef.current?.getPose() ?? { x: 0, z: 0, yaw: 0 }, []);
  const dpad = (fx: number, fz: number) => ({
    onPointerDown: () => worldRef.current?.setTouchMove(fx, fz),
    onPointerUp: () => worldRef.current?.setTouchMove(0, 0),
    onPointerLeave: () => worldRef.current?.setTouchMove(0, 0),
  });

  return (
    <div className="r3d-page">
      <div className="r3d-head">
        <div>
          <h1>{ui.title}</h1>
          <p>{ui.sub}</p>
        </div>
        <div className="r3d-tools">
          <select
            className="r3d-select" aria-label={ui.pickPack} value={dirname ?? ''}
            onChange={(e) => navigate(['interview-prep', 'room3d', e.target.value])}
          >
            {!packs?.length && <option value="">{packs ? ui.noPack : ui.loading}</option>}
            {packs?.map((p) => (
              <option key={p.dirname} value={p.dirname}>
                {p.company}{p.job_title ? `｜${p.job_title.slice(0, 18)}` : ''}
              </option>
            ))}
          </select>
          <div className="r3d-seg" role="group" aria-label="language">
            <button aria-pressed={lang === 'ja'} onClick={() => setLang('ja')}>JP</button>
            <button aria-pressed={lang === 'zh'} onClick={() => setLang('zh')}>中</button>
          </div>
        </div>
      </div>

      <div ref={stageRef} className="r3d-stage" tabIndex={0}>
        <div className="r3d-hud-top">
          <div className="r3d-roomtag r3d-glass">
            <strong>{roomLabel(room, lang)}{company ? `｜${company}` : ''}</strong>
            <span>{mode === 'seat' ? ui.controlsSeat : ui.controlsWalk}</span>
          </div>
          <div className="r3d-actions">
            {mode === 'walk' && scriptState === 'ready' && pb.idx > 0 && !pb.finished && (
              <button className="r3d-btn r3d-btn--gold" onClick={startInterview}>🪑 {ui.resume}</button>
            )}
            {mode === 'walk' && scriptState === 'ready' && (
              <>
                <button className={`r3d-btn${pb.idx > 0 && !pb.finished ? '' : ' r3d-btn--gold'}`} onClick={startMock}>
                  🎲 {ui.mock.replace('{k}', String(MOCK_QUESTIONS))}
                </button>
                <button className="r3d-btn" onClick={startFull}>📚 {ui.full}</button>
              </>
            )}
            {mode === 'walk' && (scriptState === 'missing' || scriptState === 'building' || scriptState === 'error') && (
              <button className="r3d-btn r3d-btn--gold" onClick={buildScript} disabled={scriptState === 'building'} title={ui.noScript}>
                {scriptState === 'building' ? ui.building : ui.buildScript}
              </button>
            )}
            {mode === 'seat' && <button className="r3d-btn" onClick={() => worldRef.current?.stand()}>{ui.standUp}</button>}
            <button className="r3d-btn" onClick={toggleFullscreen}>{fullscreen ? ui.exitFullscreen : ui.fullscreen}</button>
          </div>
        </div>

        {hoverLabel && !reading && !slideOpen && <div className="r3d-tooltip r3d-glass">{hoverLabel}</div>}

        {mode === 'walk' && (
          <MiniMap
            title={ui.minimap} current={room} available={available}
            roomName={(id) => roomLabel(id, lang)} getPose={getPose}
            onRoom={(id) => worldRef.current?.goToRoom(id)}
            onBoard={(id) => worldRef.current?.focusBoard(id, () => setReading(id))}
            onSeat={startInterview}
          />
        )}
        {mode === 'walk' && (
          <div className="r3d-dpad" aria-hidden="true">
            <span /><button className="r3d-btn" {...dpad(0, 1)}>▲</button><span />
            <button className="r3d-btn" {...dpad(-1, 0)}>◀</button>
            <button className="r3d-btn" {...dpad(0, -1)}>▼</button>
            <button className="r3d-btn" {...dpad(1, 0)}>▶</button>
          </div>
        )}

        {mode === 'seat' && script && scriptState === 'ready' && (
          <SeatPanel script={playScript ?? script} pb={pb} rec={rec} subs={subs} setSubs={setSubs} lang={lang} ui={ui} onStand={() => worldRef.current?.stand()} onReview={goReview} />
        )}

        {error && !reading && (
          <div className="r3d-center"><div className="r3d-card r3d-glass"><h3>{ui.loadFailed}</h3><p>{error}</p></div></div>
        )}

        {readingStation && (
          <ReaderPanel
            ui={ui}
            title={lang === 'ja' ? readingStation.titleJa : readingStation.titleZh}
            source={readingStation.source}
            body={readingStation.body}
            onClose={() => setReading(null)}
          />
        )}
        {slideOpen && content?.slides.length ? (
          <SlidePanel ui={ui} slides={content.slides} index={slideIdx} onIndex={setSlideIdx} onClose={() => setSlideOpen(false)} />
        ) : null}
      </div>
    </div>
  );
}
