import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Strip the Python-repr content-block artefact from older LLM output, e.g.
 *   [{'type': 'text', 'text': "actual answer...", 'index': 0}]
 * (keys single-quoted, value may be double-quoted — so targeted regexes).
 * @param {string} text
 * @returns {string}
 */
export function cleanLlmText(text) {
  if (!text || typeof text !== 'string') return text || '';
  const trimmed = text.trimStart();
  if (!trimmed.startsWith('[{')) return text;

  const dq = [...text.matchAll(/'text':\s*"([\s\S]*?)"\s*[,}]/g)];
  if (dq.length) return dq.map(m => m[1]).join('').replace(/\\n/g, '\n').replace(/\\'/g, "'").replace(/\\"/g, '"');

  const sq = [...text.matchAll(/'text':\s*'([\s\S]*?)'\s*[,}]/g)];
  if (sq.length) return sq.map(m => m[1]).join('').replace(/\\n/g, '\n');

  try {
    const asJson = text
      .replace(/'/g, '"')
      .replace(/\bNone\b/g, 'null')
      .replace(/\bTrue\b/g, 'true')
      .replace(/\bFalse\b/g, 'false');
    const blocks = JSON.parse(asJson);
    if (Array.isArray(blocks)) {
      const parts = blocks.filter(b => b.type === 'text').map(b => b.text || '');
      if (parts.length) return parts.join('');
    }
  } catch { /* ignore */ }

  return text;
}

/**
 * Shared react-markdown component overrides — Tailwind-styled, no prose plugin.
 * Used by the Dashboard execution detail and the Agent Chat answers so reports,
 * tables, code blocks and the correlation timeline render consistently.
 */
export const mdComponents = {
  // eslint-disable-next-line no-unused-vars
  h1: ({node, ...p}) => <h1 className="text-xl font-bold text-slate-900 mt-4 mb-2" {...p} />,
  // eslint-disable-next-line no-unused-vars
  h2: ({node, ...p}) => <h2 className="text-lg font-bold text-slate-800 mt-4 mb-2 border-b border-slate-200 pb-1" {...p} />,
  // eslint-disable-next-line no-unused-vars
  h3: ({node, ...p}) => <h3 className="text-base font-semibold text-slate-800 mt-3 mb-1" {...p} />,
  // eslint-disable-next-line no-unused-vars
  p: ({node, ...p}) => <p className="text-sm text-slate-900 mb-3 leading-relaxed" {...p} />,
  // eslint-disable-next-line no-unused-vars
  strong: ({node, ...p}) => <strong className="font-semibold text-slate-900" {...p} />,
  // eslint-disable-next-line no-unused-vars
  em: ({node, ...p}) => <em className="italic text-slate-700" {...p} />,
  // eslint-disable-next-line no-unused-vars
  ul: ({node, ...p}) => <ul className="list-disc list-inside mb-3 space-y-1 text-sm text-slate-900" {...p} />,
  // eslint-disable-next-line no-unused-vars
  ol: ({node, ...p}) => <ol className="list-decimal list-inside mb-3 space-y-1 text-sm text-slate-900" {...p} />,
  // eslint-disable-next-line no-unused-vars
  li: ({node, ...p}) => <li className="text-sm text-slate-900 ml-2" {...p} />,
  // react-markdown v10 removed the `inline` prop — detect block vs inline via a
  // `language-*` class or multi-line content so inline code stays inline.
  // eslint-disable-next-line no-unused-vars
  code: ({node, inline, className, children, ...p}) => {
    const text = String(children ?? '');
    const isBlock = /language-/.test(className || '') || text.includes('\n');
    return isBlock
      ? <code className="block bg-slate-50 border border-slate-200 rounded p-3 text-xs font-mono overflow-auto whitespace-pre-wrap" {...p}>{children}</code>
      : <code className="bg-slate-100 text-slate-800 px-1 py-0.5 rounded text-xs font-mono" {...p}>{children}</code>;
  },
  // eslint-disable-next-line no-unused-vars
  pre: ({node, ...p}) => <pre className="mb-3" {...p} />,
  // eslint-disable-next-line no-unused-vars
  table: ({node, ...p}) => <div className="overflow-x-auto mb-4"><table className="min-w-full text-sm border-collapse border border-slate-200" {...p} /></div>,
  // eslint-disable-next-line no-unused-vars
  thead: ({node, ...p}) => <thead className="bg-slate-100" {...p} />,
  // eslint-disable-next-line no-unused-vars
  th: ({node, ...p}) => <th className="px-3 py-2 text-left font-semibold text-slate-700 border border-slate-200 text-xs" {...p} />,
  // eslint-disable-next-line no-unused-vars
  td: ({node, ...p}) => <td className="px-3 py-2 text-slate-900 border border-slate-200 text-xs" {...p} />,
  // eslint-disable-next-line no-unused-vars
  tr: ({node, ...p}) => <tr className="even:bg-slate-50" {...p} />,
  // eslint-disable-next-line no-unused-vars
  blockquote: ({node, ...p}) => <blockquote className="border-l-4 border-blue-300 pl-4 italic text-slate-600 mb-3 text-sm" {...p} />,
  // eslint-disable-next-line no-unused-vars
  hr: ({node, ...p}) => <hr className="border-slate-200 my-4" {...p} />,
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
