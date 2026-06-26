import React, { memo, lazy, Suspense } from 'react';
import {
  Loader2,
  XCircle,
  Brain,
  Wrench,
  Terminal,
  Info,
  Copy,
  Check,
  CornerDownLeft,
  RotateCcw,
  ListTree,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import PlanChecklist from '../markdown/PlanChecklist.jsx';
import PrivacyInsightsPanel from '../privacy/PrivacyInsightsPanel.jsx';
import AgentThread from './AgentThread.jsx';
import CollapsibleSection from './CollapsibleSection.jsx';
import { formatClock } from '../../lib/formatTime.js';

const MarkdownMessage = lazy(() => import('../markdown/MarkdownMessage.jsx'));

export const MESSAGE_TYPES = {
  USER: 'user',
  AGENT: 'agent',
  SYSTEM: 'system',
};

function FormattedText({ text, isUser }) {
  if (!text) return null;
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_)/);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return (
            <strong
              key={index}
              className={isUser ? 'text-primary-foreground font-bold' : 'text-primary font-bold'}
            >
              {part.slice(2, -2)}
            </strong>
          );
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return (
            <code
              key={index}
              className={`font-mono text-xs px-1.5 py-0.5 rounded border transition-colors ${
                isUser
                  ? 'bg-slate-800 text-slate-200 border-slate-700'
                  : 'bg-slate-50 text-slate-800 border-slate-200'
              }`}
            >
              {part.slice(1, -1)}
            </code>
          );
        }
        if (part.startsWith('_') && part.endsWith('_')) {
          return <em key={index} className="italic font-medium">{part.slice(1, -1)}</em>;
        }
        return part;
      })}
    </>
  );
}

const ChatMessage = memo(function ChatMessage({
  message,
  copiedId,
  isLoading,
  onCopy,
  onEdit,
  onRegenerate,
}) {
  const isUser = message.type === MESSAGE_TYPES.USER;
  const isSystem = message.type === MESSAGE_TYPES.SYSTEM;
  const textContent = message.content || message.text || '';

  if (isSystem) {
    return (
      <div className="flex justify-center my-4 animate-in fade-in duration-300">
        <div className="bg-slate-100/80 dark:bg-white/[0.06] backdrop-blur-sm border border-slate-200/50 dark:border-white/10 rounded-full px-4 py-1.5 max-w-xl text-center shadow-sm flex items-center gap-2">
          <Info className="size-3.5 text-slate-500 dark:text-slate-400" />
          <span className="text-slate-600 dark:text-slate-300 font-sans text-xs leading-none">
            <FormattedText text={textContent} isUser={false} />
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`group/msg flex w-full ${isUser ? 'justify-end' : 'justify-start'} chat-msg-enter`}
      style={{ marginBottom: 'var(--msg-gap)' }}
    >
      <div className={`flex flex-col items-${isUser ? 'end' : 'start'} max-w-[85%]`}>
        <div className="font-sans text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 pl-1 pr-1 flex items-center gap-2">
          <span>{isUser ? 'You' : 'Agent'} · {formatClock(message.timestamp)}</span>
          {isUser && message.hasTraceId && (
            <span className="normal-case tracking-normal text-[9px] font-semibold text-sky-700 bg-sky-50 border border-sky-100 rounded px-1 leading-4">🔎 correlation lookup</span>
          )}
        </div>

        <div
          style={{ padding: 'var(--msg-pad)' }}
          className={`w-full transition-all duration-300 leading-relaxed font-sans text-[13.5px] select-text ${
            isUser
              ? 'bg-primary text-primary-foreground rounded-xl rounded-tr-sm shadow-sm shadow-primary/35'
              : 'bg-white text-slate-900 border border-black/[0.07] rounded-xl rounded-tl-sm shadow-[0_1px_2px_rgb(0_0_0/0.06)] dark:bg-[#1c1c1e] dark:text-slate-100 dark:border-white/10 dark:shadow-none'
          }`}
        >
          {!isUser && message.steps && message.steps.length > 0 && (
            <AgentThread steps={message.steps} live={message.isLoading} />
          )}

          {!isUser && message.isMarkdown && textContent && (
            <PlanChecklist content={textContent} />
          )}

          {textContent && (
            <div className="break-words">
              {(!isUser && message.isMarkdown)
                ? (
                  <Suspense fallback={<div className="text-slate-400 text-xs">Loading…</div>}>
                    <MarkdownMessage content={textContent} />
                  </Suspense>
                )
                : <div className="whitespace-pre-wrap"><FormattedText text={textContent} isUser={isUser} /></div>}
              {message.streaming && (
                <span className="inline-block w-[2px] h-[1em] ml-0.5 -mb-0.5 bg-primary/70 animate-pulse rounded-sm align-middle" />
              )}
            </div>
          )}

          {!isUser && ((message.privacyRedactions?.length > 0) || (message.selectedSkills?.length > 0)) && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {message.privacyRedactions?.length > 0 && (
                <PrivacyInsightsPanel redactions={message.privacyRedactions} />
              )}
              {(message.selectedSkills || []).map((name) => (
                <span
                  key={name}
                  title="Skill auto-selected for this query"
                  className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-300"
                >
                  <Wrench className="h-3 w-3" />
                  {name}
                </span>
              ))}
            </div>
          )}

          {Array.isArray(message.todos) && message.todos.length > 0 && (
            <div className="mt-3.5 rounded-xl border border-emerald-200/70 bg-emerald-50/40 dark:border-emerald-500/25 dark:bg-emerald-500/[0.07] p-3 text-[12px]">
              <div className="flex items-center gap-1.5 mb-2 font-semibold text-emerald-700 dark:text-emerald-300 uppercase tracking-wide text-[10px]">
                <Terminal className="size-3.5" /> Plan
              </div>
              <ul className="space-y-1">
                {message.todos.map((t, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span>{t.status === 'completed' ? '✅' : t.status === 'in_progress' ? '🔄' : t.status === 'blocked' ? '⛔' : '⬜'}</span>
                    <span className={t.status === 'completed' ? 'line-through opacity-70' : ''}>{t.text}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {message.structured && (
            <div className="mt-3.5 rounded-xl border border-indigo-200/70 bg-indigo-50/40 dark:border-indigo-500/25 dark:bg-indigo-500/[0.07] p-3 text-[12px]">
              <div className="flex items-center gap-1.5 mb-2 font-semibold text-indigo-700 dark:text-indigo-300 uppercase tracking-wide text-[10px]">
                <Terminal className="size-3.5" /> Structured report
                {message.structured.severity && (
                  <span className="ml-1 px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200 normal-case tracking-normal">{message.structured.severity}</span>
                )}
                {typeof message.structured.confidence === 'number' && (
                  <span className="ml-auto text-indigo-400 dark:text-indigo-400/80 normal-case tracking-normal">conf {Math.round(message.structured.confidence * 100)}%</span>
                )}
              </div>
              {message.structured.root_cause && (
                <div className="mb-2"><span className="font-semibold text-slate-600 dark:text-slate-300">Root cause:</span> <span className="text-slate-700 dark:text-slate-200">{message.structured.root_cause}</span></div>
              )}
              {Array.isArray(message.structured.evidence) && message.structured.evidence.length > 0 && (
                <div className="mb-2">
                  <div className="font-semibold text-slate-600 dark:text-slate-300 mb-1">Evidence</div>
                  <ul className="space-y-0.5">
                    {message.structured.evidence.map((ev, i) => (
                      <li key={i} className="font-mono text-[11px] text-slate-600 dark:text-slate-400">
                        {ev.file}{ev.line ? `:${ev.line}` : ''}{ev.symbol ? ` — ${ev.symbol}` : ''}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {Array.isArray(message.structured.next_steps) && message.structured.next_steps.length > 0 && (
                <div>
                  <div className="font-semibold text-slate-600 dark:text-slate-300 mb-1">Next steps</div>
                  <ul className="list-disc list-inside text-slate-600 dark:text-slate-300 space-y-0.5">
                    {message.structured.next_steps.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}

          {message.tools && message.tools.length > 0 && (
            <div className="mt-3.5 flex gap-2 flex-wrap">
              {message.tools.map(t => (
                <span
                  key={t.name}
                  className="inline-flex items-center gap-1.5 font-mono text-[10.5px] font-medium text-slate-600 bg-slate-50 border border-slate-200/60 px-2 py-0.5 rounded-lg hover:bg-slate-100/80 hover:text-slate-800 transition-colors shadow-sm cursor-help select-all"
                  title={`Tool run: ${t.name}`}
                >
                  <Terminal className="size-3.5 text-slate-400" />
                  {t.name}
                  <span className="text-slate-300">·</span>
                  <span className="text-slate-400 font-normal">{t.t || t.duration || '0s'}</span>
                </span>
              ))}
            </div>
          )}

          {message.citations && message.citations.length > 0 && (
            <CollapsibleSection icon={ListTree} title="Sources" accent="blue" count={message.citations.length}>
              <div className="flex flex-col gap-1">
                {message.citations.map((c, idx) => (
                  <div key={c} className="flex items-center gap-2 text-[11px]">
                    <span className="shrink-0 size-4 rounded-full bg-slate-100 dark:bg-white/10 text-slate-500 dark:text-slate-300 text-[9px] font-semibold flex items-center justify-center">{idx + 1}</span>
                    <span className="font-mono text-[10.5px] text-slate-500 dark:text-slate-400 break-all select-all">{c}</span>
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          )}

          {message.isLoading && (
            <div className="mt-3 animate-in fade-in duration-300">
              {message.statusHistory && message.statusHistory.length > 0 && (
                <CollapsibleSection
                  icon={Brain}
                  title="Reasoning"
                  accent="primary"
                  defaultOpen
                  count={message.statusHistory.length}
                >
                  <div className="max-h-36 overflow-y-auto space-y-1.5 pr-1">
                    {message.statusHistory.map((status, idx) => (
                      <div
                        key={idx}
                        className={`text-xs pl-2.5 border-l-2 py-0.5 font-sans leading-relaxed ${
                          status.type === 'tool'
                            ? 'border-blue-500 text-blue-600 dark:text-blue-400 bg-blue-50/20 dark:bg-blue-500/[0.06]'
                            : status.type === 'thinking'
                            ? 'border-primary text-primary bg-red-50/20 dark:bg-primary/[0.08]'
                            : status.type === 'error'
                            ? 'border-red-500 text-red-600 bg-red-50/20 dark:bg-red-500/[0.06]'
                            : 'border-slate-400 text-slate-600 dark:text-slate-300'
                        }`}
                      >
                        <span className="opacity-60 mr-1.5 font-mono text-[10px]">{status.time}</span>
                        {status.message}
                      </div>
                    ))}
                  </div>
                </CollapsibleSection>
              )}
              {!message.streaming && !(message.steps && message.steps.length > 0) && (
                <div className="mt-2 flex items-center gap-2 bg-slate-50 dark:bg-white/[0.04] border border-slate-100 dark:border-white/10 rounded-lg p-2.5">
                  <Loader2 className="size-4 animate-spin text-primary" />
                  <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">
                    {message.currentStatus?.message || 'Executing agent workflows'}
                    <span className="chat-thinking-dots" />
                  </span>
                </div>
              )}
            </div>
          )}

          {message.isError && (
            <Badge variant="destructive" className="mt-3.5 px-2.5 py-0.5 rounded-full flex items-center gap-1.5 w-fit font-sans text-xs">
              <XCircle className="w-3.5 h-3.5" />
              Workflow Terminated
            </Badge>
          )}
        </div>

        {!message.isLoading && (message.content || message.text) && (
          <div className={cn(
            'mt-1 flex items-center gap-0.5 opacity-0 group-hover/msg:opacity-100 transition-opacity',
            isUser ? 'pr-1' : 'pl-1'
          )}>
            <button
              type="button"
              onClick={() => onCopy(message)}
              title="Copy"
              className="size-6 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-600 hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
            >
              {copiedId === message.id ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
            </button>
            {isUser ? (
              <button
                type="button"
                onClick={() => onEdit(message)}
                title="Edit & resend"
                className="size-6 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-600 hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
              >
                <CornerDownLeft className="size-3.5" />
              </button>
            ) : (
              <button
                type="button"
                onClick={onRegenerate}
                disabled={isLoading}
                title="Regenerate"
                className="size-6 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-600 hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors disabled:opacity-40"
              >
                <RotateCcw className="size-3.5" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
});

export default ChatMessage;
