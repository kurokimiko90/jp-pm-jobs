import React, { useEffect, useMemo, useState } from 'react';
import { get, post } from '../api';
import { C, scoreColor, alpha, getTierLabel } from '../theme';
import { Empty, PageHeader, useIsNarrow } from '../components/ui';
import { useT } from '../i18n';

const StrategyConfig: React.FC = () => {
  const t = useT();
  const [cfg, setCfg] = useState<any>(null);
  useEffect(() => { get('/api/strategy-config').then(setCfg).catch(() => {}); }, []);
  if (!cfg) return null;

  const sc = cfg.scoring || {};
  const wv = cfg.waves || {};
  const daily = cfg.daily || {};
  const loc = cfg.location_scores || {};

  return (
    <div style={{ background: C.bg, borderRadius: 20, padding: '24px 28px', marginTop: 24, boxShadow: '0 10px 40px rgba(90,82,72,0.13)' }}>
      <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: 1, color: C.ink, marginBottom: 16 }}>
        {t.scoring_strategy_config}
      </div>
      <div style={{ fontSize: 12, color: C.sub, marginBottom: 16 }}>
        {t.scoring_strategy_config_hint}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 20 }}>
        {/* 加權公式 */}
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.ink, marginBottom: 10 }}>{t.scoring_formula}</div>
          {[
            { label: t.scoring_recommend_weight, value: sc.recommend_weight },
            { label: t.scoring_location_weight, value: sc.location_weight },
            { label: t.scoring_score_weight, value: sc.score_weight },
            { label: t.scoring_agent_penalty, value: `-${sc.agent_penalty}` },
          ].map((r) => (
            <div key={r.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${C.border}` }}>
              <span style={{ fontSize: 13, color: 'rgba(90,82,72,0.7)' }}>{r.label}</span>
              <span style={{ fontSize: 13, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>×{r.value}</span>
            </div>
          ))}
        </div>

        {/* 波次設定 */}
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.ink, marginBottom: 10 }}>{t.scoring_waves}</div>
          {(wv.max_per_wave || []).map((n: number, i: number) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${C.border}` }}>
              <span style={{ fontSize: 13, color: 'rgba(90,82,72,0.7)' }}>{t.strategy_wave_label} {i + 1}</span>
              <span style={{ fontSize: 13, fontWeight: 700 }}>{n} {t.unit_company_count}</span>
            </div>
          ))}
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${C.border}` }}>
            <span style={{ fontSize: 13, color: 'rgba(90,82,72,0.7)' }}>{t.scoring_cooldown_days}</span>
            <span style={{ fontSize: 13, fontWeight: 700 }}>{wv.cooldown_days} {t.unit_day}</span>
          </div>
        </div>

        {/* 每日設定 + 地域分數 */}
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.ink, marginBottom: 10 }}>{t.scoring_daily_location}</div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${C.border}` }}>
            <span style={{ fontSize: 13, color: 'rgba(90,82,72,0.7)' }}>{t.scoring_daily_limit}</span>
            <span style={{ fontSize: 13, fontWeight: 700 }}>{daily.max_submit} {t.unit_company_count}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${C.border}` }}>
            <span style={{ fontSize: 13, color: 'rgba(90,82,72,0.7)' }}>{t.scoring_jp_hours}</span>
            <span style={{ fontSize: 13, fontWeight: 700 }}>{daily.jp_hours?.[0]}:00–{daily.jp_hours?.[1]}:00</span>
          </div>
          {Object.entries(loc).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${C.border}` }}>
              <span style={{ fontSize: 13, color: 'rgba(90,82,72,0.7)' }}>{k}</span>
              <span style={{ fontSize: 13, fontWeight: 700 }}>{v as number}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

interface TuningSuggestion {
  tier: string; pass_rate: number; passed: number; total: number;
  overall_rate: number; current_weight: number; suggested_weight: number; message: string;
}

/** 拒絕原因碼累積足量後的調校建議（純規則，零 LLM 成本）— 見規劃書 §1.4。 */
const TuningSuggestions: React.FC = () => {
  const t = useT();
  const [data, setData] = useState<{ suggestions: TuningSuggestion[]; sample_size: number; min_sample: number } | null>(null);
  useEffect(() => { get('/api/scoring/suggestions').then(setData).catch(() => {}); }, []);
  if (!data) return null;

  return (
    <div style={{ background: C.bg, borderRadius: 20, padding: '24px 28px', marginTop: 24, boxShadow: '0 10px 40px rgba(90,82,72,0.13)' }}>
      <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: 1, color: C.ink, marginBottom: 6 }}>
        {t.scoring_tuning_title}
      </div>
      <div style={{ fontSize: 12, color: C.sub, marginBottom: 16 }}>
        {t.scoring_tuning_hint.replace('{sample}', String(data.sample_size)).replace('{min}', String(data.min_sample))}
      </div>
      {data.suggestions.length === 0 ? (
        <div style={{ fontSize: 13, color: C.sub }}>
          {t.scoring_tuning_empty}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {data.suggestions.map((s) => (
            <div key={s.tier} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px',
              borderRadius: 14, background: 'rgba(242,181,192,0.14)',
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 2 }}>{getTierLabel(s.tier)}</div>
                <div style={{ fontSize: 12.5, color: 'rgba(90,82,72,0.75)', lineHeight: 1.6 }}>
                  {t.scoring_tuning_message
                    .replace('{tier}', getTierLabel(s.tier))
                    .replace('{pass_rate}', String(s.pass_rate))
                    .replace('{passed}', String(s.passed))
                    .replace('{total}', String(s.total))
                    .replace('{overall_rate}', String(s.overall_rate))
                    .replace('{current_weight}', String(s.current_weight))
                    .replace('{suggested_weight}', String(s.suggested_weight))}
                </div>
              </div>
              <div style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                <div style={{ fontSize: 12, color: C.sub }}>{t.scoring_tier_preference}</div>
                <div style={{ fontSize: 16, fontWeight: 800 }}>
                  {s.current_weight} <span style={{ color: C.sub, fontWeight: 500 }}>›</span> <span style={{ color: C.gold }}>{s.suggested_weight}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

type Rule = 'jp' | 'overseas';

const initials = (s: string) => (s || '?').split(/[\s\-・（(]/)[0].charAt(0).toUpperCase();

export const Scoring: React.FC = () => {
  const t = useT();
  const isNarrow = useIsNarrow();
  const [meta, setMeta] = useState<any>(null);
  const [rule, setRule] = useState<Rule>('jp');
  const [w, setW] = useState<Record<string, number>>({});
  const [preview, setPreview] = useState<any>(null);
  const [previewError, setPreviewError] = useState(false);

  useEffect(() => { get('/api/scoring').then(setMeta); }, []);

  const baseWeights = (m: any, r: Rule): Record<string, number> =>
    ({ ...(r === 'jp' ? m.weights : m.overseas_weights) });

  // rule 切替で weights をリセット
  useEffect(() => { if (meta) { setW(baseWeights(meta, rule)); setPreview(null); } }, [meta, rule]);

  const dims = useMemo(() => Object.keys(w), [w]);
  const total = useMemo(() => dims.reduce((s, k) => s + (w[k] ?? 0), 0), [w, dims]);

  // 1つ動かす → 残りを等比で調整して 100 を保つ
  const adjust = (key: string, val: number) => {
    const others = dims.filter((k) => k !== key);
    const restOld = others.reduce((s, k) => s + (w[k] ?? 0), 0);
    const restNew = 100 - val;
    const next: Record<string, number> = { [key]: val };
    if (restOld > 0) others.forEach((k) => { next[k] = Math.round((w[k] ?? 0) / restOld * restNew); });
    else others.forEach((k) => { next[k] = Math.round(restNew / others.length); });
    const diff = 100 - Object.values(next).reduce((s, x) => s + x, 0);
    if (others[0]) next[others[0]] = (next[others[0]] ?? 0) + diff;
    setW(next);
    runPreview(next);
  };

  // 失敗必須可見：靜默吞掉會讓「拖了滑桿卻沒預覽」看起來像功能壞掉
  const runPreview = async (weights: Record<string, number>) => {
    try {
      setPreview(await post('/api/rescore', weights));
      setPreviewError(false);
    } catch {
      setPreview(null);
      setPreviewError(true);
    }
  };

  const reset = () => { setW(baseWeights(meta, rule)); setPreview(null); };

  if (!meta) return <Empty>{t.jobs_loading}</Empty>;

  const dirty = dims.some((k) => (w[k] ?? 0) !== ((rule === 'jp' ? meta.weights : meta.overseas_weights)[k] ?? 0));
  const penalties: { factor: number; label: string }[] = Object.entries(meta.penalties || {}).map(([k, v]: any) => ({
    factor: v.factor,
    label: ({
      eng_only: t.scoring_penalty_eng_only,
      pm_gate: t.scoring_penalty_pm_gate,
    } as Record<string, string>)[k] || v.label,
  }));
  const ruleLabel = rule === 'jp' ? t.scoring_rule_jp_label : t.scoring_rule_overseas_label;
  const shortLabels: Record<string, string> = {
    salary_fit: t.scoring_dim_salary_fit,
    market_keywords: t.scoring_dim_market_keywords,
    role_fit: t.scoring_dim_role_fit,
    tier_preference: t.scoring_dim_tier_preference,
    tech_overlap: t.scoring_dim_tech_overlap,
    domain: t.scoring_dim_domain,
    remote_visa: t.scoring_dim_remote_visa,
  };

  const tab = (active: boolean): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', height: 40, padding: '0 18px', borderRadius: 999,
    fontSize: 14, fontWeight: active ? 700 : 500, letterSpacing: 1, cursor: 'pointer', border: 'none',
    color: C.ink, background: active ? C.milkTea : 'transparent',
    boxShadow: active ? 'none' : 'inset 0 0 0 1.5px rgba(232,213,183,0.95)',
  });

  return (
    <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <PageHeader title={t.scoring_page_title} subtitle={t.scoring_page_subtitle} />

      {/* tabs */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <button onClick={() => setRule('jp')} style={tab(rule === 'jp')}>{t.scoring_rule_jp}</button>
        <button onClick={() => setRule('overseas')} style={tab(rule === 'overseas')}>{t.scoring_rule_overseas}</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? '1fr' : '1.3fr 1fr', gap: 18, alignItems: 'start' }}>
        {/* 左：dimensions */}
        <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '24px 28px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
            <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)' }}>
              {t.scoring_dimensions_count.replace('{count}', String(dims.length))}
            </span>
            {dirty && (
              <button onClick={reset} style={{
                fontSize: 12, fontWeight: 700, letterSpacing: 1, color: 'rgba(90,82,72,0.5)', cursor: 'pointer',
                padding: '6px 12px', borderRadius: 999, border: '1.5px solid rgba(232,213,183,0.95)', background: 'none',
              }}>{t.scoring_reset_default}</button>
            )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {dims.map((k) => (
              <div key={k}>
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontSize: 15, fontWeight: 700, letterSpacing: '0.5px' }}>{shortLabels[k] || k}</span>
                  <span style={{ fontSize: 18, fontWeight: 800, color: C.gold, fontVariantNumeric: 'tabular-nums' }}>
                    {w[k] ?? 0}<span style={{ fontSize: 12, color: 'rgba(90,82,72,0.45)', fontWeight: 500 }}> %</span>
                  </span>
                </div>
                <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.55)', letterSpacing: '0.5px', marginBottom: 10, lineHeight: 1.5 }}>
                  {shortLabels[k] || ''}
                </div>
                <input
                  type="range" min={0} max={60} value={w[k] ?? 0}
                  onChange={(e) => adjust(k, Number(e.target.value))}
                  style={{ width: '100%', accentColor: C.gold, height: 6 }}
                />
              </div>
            ))}
          </div>

          <div style={{ marginTop: 22, paddingTop: 18, borderTop: '1.5px solid rgba(232,213,183,0.6)', fontSize: 13, fontWeight: 500, letterSpacing: 1, color: 'rgba(90,82,72,0.7)' }}>
            {t.scoring_total_with_hint.replace('{total}', String(total))}
          </div>
        </div>

        {/* 右：preview + penalties */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '22px 24px' }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 8 }}>
              {t.scoring_preview_jobs}
            </div>
            {!preview && !previewError && (
              <div style={{ fontSize: 13, color: C.sub, padding: '14px 0' }}>{t.scoring_preview_idle}</div>
            )}
            {previewError && (
              <div style={{ fontSize: 13, color: C.errorRed, padding: '14px 0' }}>{t.scoring_preview_error}</div>
            )}
            {preview && (!preview.items || preview.items.length === 0) && (
              <div style={{ fontSize: 13, color: C.sub, padding: '14px 0' }}>{t.scoring_preview_empty}</div>
            )}
            {preview?.items?.slice(0, 8).map((p: any) => {
              const delta = Math.round((p.new_score - p.old_score) * 10) / 10;
              const dColor = delta > 0 ? C.successGreen : delta < 0 ? C.errorRed : C.sub;
              return (
                <div key={p.id} style={{ display: 'grid', gridTemplateColumns: '32px 1fr auto auto', gap: 12, alignItems: 'center', padding: '11px 0', borderTop: '1px dashed rgba(232,213,183,0.8)' }}>
                  <span style={{ width: 32, height: 32, borderRadius: 9, background: `${alpha(scoreColor(p.old_score), 0.13)}`, color: scoreColor(p.old_score), display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 800 }}>
                    {initials(p.company)}
                  </span>
                  <span style={{ fontSize: 14, fontWeight: 700, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.company}</span>
                  <span style={{ fontSize: 14, fontWeight: 500, color: 'rgba(90,82,72,0.45)', fontVariantNumeric: 'tabular-nums' }}>
                    {Math.round(p.old_score)} › <span style={{ color: C.gold, fontWeight: 800 }}>{Math.round(p.new_score)}</span>
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: dColor, fontVariantNumeric: 'tabular-nums', minWidth: 34, textAlign: 'right' }}>
                    {delta > 0 ? `+${delta}` : delta}
                  </span>
                </div>
              );
            })}
          </div>

          <div style={{ background: 'rgba(242,181,192,0.14)', borderRadius: 26, padding: '22px 24px' }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.6)', marginBottom: 14 }}>
              {t.scoring_penalty_rules.replace('{rule}', ruleLabel)}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {penalties.map((p, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span style={{ width: 7, height: 7, borderRadius: 999, background: C.rosePink, flex: 'none', marginTop: 8 }} />
                  <span style={{ fontSize: 14, fontWeight: 500, lineHeight: 1.65, letterSpacing: '0.5px' }}>
                    {p.label}　<span style={{ color: C.sub, fontWeight: 700 }}>×{p.factor}</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <TuningSuggestions />
      <StrategyConfig />
    </div>
  );
};
