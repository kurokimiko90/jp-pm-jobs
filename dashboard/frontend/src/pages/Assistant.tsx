import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Robot, PaperPlaneRight, Sparkle, Notebook } from '@phosphor-icons/react';
import { get, post } from '../api';
import { C } from '../theme';
import { Empty, PageHeader, useIsNarrow } from '../components/ui';
import { useT } from '../i18n';

interface Turn {
  id: number;
  channel: string;
  question: string;
  answer: string;
  citations: string[];
  question_at: string;
  created_at: string;
}

const formatTs = (s: string): string => (s ? s.slice(5, 16) : '');

interface Finding {
  level: string;
  tag: string;
  text: string;
  job_id: number;
}

const levelColor = (level: string): string =>
  level === 'P0' ? C.errorRed : level === 'P1' ? C.amber : C.progressIndigo;

const jobIdFromCitation = (c: string): number | null => {
  const m = /^job:(\d+)$/.exec(c);
  return m ? Number(m[1]) : null;
};

export const Assistant: React.FC<{ openJob?: (id: number) => void }> = ({ openJob }) => {
  const t = useT();
  const isNarrow = useIsNarrow();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [summaryList, setSummaryList] = useState<string[]>([]);
  const [summaryName, setSummaryName] = useState<string | null>(null);
  const [summaryContent, setSummaryContent] = useState('');
  const threadScrollRef = useRef<HTMLDivElement>(null);

  const reload = () => {
    get('/api/assistant/history?limit=30').then(setTurns).catch(() => {});
    get('/api/assistant/findings').then(setFindings).catch(() => {});
  };

  useEffect(() => {
    reload();
    get('/api/assistant/summaries').then((list: string[]) => {
      setSummaryList(list);
      if (list.length > 0) setSummaryName(list[0]);
    }).catch(() => {});
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!summaryName) { setSummaryContent(''); return; }
    get(`/api/assistant/summary?name=${encodeURIComponent(summaryName)}`)
      .then((r: { content: string }) => setSummaryContent(r.content))
      .catch(() => setSummaryContent(''));
  }, [summaryName]);

  useEffect(() => {
    const el = threadScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const send = async () => {
    const q = question.trim();
    if (!q || sending) return;
    setSending(true);
    setQuestion('');
    try {
      await post('/api/assistant/chat', { question: q });
      reload();
    } catch {
      // 失敗時把問題還原到輸入框，讓使用者可以重試
      setQuestion(q);
    } finally {
      setSending(false);
    }
  };

  if (!loaded) return <Empty>{t.jobs_loading}</Empty>;

  return (
    <div style={{ animation: 'riseIn 420ms cubic-bezier(0.34,1.56,0.64,1) both' }}>
      <PageHeader title={t.assistant_title} subtitle={t.assistant_subtitle} />

      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? '1fr' : '1.6fr 1fr', gap: 18 }}>
        {/* 左：對話串 + composer */}
        <div style={{
          background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)',
          padding: '20px 22px', display: 'flex', flexDirection: 'column',
          minHeight: 480, maxHeight: isNarrow ? undefined : 640,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <span style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 34, height: 34, borderRadius: '50%', background: 'rgba(245,200,66,0.22)',
            }}>
              <Robot size={18} color={C.gold} weight="duotone" />
            </span>
            <div>
              <div style={{ fontSize: 14, fontWeight: 700 }}>{t.assistant_copilot_name}</div>
              <div style={{ fontSize: 12, color: 'rgba(90,82,72,0.55)' }}>{t.assistant_grounded_note}</div>
            </div>
          </div>

          <div ref={threadScrollRef} style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14, paddingRight: 4 }}>
            {turns.length === 0 && (
              <div style={{ fontSize: 13, color: 'rgba(90,82,72,0.5)', lineHeight: 1.7, padding: '20px 4px' }}>
                {t.assistant_empty_hint}
              </div>
            )}
            {turns.map((turn) => (
              <div key={turn.id} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ alignSelf: 'flex-end', fontSize: 11, color: 'rgba(90,82,72,0.45)', padding: '0 4px' }}>
                  {formatTs(turn.question_at)}
                </div>
                <div style={{
                  alignSelf: 'flex-end', maxWidth: '85%', background: 'rgba(245,200,66,0.18)',
                  borderRadius: '16px 16px 4px 16px', padding: '10px 14px', fontSize: 13.5,
                  lineHeight: 1.6, whiteSpace: 'pre-wrap', marginBottom: 4,
                }}>
                  {turn.question}
                </div>
                <div style={{
                  alignSelf: 'flex-start', maxWidth: '90%', background: C.card,
                  borderRadius: '16px 16px 16px 4px', padding: '12px 16px', fontSize: 13.5,
                  lineHeight: 1.75, whiteSpace: 'pre-wrap', boxShadow: '0 4px 16px rgba(90,82,72,0.08)',
                }}>
                  {turn.answer}
                  {turn.citations.length > 0 && (
                    <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {turn.citations.map((c) => {
                        const jobId = jobIdFromCitation(c);
                        return (
                          <span
                            key={c}
                            onClick={() => jobId != null && openJob?.(jobId)}
                            style={{
                              fontSize: 11, fontWeight: 600, letterSpacing: 0.5,
                              color: C.amber, background: 'rgba(245,200,66,0.14)',
                              borderRadius: 999, padding: '3px 10px',
                              cursor: jobId != null && openJob ? 'pointer' : 'default',
                            }}
                          >
                            {c}
                          </span>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div style={{ alignSelf: 'flex-start', fontSize: 11, color: 'rgba(90,82,72,0.45)', padding: '0 4px' }}>
                  {formatTs(turn.created_at)}
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 10, marginTop: 14, alignItems: 'center' }}>
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder={t.assistant_composer_placeholder}
              disabled={sending}
              style={{
                flex: 1, height: 46, border: 'none', outline: 'none', borderRadius: 999,
                background: 'rgba(232,213,183,0.25)', padding: '0 20px', fontSize: 14,
                fontWeight: 500, color: C.ink,
              }}
            />
            <button
              onClick={send}
              disabled={sending || !question.trim()}
              aria-label={t.assistant_send}
              style={{
                flexShrink: 0, width: 46, height: 46, borderRadius: '50%', border: 'none',
                background: C.gold, color: C.ink, display: 'flex', alignItems: 'center',
                justifyContent: 'center', cursor: sending ? 'default' : 'pointer',
                opacity: sending || !question.trim() ? 0.6 : 1,
              }}
            >
              <PaperPlaneRight size={18} weight="fill" />
            </button>
          </div>
          <div style={{ fontSize: 11, color: 'rgba(90,82,72,0.45)', marginTop: 8, textAlign: 'center' }}>
            {t.assistant_scope_note}
          </div>
        </div>

        {/* 右：AI 目前發現 + 引用範圍 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '20px 22px' }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)', marginBottom: 14 }}>
              {t.assistant_findings_heading}
            </div>
            {findings.length === 0 ? (
              <div style={{ fontSize: 13, color: 'rgba(90,82,72,0.5)' }}>{t.assistant_findings_empty}</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {findings.map((f, i) => (
                  <button
                    key={i}
                    onClick={() => openJob?.(f.job_id)}
                    style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10, textAlign: 'left',
                      background: C.card, border: 'none', borderRadius: 14, padding: '10px 12px',
                      cursor: openJob ? 'pointer' : 'default',
                    }}
                  >
                    <span style={{
                      flexShrink: 0, fontSize: 10, fontWeight: 800, letterSpacing: 0.5,
                      color: '#fff', background: levelColor(f.level), borderRadius: 6,
                      padding: '2px 6px', marginTop: 2,
                    }}>
                      {f.level}
                    </span>
                    <span style={{ fontSize: 13, lineHeight: 1.5 }}>
                      <strong>{f.tag}</strong>
                      <br />
                      <span style={{ color: 'rgba(90,82,72,0.7)' }}>{f.text}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {summaryList.length > 0 && (
            <div style={{ background: C.bg, borderRadius: 26, boxShadow: '0 10px 40px rgba(90,82,72,0.13)', padding: '20px 22px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Notebook size={15} color={C.amber} weight="fill" />
                  <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 2, color: 'rgba(90,82,72,0.55)' }}>
                    {t.assistant_summary_heading}
                  </div>
                </div>
                {summaryList.length > 1 && (
                  <select
                    value={summaryName ?? ''}
                    onChange={(e) => setSummaryName(e.target.value)}
                    style={{
                      fontSize: 12, border: 'none', borderRadius: 8, background: 'rgba(232,213,183,0.3)',
                      padding: '4px 8px', color: C.ink,
                    }}
                  >
                    {summaryList.map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                )}
              </div>
              <div className="md-body" style={{ fontSize: 12.5, lineHeight: 1.75, maxHeight: 420, overflowY: 'auto', color: 'rgba(90,82,72,0.8)' }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{summaryContent}</ReactMarkdown>
              </div>
            </div>
          )}

          <div style={{ background: 'rgba(232,213,183,0.2)', borderRadius: 26, padding: '20px 22px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <Sparkle size={15} color={C.amber} weight="fill" />
              <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1, color: 'rgba(90,82,72,0.6)' }}>
                {t.assistant_rule_heading}
              </div>
            </div>
            <div style={{ fontSize: 12.5, lineHeight: 1.7, color: 'rgba(90,82,72,0.65)' }}>
              {t.assistant_rule_body}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
