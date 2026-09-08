import React, { useCallback, useEffect, useState } from 'react';
import {
  Compass, Briefcase, PaperPlaneTilt, FileText, EnvelopeSimple,
  ChartBar, Heartbeat, Target, SlidersHorizontal, List, X,
  Robot,
  type Icon,
} from '@phosphor-icons/react';
import { get } from './api';
import { useHashRoute } from './filters/useHashFilters';
import { useIsNarrow } from './components/ui';
import { useLang, useT } from './i18n';
import { Today } from './pages/Today';
import { JobsHub } from './pages/JobsHub';
import { Scoring } from './pages/Scoring';
import { PacksHub } from './pages/PacksHub';
import { InterviewPrepHub } from './pages/InterviewPrepHub';
import { Genki } from './pages/Genki';
import { ApplicationsHub } from './pages/ApplicationsHub';
import { Inbox } from './pages/Inbox';
import { Analytics } from './pages/Analytics';
import { Assistant } from './pages/Assistant';
import { JobDrawer } from './components/JobDrawer';

type Page = 'today' | 'jobs' | 'analytics' | 'packs' | 'scoring' | 'interview-prep' | 'genki' | 'inbox' | 'applications' | 'assistant';

const PAGES: readonly Page[] = ['today', 'jobs', 'analytics', 'packs', 'scoring', 'interview-prep', 'genki', 'inbox', 'applications', 'assistant'];

const ICON: Record<Page, Icon> = {
  today: Compass,
  jobs: Briefcase,
  applications: PaperPlaneTilt,
  packs: FileText,
  inbox: EnvelopeSimple,
  analytics: ChartBar,
  genki: Heartbeat,
  'interview-prep': Target,
  scoring: SlidersHorizontal,
  assistant: Robot,
};

type NavGroup = { header: string; items: { key: Page; label: string }[] };

const NAV_STRUCTURE: { header: string; items: { key: Page; label: string }[] }[] = [
  { header: 'nav_main', items: [{ key: 'today', label: 'nav_today' }, { key: 'assistant', label: 'nav_assistant' }] },
  { header: 'nav_search', items: [{ key: 'jobs', label: 'nav_jobs' }, { key: 'analytics', label: 'nav_analytics' }] },
  { header: 'nav_pipeline', items: [{ key: 'applications', label: 'nav_applications' }, { key: 'inbox', label: 'nav_inbox' }] },
  { header: 'nav_prep_group', items: [{ key: 'packs', label: 'nav_packs' }, { key: 'interview-prep', label: 'nav_interview_group' }] },
  { header: 'nav_myself', items: [{ key: 'genki', label: 'nav_genki' }] },
  { header: 'nav_settings', items: [{ key: 'scoring', label: 'nav_scoring' }] },
];

export const App: React.FC = () => {
  const { lang, setLang } = useLang();
  const t = useT();
  // 頁面 + hub tab 進 hash（#/jobs/recommend）：重整不再回到「今日」，檢視可書籤/分享
  const [route, navigate] = useHashRoute();
  const page: Page = PAGES.includes(route[0] as Page) ? (route[0] as Page) : 'today';
  const setPage = (p: Page) => navigate([p]);
  const [drawerJob, setDrawerJob] = useState<number | null>(null);
  const closeDrawer = useCallback(() => setDrawerJob(null), []);
  const [profile, setProfile] = useState<{ name_romaji?: string; name_ja?: string } | null>(null);
  const isNarrow = useIsNarrow();
  const [menuOpen, setMenuOpen] = useState(false);

  const NAV_GROUPS: NavGroup[] = NAV_STRUCTURE.map((group) => ({
    header: t[group.header as keyof typeof t],
    items: group.items.map((item) => ({
      key: item.key,
      label: t[item.label as keyof typeof t],
    })),
  }));

  useEffect(() => { get('/api/profile-summary').then(setProfile).catch(() => {}); }, []);

  return (
    <div className="app-shell" style={{ display: 'flex', minHeight: '100vh' }}>
      {/* 窄螢幕：頂欄 + 漢堡選單 */}
      {isNarrow && (
        <div className="mobile-topbar">
          <button
            onClick={() => setMenuOpen(true)}
            aria-label={t.menu_open}
            className="mobile-menu-button"
          >
            <List size={22} />
          </button>
          <img src="/jobpilot-logo.svg" alt="" width={30} height={30} />
          <div className="mobile-brand-copy">
            <span>JobPilot</span>
            <small>CAREER COPILOT</small>
          </div>
        </div>
      )}
      {isNarrow && menuOpen && (
        <div
          onClick={() => setMenuOpen(false)}
          className="sidebar-scrim"
        />
      )}

      <aside
        className={`app-sidebar${isNarrow ? ' app-sidebar--drawer' : ''}`}
        style={isNarrow ? { transform: menuOpen ? 'translateX(0)' : 'translateX(-105%)' } : undefined}
      >
        <div className="sidebar-brand">
          <span className="sidebar-brand-mark">
            <img src="/jobpilot-logo.svg" alt="" width={42} height={42} />
          </span>
          <span className="sidebar-brand-copy">
            <strong>JobPilot</strong>
            <small>CAREER COPILOT</small>
          </span>
          {isNarrow && (
            <button
              onClick={() => setMenuOpen(false)}
              aria-label={t.menu_close}
              className="sidebar-close-button"
            >
              <X size={18} />
            </button>
          )}
        </div>
        <nav className="sidebar-nav">
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.header}>
              <div className="nav-group-label">{group.header}</div>
              {group.items.map((n) => {
                const IconComp = ICON[n.key];
                const isActive = page === n.key;
                return (
                  <button
                    key={n.key}
                    onClick={() => { setPage(n.key); setMenuOpen(false); }}
                    className={`nav-item${isActive ? ' nav-item--active' : ''}`}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    <span className="nav-item-icon">
                      <IconComp size={19} weight={isActive ? 'duotone' : 'regular'} />
                    </span>
                    <span className="nav-item-label">{n.label}</span>
                    {isActive && <span className="nav-active-marker" aria-hidden="true" />}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          {(profile?.name_romaji || profile?.name_ja) && (
            <div className="sidebar-profile">
              <span className="sidebar-profile-dot" aria-hidden="true" />
              <span>{profile.name_romaji || profile.name_ja}</span>
            </div>
          )}
          <div className="language-switcher">
            <button
              onClick={() => setLang('zh')}
              className={lang === 'zh' ? 'language-button language-button--active' : 'language-button'}
            >
              {t.lang_zh}
            </button>
            <button
              onClick={() => setLang('ja')}
              className={lang === 'ja' ? 'language-button language-button--active' : 'language-button'}
            >
              {t.lang_ja}
            </button>
            <button
              onClick={() => setLang('en')}
              className={lang === 'en' ? 'language-button language-button--active' : 'language-button'}
            >
              {t.lang_en}
            </button>
          </div>
        </div>
      </aside>

      <main className="app-main" style={{ flex: 1 }}>
        {page === 'today' && (
          <Today
            openJob={setDrawerJob}
            goGenki={() => setPage('genki')}
            goJobs={() => setPage('jobs')}
            goTailored={() => setPage('packs')}
            goApplications={(tab) => navigate(tab === 'applications' ? ['applications'] : ['applications', tab])}
          />
        )}
        {page === 'jobs' && <JobsHub openJob={setDrawerJob} selected={drawerJob} />}
        {page === 'applications' && <ApplicationsHub openJob={setDrawerJob} />}
        {page === 'packs' && <PacksHub openJob={setDrawerJob} />}
        {page === 'analytics' && <Analytics />}
        {page === 'scoring' && <Scoring />}
        {page === 'inbox' && <Inbox openJob={setDrawerJob} />}
        {page === 'interview-prep' && <InterviewPrepHub />}
        {page === 'genki' && <Genki goDojo={() => navigate(['interview-prep'])} />}
        {page === 'assistant' && <Assistant openJob={setDrawerJob} />}
      </main>

      {drawerJob != null && <JobDrawer jobId={drawerJob} onClose={closeDrawer} />}
    </div>
  );
};
