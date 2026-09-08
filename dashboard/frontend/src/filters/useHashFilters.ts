import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Hash 路由 + 篩選狀態持久化（無路由庫，~100 行）。
 * 格式：#/jobs/recommend?min=70&loc=japan
 * - path 段 = 頁面 / hub tab（useHashRoute，pushState → 可用瀏覽器返回鍵）
 * - query 段 = 該頁篩選（useHashFilters，replaceState → 不灌爆歷史）
 * 頁面切換清空 query；重新整理 / 書籤 / 分享連結皆可還原完整檢視。
 */

export type FilterValue = string | number | boolean | string[];

const readHash = (): { path: string[]; params: URLSearchParams } => {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const qIdx = raw.indexOf('?');
  const pathPart = qIdx >= 0 ? raw.slice(0, qIdx) : raw;
  const queryPart = qIdx >= 0 ? raw.slice(qIdx + 1) : '';
  return { path: pathPart.split('/').filter(Boolean), params: new URLSearchParams(queryPart) };
};

const writeHash = (path: string[], params: URLSearchParams, push: boolean): void => {
  const qs = params.toString();
  const next = `#/${path.join('/')}${qs ? `?${qs}` : ''}`;
  if (window.location.hash === next) return;
  if (push) {
    window.location.hash = next;
  } else {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${next}`);
  }
};

const serialize = (v: FilterValue): string =>
  Array.isArray(v) ? v.join(',') : typeof v === 'boolean' ? (v ? '1' : '') : String(v);

const deserialize = (raw: string, def: FilterValue): FilterValue => {
  if (Array.isArray(def)) return raw ? raw.split(',') : [];
  if (typeof def === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : def;
  }
  if (typeof def === 'boolean') return raw === '1';
  return raw;
};

/** 頁面 / hub tab 路由。navigate 會 push 歷史並清空 query。 */
export const useHashRoute = (): [string[], (path: string[]) => void] => {
  const [path, setPath] = useState<string[]>(() => readHash().path);
  useEffect(() => {
    const onChange = () => setPath(readHash().path);
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  const navigate = useCallback((p: string[]) => {
    setPath(p);
    writeHash(p, new URLSearchParams(), true);
  }, []);
  return [path, navigate];
};

/**
 * 頁面篩選狀態，同步到 hash query。非預設值才寫入 URL。
 * 回傳 [state, update(patch), reset, dirty]。
 */
export const useHashFilters = <T extends Record<string, FilterValue>>(
  defaults: T,
): [T, (patch: Partial<T>) => void, () => void, boolean] => {
  const defRef = useRef(defaults);

  const parse = useCallback((params: URLSearchParams): T => {
    const out: Record<string, FilterValue> = {};
    for (const [k, def] of Object.entries(defRef.current)) {
      const raw = params.get(k);
      out[k] = raw === null ? def : deserialize(raw, def);
    }
    return out as T;
  }, []);

  const [state, setState] = useState<T>(() => parse(readHash().params));
  const stateRef = useRef(state);

  useEffect(() => {
    const onChange = () => {
      const next = parse(readHash().params);
      stateRef.current = next;
      setState(next);
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, [parse]);

  const update = useCallback((patch: Partial<T>) => {
    const next = { ...stateRef.current, ...patch };
    stateRef.current = next;
    setState(next);
    const { path } = readHash();
    const params = new URLSearchParams();
    for (const [k, def] of Object.entries(defRef.current)) {
      const sv = serialize(next[k]);
      if (sv !== serialize(def)) params.set(k, sv);
    }
    writeHash(path, params, false);
  }, []);

  const reset = useCallback(() => update({ ...defRef.current }), [update]);

  const dirty = Object.entries(defRef.current).some(
    ([k, def]) => serialize(state[k]) !== serialize(def),
  );

  return [state, update, reset, dirty];
};
