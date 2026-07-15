import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cleanLlmText } from './markdownUtils.js';

export { cleanLlmText };

const _codeBlockClass =
  "block bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/10 " +
  "text-slate-800 dark:text-slate-200 rounded p-3 text-xs font-mono overflow-auto whitespace-pre-wrap";

// A block of HTML the agent emitted is worth rendering, not just showing as
// source (e.g. the "Frontend Design" skill outputs a full self-contained slide
// deck). Detect an HTML code fence / document and offer a Preview⇄Code toggle;
// the preview runs in a sandboxed iframe (allow-scripts, NO allow-same-origin —
// self-contained decks with JS nav work, but the frame can't touch the app).
function looksLikeHtml(lang, text) {
  if (lang === 'html' || lang === 'xml') return true;
  const head = text.slice(0, 400).toLowerCase();
  return /<!doctype html|<html[\s>]|<body[\s>]|<div class="slide"|<section[\s>]/.test(head);
}

function CodeBlock({ className, children }) {
  const text = String(children ?? '');
  const lang = (className || '').match(/language-(\w+)/)?.[1] || '';
  const isHtml = looksLikeHtml(lang, text);
  const isFullDoc = /<!doctype html|<html[\s>]/i.test(text.slice(0, 400));
  // Full documents default to the rendered preview; HTML fragments default to
  // code (with a preview available) so short snippets in explanations stay code.
  const [view, setView] = React.useState(isFullDoc ? 'preview' : 'code');

  const codeEl = <code className={_codeBlockClass}>{children}</code>;
  if (!isHtml) return codeEl;

  const openInTab = () => {
    try {
      const blob = new Blob([text], { type: 'text/html' });
      window.open(URL.createObjectURL(blob), '_blank', 'noopener');
    } catch { /* no-op */ }
  };
  const tabBtn = (v, label) => (
    <button type="button" onClick={() => setView(v)}
      className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
        view === v
          ? 'bg-slate-200 dark:bg-white/15 text-slate-900 dark:text-slate-100'
          : 'text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200'}`}>
      {label}
    </button>
  );

  return (
    <div className="mb-3 rounded border border-slate-200 dark:border-white/10 overflow-hidden">
      <div className="flex items-center gap-1 px-2 py-1 bg-slate-100 dark:bg-white/[0.06] border-b border-slate-200 dark:border-white/10">
        {tabBtn('preview', 'Preview')}
        {tabBtn('code', 'Code')}
        <span className="ml-auto text-[10px] uppercase tracking-wide text-slate-400">HTML</span>
        <button type="button" onClick={openInTab}
          className="text-[11px] font-medium text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200">
          Open ↗
        </button>
      </div>
      {view === 'preview'
        ? <iframe title="HTML preview" srcDoc={text} sandbox="allow-scripts allow-popups"
            className="w-full bg-white" style={{ height: '70vh', border: 'none' }} />
        : <div className="p-0">{codeEl}</div>}
    </div>
  );
}

/**
 * Shared react-markdown component overrides — Tailwind-styled, no prose plugin.
 * Used by the Dashboard execution detail and the Agent Chat answers so reports,
 * tables, code blocks and the correlation timeline render consistently.
 */
export const mdComponents = {
  // eslint-disable-next-line no-unused-vars
  h1: ({node, ...p}) => <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100 mt-4 mb-2" {...p} />,
  // eslint-disable-next-line no-unused-vars
  h2: ({node, ...p}) => <h2 className="text-lg font-bold text-slate-800 dark:text-slate-100 mt-4 mb-2 border-b border-slate-200 dark:border-white/10 pb-1" {...p} />,
  // eslint-disable-next-line no-unused-vars
  h3: ({node, ...p}) => <h3 className="text-base font-semibold text-slate-800 dark:text-slate-200 mt-3 mb-1" {...p} />,
  // eslint-disable-next-line no-unused-vars
  p: ({node, ...p}) => <p className="text-sm text-slate-900 dark:text-slate-200 mb-3 leading-relaxed" {...p} />,
  // eslint-disable-next-line no-unused-vars
  strong: ({node, ...p}) => <strong className="font-semibold text-slate-900 dark:text-slate-100" {...p} />,
  // eslint-disable-next-line no-unused-vars
  em: ({node, ...p}) => <em className="italic text-slate-700 dark:text-slate-300" {...p} />,
  // eslint-disable-next-line no-unused-vars
  ul: ({node, ...p}) => <ul className="list-disc list-inside mb-3 space-y-1 text-sm text-slate-900 dark:text-slate-200" {...p} />,
  // eslint-disable-next-line no-unused-vars
  ol: ({node, ...p}) => <ol className="list-decimal list-inside mb-3 space-y-1 text-sm text-slate-900 dark:text-slate-200" {...p} />,
  // eslint-disable-next-line no-unused-vars
  li: ({node, ...p}) => <li className="text-sm text-slate-900 dark:text-slate-200 ml-2" {...p} />,
  // react-markdown v10 removed the `inline` prop — detect block vs inline via a
  // `language-*` class or multi-line content so inline code stays inline.
  // eslint-disable-next-line no-unused-vars
  code: ({node, inline, className, children, ...p}) => {
    const text = String(children ?? '');
    const isBlock = /language-/.test(className || '') || text.includes('\n');
    if (isBlock) return <CodeBlock className={className} {...p}>{children}</CodeBlock>;
    return <code className="bg-slate-100 dark:bg-white/10 text-slate-800 dark:text-slate-200 px-1 py-0.5 rounded text-xs font-mono" {...p}>{children}</code>;
  },
  // Render block code's wrapper as a div (not <pre>) so CodeBlock's preview
  // toolbar/iframe aren't nested inside a <pre> (invalid HTML). The inner
  // <code className="block …"> keeps all the monospace/whitespace styling.
  // eslint-disable-next-line no-unused-vars
  pre: ({node, children, ...p}) => <div className="mb-3" {...p}>{children}</div>,
  // eslint-disable-next-line no-unused-vars
  table: ({node, ...p}) => <div className="overflow-x-auto mb-4"><table className="min-w-full text-sm border-collapse border border-slate-200 dark:border-white/10" {...p} /></div>,
  // eslint-disable-next-line no-unused-vars
  thead: ({node, ...p}) => <thead className="bg-slate-100 dark:bg-white/[0.06]" {...p} />,
  // eslint-disable-next-line no-unused-vars
  th: ({node, ...p}) => <th className="px-3 py-2 text-left font-semibold text-slate-700 dark:text-slate-200 border border-slate-200 dark:border-white/10 text-xs" {...p} />,
  // eslint-disable-next-line no-unused-vars
  td: ({node, ...p}) => <td className="px-3 py-2 text-slate-900 dark:text-slate-200 border border-slate-200 dark:border-white/10 text-xs" {...p} />,
  // eslint-disable-next-line no-unused-vars
  tr: ({node, ...p}) => <tr className="even:bg-slate-50 dark:even:bg-white/[0.03]" {...p} />,
  // eslint-disable-next-line no-unused-vars
  blockquote: ({node, ...p}) => <blockquote className="border-l-4 border-blue-300 dark:border-blue-500/40 pl-4 italic text-slate-600 dark:text-slate-400 mb-3 text-sm" {...p} />,
  // eslint-disable-next-line no-unused-vars
  hr: ({node, ...p}) => <hr className="border-slate-200 dark:border-white/10 my-4" {...p} />,
};

/**
 * Render agent / report text as rich Markdown.
 * @param {{ content?: string, className?: string }} props
 */
export default function MarkdownMessage({ content, className }) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {cleanLlmText(content || '')}
      </ReactMarkdown>
    </div>
  );
}
