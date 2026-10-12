import { useT } from '../../i18n';
import { useAppStatusLabels } from '../ui';

const WEEKDAY_JA = ['日', '月', '火', '水', '木', '金', '土'];

export const fmtDate = (iso: string): string => {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAY_JA[d.getDay()]})`;
};

/** 分群・漏斗・維度の表示名を 1 箇所に集約（維度ごとに辞書が違う）。 */
export const useTimelineLabels = () => {
  const t = useT();
  const statusLabels = useAppStatusLabels();

  const stage = (s: string): string =>
    s === 'shorui_pass' ? t.app_stats_funnel_shorui_pass : (statusLabels[s] ?? s);

  const DIM_TITLE: Record<string, string> = {
    channel: t.app_stats_dim_channel, job_type: t.app_stats_dim_job_type,
    recommend_band: t.app_stats_dim_recommend, score_band: t.app_stats_dim_score,
    mentions_ai: t.app_stats_dim_ai, domain: t.app_stats_dim_domain,
    employee_band: t.app_stats_dim_employee,
  };
  const dim = (d: string): string => DIM_TITLE[d] ?? d;

  const key = (d: string, k: string): string => {
    if (d === 'job_type') {
      switch (k) {
        case 'pdm': return t.role_type_pdm;
        case 'pjm': return t.role_type_pjm;
        case 'consulting': return t.role_type_consulting;
        case 'other': return t.role_type_other;
        default: return t.app_stats_unclassified;
      }
    }
    if (d === 'mentions_ai') {
      return k === 'yes' ? t.jobs_ai_mentioned : k === 'no' ? t.app_stats_ai_no : t.app_stats_unclassified;
    }
    if (d === 'domain') {
      switch (k) {
        case 'fintech': return t.app_stats_domain_fintech;
        case 'saas': return t.app_stats_domain_saas;
        case 'ai': return t.app_stats_domain_ai;
        default: return t.app_stats_domain_none;
      }
    }
    if (d === 'employee_band') {
      return k === 'unknown' ? t.app_stats_unclassified : `${k}${t.app_stats_employee_suffix}`;
    }
    if (d === 'channel') return k === 'direct' ? t.channel_direct : k;
    if (k === 'unknown') return t.app_stats_unclassified; // score/recommend 帯の未分析
    return k;
  };

  return { stage, dim, key, statusLabels };
};
