import { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { type Room3DText } from './ui';

interface ReaderPanelProps {
  ui: Room3DText;
  title: string;
  source: string | null;
  body: string;
  onClose: () => void;
}

/** 展示物の全文（3D の板面は要約だけ — 長文は 2D で読む） */
export function ReaderPanel({ ui, title, source, body, onClose }: ReaderPanelProps) {
  useEscape(onClose);
  return (
    <div className="r3d-panel" role="dialog" aria-label={title}>
      <div className="r3d-panel-head">
        <div className="grow">
          <h2>{title}</h2>
          {source && <small>{ui.source}: {source}</small>}
        </div>
        <button onClick={onClose}>{ui.close} ✕</button>
      </div>
      <div className="r3d-panel-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{body || ui.empty}</ReactMarkdown>
      </div>
    </div>
  );
}

interface SlidePanelProps {
  ui: Room3DText;
  slides: readonly string[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
}

/** 投影スライドの拡大表示（3D スクリーンと同じページを連動） */
export function SlidePanel({ ui, slides, index, onIndex, onClose }: SlidePanelProps) {
  useEscape(onClose);
  const name = slides[index]?.split('/').pop() ?? '';
  return (
    <div className="r3d-panel" role="dialog" aria-label={ui.slides}>
      <div className="r3d-panel-head">
        <div className="grow">
          <h2>{ui.slides} {index + 1} / {slides.length}</h2>
          <small>{decodeURIComponent(name)}</small>
        </div>
        <button onClick={onClose}>{ui.close} ✕</button>
      </div>
      <div className="r3d-slide">
        <img src={slides[index]} alt={name} />
      </div>
      <div className="r3d-slide-nav">
        <button className="r3d-btn" disabled={index === 0} onClick={() => onIndex(index - 1)}>←</button>
        <button className="r3d-btn" disabled={index >= slides.length - 1} onClick={() => onIndex(index + 1)}>→</button>
      </div>
    </div>
  );
}

function useEscape(fn: () => void): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') fn(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [fn]);
}
