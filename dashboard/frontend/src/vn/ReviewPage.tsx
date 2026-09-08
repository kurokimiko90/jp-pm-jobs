import React, { useEffect, useMemo, useRef, useState } from 'react';
import { C, alpha, RADIUS, SHADOW } from '../theme';
import { get, post } from '../api';
import { useIsNarrow } from '../components/ui';
import { VN_SCRIPTS } from './scripts';
import { type VNLang, type VNScript } from './types';
import { VN_UI } from './ui';
import { ReviewChatLine } from './review/ReviewChatLine';
import { ReviewSidebar, type SearchHit, type TocGroup } from './review/ReviewSidebar';
import {
  actSeconds, addReadSeconds, formatMinutes, loadBookmarks, loadLastScriptKey,
  recordView, saveBookmarks, saveLastScriptKey, todayStr,
} from './review/reviewUtils';

// 復習モード：先選擇要復習的劇本，選定後才顯示該劇本專屬的目次+內容（不再是所有劇本
// 攤平混在一起）。選劇本走頂部常駐下拉選單（不切畫面），預設選取：剛好 1 個公司面接
// パック → 自動開；0 個 → 開示例台本；≥2 個 → 下拉留空、內容區顯示提示待手動選。
// 上次選擇記在 localStorage，下次進頁直接回去（除非已不存在）。
// 手寫 SCRIPT_MAIN 不參與自動預設，永遠只能從下拉選單手動點開。

interface ReviewPageProps {
  lang: VNLang;
  setLang: (l: VNLang) => void;
  kaisetsu: boolean;
  setKaisetsu: (v: boolean) => void;
  onExit: () => void;
}

interface TheaterPack {
  dirname: string;
  job_id: number | null;
  company: string;
  job_title: string;
  questions: number | null;
  script_ready: boolean;
  audio_ready: number;
}

type ReadingMode = 'full' | 'mine';
/** `static:{VNScript.id}`（手寫台本/示例） | `pack:{dirname}`（公司面接パック） */
type ScriptKey = string;

const PACK_PREFIX = 'pack:';
const STATIC_PREFIX = 'static:';
const anchorId = (scriptId: string, actId: number): string => `review-act-${scriptId}-${actId}`;
const READ_TICK_MS = 10_000;

const jumpTo = (id: string) => {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

export const ReviewPage: React.FC<ReviewPageProps> = ({
  lang, setLang, kaisetsu, setKaisetsu, onExit,
}) => {
  const ui = VN_UI[lang];
  const isNarrow = useIsNarrow();
  const [packsList, setPacksList] = useState<TheaterPack[] | null>(null);
  const [selectedKey, setSelectedKey] = useState<ScriptKey | null>(null);
  const [defaultDecided, setDefaultDecided] = useState(false);
  const [loadingSelected, setLoadingSelected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [bookmarks, setBookmarks] = useState<Set<string>>(() => loadBookmarks());
  const [query, setQuery] = useState('');
  const [readingMode, setReadingMode] = useState<ReadingMode>('full');
  const [showInner, setShowInner] = useState(false);
  const [kpi, setKpi] = useState({ viewCount: 0, todaySeconds: 0 });
  const scriptCacheRef = useRef<Record<string, VNScript>>({});
  const [cacheVersion, setCacheVersion] = useState(0);

  // 1) パック一覧を取得
  useEffect(() => {
    let cancelled = false;
    get('/api/theater/scripts')
      .then((r) => { if (!cancelled) setPacksList(r.packs ?? []); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, []);

  // 2) 一覧が揃ったら一度だけデフォルト選択を決める（1 件→自動／0 件→サンプル／≥2 件→選択画面）
  useEffect(() => {
    if (packsList === null || defaultDecided) return;
    setDefaultDecided(true);
    const last = loadLastScriptKey();
    const lastValid = !!last && (
      last.startsWith(STATIC_PREFIX)
      || packsList.some((p) => `${PACK_PREFIX}${p.dirname}` === last)
    );
    if (lastValid) { setSelectedKey(last); return; }
    if (packsList.length === 1) { setSelectedKey(`${PACK_PREFIX}${packsList[0].dirname}`); return; }
    if (packsList.length === 0) {
      const sample = VN_SCRIPTS.find((s) => s.sample);
      if (sample) setSelectedKey(`${STATIC_PREFIX}${sample.id}`);
    }
    // ≥2 件は selectedKey を null のままにして選択画面を出す
  }, [packsList, defaultDecided]);

  // 3) 選択が pack: の場合、未取得なら script.json を取得（未 build なら audio:false で即時生成）
  useEffect(() => {
    if (!selectedKey || !selectedKey.startsWith(PACK_PREFIX) || !packsList) return;
    const dirname = selectedKey.slice(PACK_PREFIX.length);
    if (scriptCacheRef.current[dirname]) return;
    const pack = packsList.find((p) => p.dirname === dirname);
    if (!pack) return;
    let cancelled = false;
    (async () => {
      setLoadingSelected(true);
      try {
        if (!pack.script_ready) {
          await post('/api/theater/build', { dirname, audio: false });
        }
        const sr = await get(`/api/theater/script?dirname=${encodeURIComponent(dirname)}`);
        if (!cancelled) {
          scriptCacheRef.current[dirname] = sr.script as VNScript;
          setCacheVersion((v) => v + 1);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoadingSelected(false);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedKey, packsList]);

  // KPI: 進頁記一次瀏覽、每 10 秒累計今日復習時間（localStorage，單人工具零後端）
  useEffect(() => {
    const today = todayStr();
    const rec = recordView(today);
    setKpi({ viewCount: rec.viewCount, todaySeconds: rec.byDate[today] ?? 0 });
    const timer = setInterval(() => {
      const sec = addReadSeconds(today, READ_TICK_MS / 1000);
      setKpi((k) => ({ ...k, todaySeconds: sec }));
    }, READ_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const selectScript = (key: ScriptKey) => {
    setActiveId(null);
    setQuery('');
    setSelectedKey(key);
    saveLastScriptKey(key);
  };

  const toggleBookmark = (id: string) => {
    setBookmarks((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      saveBookmarks(next);
      return next;
    });
  };

  const selectedScript: VNScript | null = useMemo(() => {
    if (!selectedKey) return null;
    if (selectedKey.startsWith(STATIC_PREFIX)) {
      const id = selectedKey.slice(STATIC_PREFIX.length);
      return VN_SCRIPTS.find((s) => s.id === id) ?? null;
    }
    const dirname = selectedKey.slice(PACK_PREFIX.length);
    return scriptCacheRef.current[dirname] ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey, cacheVersion]);

  const group: TocGroup | null = useMemo(() => {
    if (!selectedScript) return null;
    return {
      scriptId: selectedScript.id,
      label: lang === 'ja' ? selectedScript.titleJa : selectedScript.titleZh,
      acts: selectedScript.acts.map((a) => ({
        id: anchorId(selectedScript.id, a.id),
        label: lang === 'ja' ? a.titleJa : a.titleZh,
        seconds: actSeconds(a),
      })),
    };
  }, [selectedScript, lang]);

  const searchHits: SearchHit[] = useMemo(() => {
    if (!selectedScript || !query.trim()) return [];
    const q = query.trim().toLowerCase();
    const hits: SearchHit[] = [];
    for (const act of selectedScript.acts) {
      const actLabel = lang === 'ja' ? act.titleJa : act.titleZh;
      for (const line of act.lines) {
        const text = `${line.ja} ${line.zh}`.toLowerCase();
        if (text.includes(q)) {
          hits.push({ id: anchorId(selectedScript.id, act.id), actLabel, snippet: line.ja.slice(0, 60) });
        }
      }
    }
    return hits;
  }, [query, selectedScript, lang]);

  // スクロール追従ハイライト（今どの幕を見ているか目次に反映）
  useEffect(() => {
    if (!group) return undefined;
    const els = group.acts.map((a) => document.getElementById(a.id)).filter((el): el is HTMLElement => el != null);
    if (!els.length) return undefined;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) setActiveId(visible[0].target.id);
    }, { rootMargin: '-15% 0px -70% 0px', threshold: 0 });
    els.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [group]);

  const activeAct = group?.acts.find((a) => a.id === activeId);

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

  const controlsRow = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
      <div>
        <h1 style={{ fontSize: 26, fontWeight: 700, letterSpacing: 1, margin: '0 0 4px' }}>
          📖 {ui.reviewTitle}
        </h1>
        <div style={{ fontSize: 13, color: C.sub }}>{ui.reviewSub}</div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={() => setLang('ja')} style={segBtn(lang === 'ja')}>JP</button>
        <button onClick={() => setLang('zh')} style={segBtn(lang === 'zh')}>中</button>
        <span style={{ width: 6 }} />
        <button onClick={() => setKaisetsu(!kaisetsu)} style={segBtn(kaisetsu)}>
          💡 {ui.kaisetsu}{kaisetsu ? ' ON' : ' OFF'}
        </button>
        <button onClick={() => setShowInner(!showInner)} style={segBtn(showInner)}>
          🧠 {ui.reviewShowInner}{showInner ? ' ON' : ' OFF'}
        </button>
        <span style={{ width: 6 }} />
        <button onClick={() => setReadingMode('full')} style={segBtn(readingMode === 'full')}>
          {ui.reviewModeFull}
        </button>
        <button onClick={() => setReadingMode('mine')} style={segBtn(readingMode === 'mine')}>
          {ui.reviewModeMine}
        </button>
        <span style={{ width: 6 }} />
        <button onClick={onExit} style={segBtn(false)}>← {ui.backToTitle}</button>
      </div>
    </div>
  );

  // 劇本切替ドロップダウン（各社面接パック + 台本を optgroup で分ける）。
  // 常時ヘッダーにいる — 別画面へ遷移せず、いつでもここから切り替えられる。
  const scriptDropdown = (
    <select
      value={selectedKey ?? ''}
      onChange={(e) => { if (e.target.value) selectScript(e.target.value); }}
      style={{
        border: `1.5px solid ${alpha(C.milkTea, 0.7)}`, borderRadius: RADIUS.pill,
        padding: '6px 12px', fontSize: 12.5, fontWeight: 600, color: C.ink,
        background: C.card, cursor: 'pointer', maxWidth: isNarrow ? '100%' : 260,
      }}
    >
      {selectedKey === null && <option value="">{ui.reviewPickTitle}</option>}
      {packsList !== null && packsList.length > 0 && (
        <optgroup label={`🎧 ${ui.packSection}`}>
          {packsList.map((p) => (
            <option key={p.dirname} value={`${PACK_PREFIX}${p.dirname}`}>
              {p.company}{p.job_title ? ` / ${p.job_title}` : ''}
            </option>
          ))}
        </optgroup>
      )}
      <optgroup label={`📜 ${ui.staticSection}`}>
        {VN_SCRIPTS.map((s) => (
          <option key={s.id} value={`${STATIC_PREFIX}${s.id}`}>
            {(lang === 'ja' ? s.titleJa : s.titleZh)}{s.sample ? ` (${ui.sampleTag})` : ''}
          </option>
        ))}
      </optgroup>
    </select>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18, paddingBottom: 40 }}>
      <div style={{
        position: 'sticky', top: 0, zIndex: 3,
        background: alpha(C.bg, 0.97), backdropFilter: 'blur(6px)',
        borderRadius: RADIUS.md, border: `1px solid ${alpha(C.milkTea, 0.6)}`,
        padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 14,
        flexWrap: 'wrap',
      }}>
        {scriptDropdown}
        <div style={{ fontSize: 12.5, fontWeight: 700, color: C.ink, flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {activeAct ? activeAct.label : ''}
        </div>
        <div style={{ fontSize: 11, color: C.sub, flexShrink: 0 }}>
          👀 {kpi.viewCount} · {formatMinutes(kpi.todaySeconds)}
        </div>
      </div>

      {controlsRow}

      {error && <div style={{ fontSize: 13, color: C.rosePink }}>{ui.packLoadFailed}: {error}</div>}

      {selectedKey === null && (
        <div style={{ fontSize: 13, color: C.sub }}>👆 {ui.reviewPickTitle}</div>
      )}

      {loadingSelected && (
        <div style={{ fontSize: 13, color: C.sub }}>{ui.packBuilding}</div>
      )}

      {!loadingSelected && selectedScript && group && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: isNarrow ? '1fr' : '240px minmax(0, 1fr)',
          gap: 20,
          alignItems: 'start',
        }}>
          <ReviewSidebar
            ui={ui}
            group={group}
            activeId={activeId}
            bookmarks={bookmarks}
            onToggleBookmark={toggleBookmark}
            onJump={jumpTo}
            isNarrow={isNarrow}
            query={query}
            onQuery={setQuery}
            hits={searchHits}
          />

          <ScriptDetail
            script={selectedScript}
            lang={lang}
            ui={ui}
            kaisetsu={kaisetsu}
            showInner={showInner}
            readingMode={readingMode}
            anchorId={anchorId}
          />
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------- 単一劇本の本文レンダリング

const ScriptDetail: React.FC<{
  script: VNScript;
  lang: VNLang;
  ui: typeof VN_UI['ja'];
  kaisetsu: boolean;
  showInner: boolean;
  readingMode: ReadingMode;
  anchorId: (scriptId: string, actId: number) => string;
}> = ({ script, lang, ui, kaisetsu, showInner, readingMode, anchorId: anchor }) => {
  const interviewer = lang === 'ja' ? script.interviewerJa : script.interviewerZh;
  const company = script._meta?.company;
  return (
    <div style={{
      background: C.bg, border: `1.5px solid ${alpha(C.milkTea, 0.55)}`,
      borderRadius: RADIUS.lg, boxShadow: SHADOW.sm, padding: '22px 24px', minWidth: 0,
    }}>
      <div style={{ marginBottom: 14 }}>
        {company ? (
          <>
            <div style={{ fontWeight: 700, fontSize: 19 }}>{company}</div>
            <div style={{ fontSize: 13, color: C.sub, marginTop: 2 }}>
              {script._meta?.jobTitle} · {lang === 'ja' ? '模擬面接' : '模擬面試'}
            </div>
          </>
        ) : (
          <div style={{ fontWeight: 700, fontSize: 18 }}>
            {lang === 'ja' ? script.titleJa : script.titleZh}
          </div>
        )}
        <div style={{ fontSize: 12, color: C.sub, marginTop: 4 }}>
          {ui.reportBy}：{interviewer}
        </div>
      </div>

      {script.acts.map((act) => (
        <div key={act.id} id={anchor(script.id, act.id)} style={{ marginBottom: 18, scrollMarginTop: 64 }}>
          <div style={{ borderBottom: `1.5px dashed ${alpha(C.milkTea, 0.7)}`, paddingBottom: 8, marginBottom: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 1, color: C.sub }}>
              {lang === 'ja' ? act.titleJa : act.titleZh}
              {(lang === 'ja' ? act.goalJa : act.goalZh) && (
                <span style={{ marginLeft: 10, fontWeight: 400, opacity: 0.85 }}>
                  🎯 {lang === 'ja' ? act.goalJa : act.goalZh}
                </span>
              )}
            </div>
            {act.framework && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                {act.framework.items.map((it, i) => (
                  <span key={i} style={{
                    fontSize: 11.5, color: '#3E6B4F', background: alpha('#3E6B4F', 0.1),
                    borderRadius: RADIUS.pill, padding: '2px 9px',
                  }}>
                    ✔ {lang === 'ja' ? it.ja : it.zh}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {act.lines.map((line) => (
              <ReviewChatLine
                key={line.id}
                line={line}
                lang={lang}
                kaisetsu={kaisetsu}
                showInner={showInner}
                script={script}
                readingMode={readingMode}
              />
            ))}
          </div>
        </div>
      ))}

      {script.report.aspects.length > 0 && (
        <div style={{ borderTop: `1.5px dashed ${alpha(C.milkTea, 0.8)}`, paddingTop: 14, marginTop: 6 }}>
          <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1.5, color: C.sub, marginBottom: 8 }}>
            {ui.reportTitle}
          </div>
          {script.report.aspects.map((a) => (
            <div key={a.labelJa} style={{ display: 'flex', gap: 10, alignItems: 'baseline', marginBottom: 4 }}>
              <span style={{ fontSize: 13, fontWeight: 700, minWidth: 100 }}>
                {lang === 'ja' ? a.labelJa : a.labelZh}
              </span>
              <span style={{ fontSize: 12, color: C.sub }}>{a.score}/5</span>
              <span style={{ fontSize: 12.5, color: C.sub }}>
                {lang === 'ja' ? a.commentJa : a.commentZh}
              </span>
            </div>
          ))}
          <div style={{ fontSize: 13, lineHeight: 1.7, color: C.ink, marginTop: 8 }}>
            {lang === 'ja' ? script.report.overallJa : script.report.overallZh}
          </div>
        </div>
      )}
      {script.noFavor && !script.report.aspects.length && (
        <div style={{ fontSize: 12.5, color: C.sub, marginTop: 4 }}>
          {lang === 'ja' ? script.report.overallJa : script.report.overallZh}
        </div>
      )}
    </div>
  );
};
