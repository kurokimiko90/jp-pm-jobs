import React, { useEffect, useState } from 'react';
import { get } from '../api';
import { Empty } from '../components/ui';
import { useT } from '../i18n';
import type { TimelineData } from '../components/timeline/types';
import { SectionNav, TimelineSection } from '../components/timeline/parts';
import { fmtDate, useTimelineLabels } from '../components/timeline/labels';
import {
  upcomingInsights, overviewInsights, trendInsights, waitInsights, factorInsights, qualityInsights,
} from '../components/timeline/insights';
import { UpcomingBody, OverviewBody, TrendBody, WaitBody, QualityBody } from '../components/timeline/sections';
import { FactorsBody } from '../components/timeline/FactorsBody';
import { TimelineHero } from '../components/timeline/hero';

/**
 * データ統計ページ。カードを「答える問い」ごとの 6 区にまとめ、各区の頭に
 * 問い・読み方・規則で算出した結論を置く（LLM 不使用、API の数字のみ）。
 *   ① 近期行程 ② 卡在哪一關 ③ 趨勢 ④ 等待 ⑤ 影響因素 ⑥ 可信度
 */
export const ApplicationsTimeline: React.FC = () => {
  const t = useT();
  const labels = useTimelineLabels();
  const [data, setData] = useState<TimelineData | null>(null);

  useEffect(() => {
    get('/api/applications/timeline').then(setData).catch(() => setData(null));
  }, []);

  if (!data) return <Empty>{t.app_timeline_no_data}</Empty>;

  const baseline = data.summary.pass_rate;
  const sections = [
    {
      id: 'tl-upcoming', short: t.tl_s1_short, title: t.tl_s1_title, hint: t.tl_s1_hint,
      insights: upcomingInsights(data.agenda.upcoming, data.time?.interview_load, fmtDate),
      body: <UpcomingBody data={data} />,
    },
    {
      id: 'tl-overview', short: t.tl_s2_short, title: t.tl_s2_title, hint: t.tl_s2_hint,
      insights: overviewInsights(data.funnel, data.rejection_dist, labels.stage),
      body: <OverviewBody data={data} />,
    },
    {
      id: 'tl-trend', short: t.tl_s3_short, title: t.tl_s3_title, hint: t.tl_s3_hint,
      insights: trendInsights(data.time?.weekly, baseline),
      body: <TrendBody data={data} />,
    },
    {
      id: 'tl-wait', short: t.tl_s4_short, title: t.tl_s4_title, hint: t.tl_s4_hint,
      insights: waitInsights(data.shorui_turnaround, data.time?.wait_curve),
      body: <WaitBody data={data} />,
    },
    {
      id: 'tl-factors', short: t.tl_s5_short, title: t.tl_s5_title, hint: t.tl_s5_hint,
      insights: factorInsights(data.segments, baseline, labels.dim, labels.key),
      body: <FactorsBody data={data} />,
    },
    {
      id: 'tl-quality', short: t.tl_s6_short, title: t.tl_s6_title, hint: t.tl_s6_hint,
      insights: qualityInsights(data.summary, data.quality),
      body: <QualityBody data={data} />,
    },
  ];

  return (
    <div className="tl-page">
      <TimelineHero data={data} />
      <SectionNav sections={sections} />
      {sections.map((s, i) => (
        <TimelineSection key={s.id} id={s.id} index={i + 1} short={s.short} title={s.title} hint={s.hint} insights={s.insights}>
          {s.body}
        </TimelineSection>
      ))}
    </div>
  );
};
