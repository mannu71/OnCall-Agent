import React, { useState } from 'react';
import PropTypes from 'prop-types';
import { ChevronRight, Terminal, Loader2, CheckCircle2, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

function safeJson(v) {
  try {
    return typeof v === 'string' ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/**
 * Compact a model identifier for the timeline chip. Bedrock ids arrive as a
 * cross-region inference profile (e.g. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`);
 * strip the region + provider prefixes and the trailing date/version so the
 * chip reads `claude-sonnet-4-5`. A per-def llm_config NAME (what an explicit
 * subagent model resolves to) has none of those markers, so it passes through
 * essentially unchanged.
 */
function shortModel(m) {
  if (!m) return '';
  let s = String(m);
  s = s.replace(/^(us|eu|ap|apac)\./i, '');   // region inference-profile prefix
  s = s.replace(/^[a-z0-9-]+\./i, '');          // provider prefix (anthropic., amazon., …)
  s = s.replace(/-\d{8}-v\d+(?::\d+)?$/i, '');   // trailing -YYYYMMDD-vN[:N]
  s = s.replace(/-v\d+(?::\d+)?$/i, '');          // or bare -vN[:N]
  return s || String(m);
}

/** One node on the timeline — collapsible tool call with args + result. */
function ThreadNode({ step }) {
  const [open, setOpen] = useState(false);
  const status = step.status || 'running';
  const dur = step.durationMs != null ? `${(step.durationMs / 1000).toFixed(1)}s` : null;
  const hasArgs = step.args && typeof step.args === 'object' && Object.keys(step.args).length > 0;
  const hasResult = step.result != null && step.result !== '';

  const dot =
    status === 'done'
      ? 'bg-emerald-500 border-emerald-200 dark:border-emerald-500/30'
      : status === 'error'
        ? 'bg-red-500 border-red-200 dark:border-red-500/30'
        : 'bg-primary border-primary/30 chat-thread-pulse';

  return (
    <div className="relative pl-6 pb-3 last:pb-0">
      {/* node dot */}
      <span
        className={cn('absolute left-0 top-1 size-3 rounded-full border-2 border-white dark:border-[#1c1c1e] transition-all', dot)}
      />
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 text-left group/node"
      >
        <ChevronRight
          className={cn('size-3 text-slate-400 shrink-0 transition-transform duration-200', open && 'rotate-90')}
        />
        <Terminal className="size-3 text-slate-400 shrink-0" />
        <span className="font-mono text-[11px] font-semibold text-slate-700 dark:text-slate-200 truncate">
          {step.name}
        </span>
        {(() => {
          const origin = step.agent || 'agent';
          const isMain = origin === 'agent';
          const model = step.model || '';
          const who = isMain ? 'Run by the main agent' : `Run by subagent: ${origin}`;
          return (
            <span
              className={cn(
                'shrink-0 rounded-full px-1.5 py-px text-[9px] font-semibold tracking-wide',
                isMain
                  ? 'bg-slate-100 text-slate-500 dark:bg-white/10 dark:text-slate-400'
                  : 'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300',
              )}
              title={model ? `${who} · ${model}` : who}
            >
              {isMain ? 'agent' : origin}
            </span>
          );
        })()}
        {step.model && (
          <span
            className="shrink-0 max-w-[150px] truncate inline-flex items-center gap-1 rounded px-1 py-px text-[9px] font-mono text-slate-500 dark:text-slate-400 bg-slate-100/70 dark:bg-white/[0.06]"
            title={`Model: ${step.model}`}
          >
            <span className="opacity-50">◆</span>
            {shortModel(step.model)}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5 shrink-0">
          {dur && <span className="text-[9.5px] text-slate-400 font-mono">{dur}</span>}
          {status === 'running' && <Loader2 className="size-3 text-primary animate-spin" />}
          {status === 'done' && <CheckCircle2 className="size-3 text-emerald-500" />}
          {status === 'error' && <XCircle className="size-3 text-red-500" />}
        </span>
      </button>
      {open && (hasArgs || hasResult) && (
        <div className="mt-1 space-y-1">
          {hasArgs && (
            <div>
              <div className="text-[8.5px] font-semibold uppercase tracking-wider text-slate-400 mb-0.5">Arguments</div>
              <pre className="text-[10px] font-mono text-slate-600 dark:text-slate-300 bg-white dark:bg-black/30 border border-slate-200/70 dark:border-white/10 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-all">{safeJson(step.args)}</pre>
            </div>
          )}
          {hasResult && (
            <div>
              <div className="text-[8.5px] font-semibold uppercase tracking-wider text-slate-400 mb-0.5">Result</div>
              <pre className="text-[10px] font-mono text-slate-600 dark:text-slate-300 bg-white dark:bg-black/30 border border-slate-200/70 dark:border-white/10 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-all max-h-48 overflow-y-auto">{safeJson(step.result)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

ThreadNode.propTypes = { step: PropTypes.object.isRequired };

/**
 * Inline "agent thread" timeline — a vertical line with a node dot per tool
 * call, replacing the flat stack of step cards. While the run is live, a synapse
 * pulse travels down the line. Design borrowed from the reference chat UI; code
 * is our own.
 */
export default function AgentThread({ steps, live = false }) {
  if (!steps || steps.length === 0) return null;
  return (
    <div className="relative mb-3 mt-0.5">
      {/* vertical line */}
      <div className="absolute left-[5px] top-1.5 bottom-1.5 w-px bg-slate-200 dark:bg-white/10" />
      {/* travelling synapse pulse (live only) */}
      {live && (
        <div className="absolute left-[3px] w-[5px] h-[5px] rounded-full bg-primary chat-synapse" />
      )}
      <div className="relative">
        {steps.map((s) => (
          <ThreadNode key={s.id} step={s} />
        ))}
      </div>
    </div>
  );
}

AgentThread.propTypes = {
  steps: PropTypes.array,
  live: PropTypes.bool,
};
