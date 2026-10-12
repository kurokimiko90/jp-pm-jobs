import { tStatic } from './i18n';

const json = async (r: Response) => {
  if (!r.ok) {
    // FastAPI の HTTPException は body の detail に理由を入れる。
    // ここで拾わないと画面には "400 Bad Request" しか出ず原因が分からない。
    let detail = '';
    try {
      const body = await r.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      /* body が JSON でない（502 等）— ステータス行だけで出す */
    }
    throw new Error(detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
};

export const get = (url: string) => fetch(url).then(json);

// /api/stats 在同一畫面被 App / Today / 來源篩選列各自呼叫；
// 短 TTL 內共用同一個 in-flight promise，避免重複請求
const STATS_TTL_MS = 30_000;
let statsCache: { promise: Promise<unknown>; at: number } | null = null;

export const getStats = (): Promise<any> => {
  const now = Date.now();
  if (!statsCache || now - statsCache.at > STATS_TTL_MS) {
    statsCache = { promise: get('/api/stats').catch((e) => { statsCache = null; throw e; }), at: now };
  }
  return statsCache.promise;
};

export const post = (url: string, body: unknown) =>
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(json);

export const openFolder = (path: string) =>
  post('/api/open-folder', { path }).catch((e) =>
    alert(tStatic('api_open_folder_failed').replace('{message}', e.message))
  );

export const openFile = (path: string) =>
  post('/api/open-file', { path }).catch((e) =>
    alert(tStatic('api_open_file_failed').replace('{message}', e.message))
  );

export interface OpenJdsResult {
  opened: number;
  by_source: Record<string, number>;
  matched: number;
  limit: number;
  skipped_over_limit: number;
  /** r-agent 職缺缺 source_id → 無法組出可開的 URL，被跳過的筆數 */
  skipped_ragent_no_source_id?: number;
}

/** 選択済みの求人 ID だけを開く（確認ダイアログ → 実行 → 結果表示まで一括）。
 *  ID を渡さない全件モードは意図せず数百タブ開くため、UI からは使わない。 */
export const batchOpenJds = async (ids: number[]): Promise<void> => {
  const uniq = Array.from(new Set(ids));
  if (uniq.length === 0) return;
  if (!confirm(tStatic('jobs_batch_open_confirm').replace('{count}', String(uniq.length)))) return;
  try {
    const r: OpenJdsResult = await post('/api/open-jds', { job_ids: uniq });
    const detail = Object.entries(r.by_source).map(([s, n]) => `${s}: ${n}`).join(', ');
    let msg = tStatic('jobs_batch_open_done')
      .replace('{count}', String(r.opened))
      .replace('{detail}', detail);
    if (r.skipped_over_limit > 0) {
      msg += `\n${tStatic('jobs_batch_open_capped')
        .replace('{limit}', String(r.limit))
        .replace('{rest}', String(r.skipped_over_limit))}`;
    }
    if (r.skipped_ragent_no_source_id) {
      msg += `\n${tStatic('jobs_batch_open_skipped')
        .replace('{count}', String(r.skipped_ragent_no_source_id))}`;
    }
    alert(msg);
  } catch (e) {
    alert(tStatic('api_open_jds_failed').replace('{message}', (e as Error).message));
  }
};
