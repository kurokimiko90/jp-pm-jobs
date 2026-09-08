import React, { useCallback, useEffect, useState } from 'react';
import { get, post } from '../api';
import { C } from '../theme';
import { useT } from '../i18n';
import { Badge, Card, Empty, FilterChip, Pill, PageHeader, Pager } from '../components/ui';

interface Mail {
  id: number;
  gmail_msg_id: string;
  thread_id: string | null;
  job_id: number | null;
  sender: string | null;
  subject: string | null;
  received_at: string | null;
  category: string | null;
  confidence: number | null;
  summary: string | null;
  draft_id: string | null;
  status: string;
  company: string | null;
  title: string | null;
}

interface InboxData {
  mails: Mail[];
  categories: Record<string, number>;
}

const CAT_LABEL: Record<string, { labelKey: string; color: string }> = {
  interview_invite: { labelKey: 'inbox_category_interview_invite', color: C.successGreen },
  scheduling:       { labelKey: 'inbox_category_scheduling', color: C.progressIndigo },
  initial_contact:  { labelKey: 'inbox_category_initial_contact', color: C.lavender },
  offer:            { labelKey: 'inbox_category_offer', color: C.gold },
  rejection:        { labelKey: 'inbox_category_rejection', color: C.errorRed },
  other:            { labelKey: 'inbox_category_other', color: C.sub },
};

const GMAIL_DRAFTS_URL = 'https://mail.google.com/mail/u/0/#drafts';

const actionBtn: React.CSSProperties = {
  borderRadius: 999,
  padding: '5px 12px',
  border: `1px solid ${C.border}`,
  background: C.card,
  color: C.ink,
  fontSize: 12,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

export const Inbox: React.FC<{ openJob: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const [data, setData] = useState<InboxData | null>(null);
  const [filter, setFilter] = useState<string>('all');
  const [scanning, setScanning] = useState(false);
  const [order, setOrder] = useState<'desc' | 'asc'>('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const load = useCallback(() => { get('/api/inbox').then(setData); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [filter, order]);

  const scan = async () => {
    setScanning(true);
    await post('/api/inbox/scan', { days: 7 });
    // 背景 subprocess，幾秒後重抓
    setTimeout(() => { load(); setScanning(false); }, 5000);
  };

  const setStatus = async (id: number, status: string) => {
    await post(`/api/inbox/${id}/status`, { status });
    load();
  };

  const mails = !data ? [] : filter === 'all'
    ? data.mails
    : data.mails.filter((m) => (m.category || 'other') === filter);
  const sortedMails = order === 'desc'
    ? mails // 後端已 ORDER BY received_at DESC, id DESC
    : [...mails].sort((a, b) =>
        (a.received_at ?? '').localeCompare(b.received_at ?? '') || a.id - b.id);
  const total = sortedMails.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedMails = sortedMails.slice((safePage - 1) * pageSize, safePage * pageSize);

  if (!data) return <Empty>{t.inbox_none}</Empty>;

  return (
    <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <PageHeader
        title={t.inbox_title}
        right={<Pill active onClick={scanning ? undefined : scan}>{scanning ? '…' : t.inbox_scan}</Pill>}
      />

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        <Pill active={filter === 'all'} onClick={() => setFilter('all')}>
          {t.inbox_all} {data.mails.length}
        </Pill>
        {Object.entries(data.categories).map(([cat, n]) => (
          <Pill key={cat} active={filter === cat} onClick={() => setFilter(cat)}>
            {t[(CAT_LABEL[cat] || CAT_LABEL.other).labelKey as keyof typeof t]} {n}
          </Pill>
        ))}
        <span style={{ marginLeft: 'auto' }}>
          <FilterChip active onClick={() => setOrder(order === 'desc' ? 'asc' : 'desc')}>
            {t.inbox_sort_received}{order === 'desc' ? ' ↓' : ' ↑'}
          </FilterChip>
        </span>
      </div>

      {mails.length === 0 ? (
        <Empty>{t.inbox_empty}</Empty>
      ) : (
        <>
          <div className="inbox-grid" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {pagedMails.map((m) => {
              const cat = CAT_LABEL[m.category || 'other'] || CAT_LABEL.other;
              return (
                <Card key={m.id} style={{ height: '100%', opacity: m.status === 'skipped' ? 0.5 : 1 }}>
                  <div className="inbox-card-layout" style={{ display: 'flex', justifyContent: 'space-between', gap: 12, minWidth: 0 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <Badge color={cat.color}>{t[cat.labelKey as keyof typeof t]}</Badge>
                      {m.confidence != null && (
                        <span style={{ fontSize: 11, color: C.sub }}>{Math.round(m.confidence * 100)}%</span>
                      )}
                      {m.status === 'sent' && <Badge color={C.successGreen}>{t.inbox_status_sent}</Badge>}
                      {m.status === 'draft_ready' && <Badge color={C.gold}>{t.inbox_status_draft}</Badge>}
                    </div>
                    <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {m.subject || t.inbox_no_subject}
                    </div>
                    <div style={{ fontSize: 12, color: C.sub, marginBottom: 6 }}>
                      {m.sender} · {m.received_at}
                    </div>
                    {m.summary && (
                      <div style={{ fontSize: 13, color: C.ink, marginBottom: 8 }}>{m.summary}</div>
                    )}
                    <div style={{ fontSize: 12 }}>
                      {m.job_id != null ? (
                        <span
                          onClick={() => openJob(m.job_id as number)}
                          style={{ color: C.progressIndigo, cursor: 'pointer', textDecoration: 'underline' }}
                        >
                          {m.company || m.title || `job #${m.job_id}`}
                        </span>
                      ) : (
                        <span style={{ color: C.sub }}>{t.inbox_unmatched}</span>
                      )}
                    </div>
                  </div>

                  <div className="inbox-card-actions" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {m.draft_id && (
                      <a
                        href={GMAIL_DRAFTS_URL}
                        target="_blank"
                        rel="noreferrer"
                        style={{ ...actionBtn, background: C.gold, fontWeight: 600, textAlign: 'center', textDecoration: 'none' }}
                      >
                        {t.inbox_open_draft}
                      </a>
                    )}
                    {m.status !== 'sent' && (
                      <button onClick={() => setStatus(m.id, 'sent')} style={actionBtn}>{t.inbox_mark_sent}</button>
                    )}
                    {m.status !== 'skipped' && (
                      <button onClick={() => setStatus(m.id, 'skipped')} style={{ ...actionBtn, color: C.sub }}>{t.inbox_skip}</button>
                    )}
                  </div>
                  </div>
                </Card>
              );
            })}
          </div>
          <Pager
            page={safePage}
            total={total}
            size={pageSize}
            onPage={setPage}
            onSize={(s) => { setPageSize(s); setPage(1); }}
            sizeOptions={[10, 25, 50]}
          />
        </>
      )}
    </div>
  );
};
