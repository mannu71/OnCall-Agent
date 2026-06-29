import React from 'react';
import PropTypes from 'prop-types';
import { cn } from '@/lib/utils';

/**
 * Run trace timeline + token metrics. Extracted from the old always-on right
 * column so it can live inside the collapsible Trace drawer. Pure presentational.
 */
export default function TraceTimeline({ traceSteps, tokens }) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-auto p-5 relative">
        <div className="relative flex flex-col gap-0">
          <div className="absolute left-[5px] top-2 bottom-2 w-0.5 bg-slate-100 dark:bg-white/10" />

          {traceSteps.length === 0 && (
            <div className="pl-8 text-[12px] text-slate-400 italic">
              No activity yet. Ask the agent a question to see its reasoning and tool calls here.
            </div>
          )}

          {traceSteps.map((s, i) => {
            let colorClasses = 'bg-slate-300 border-slate-200';
            let textColors = 'text-slate-500 bg-slate-50 border-slate-100 dark:bg-white/[0.04] dark:border-white/10';
            if (s.l === 'tool') {
              colorClasses = 'bg-blue-500 border-blue-200 shadow-[0_0_0_3px_rgba(59,130,246,0.1)]';
              textColors = 'text-blue-600 bg-blue-50/50 border-blue-100/40 dark:bg-blue-500/10 dark:border-blue-500/20';
            } else if (s.l === 'answer') {
              colorClasses = 'bg-rose-500 border-rose-200 shadow-[0_0_0_3px_rgba(244,63,94,0.1)]';
              textColors = 'text-rose-600 bg-rose-50/50 border-rose-100/40 dark:bg-rose-500/10 dark:border-rose-500/20';
            } else if (s.l === 'think') {
              colorClasses = 'bg-slate-400 border-slate-200';
              textColors = 'text-slate-500 bg-slate-50 border-slate-100/60 dark:bg-white/[0.04] dark:border-white/10';
            }
            return (
              <div key={i} className="relative pl-8 pb-6 last:pb-2 group chat-msg-enter">
                <span className={cn('absolute left-0 top-1.5 size-3 rounded-full border-2 border-white dark:border-[#1c1c1e] transition-all duration-300', colorClasses)} />
                <div className="flex justify-between items-baseline gap-2">
                  <span className={cn('font-bold text-[10px] uppercase tracking-wider', s.l === 'tool' ? 'text-blue-600' : s.l === 'answer' ? 'text-rose-600' : 'text-slate-500')}>{s.l}</span>
                  <span className={cn('font-mono text-[9px] px-1.5 py-0.5 rounded border', textColors)}>{s.t}</span>
                </div>
                <div className="font-sans text-[12.5px] leading-relaxed text-slate-700 dark:text-slate-200 mt-1 select-text">
                  {s.text}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Token metrics */}
      <div className="p-4 bg-black/[0.02] dark:bg-white/[0.03] border-t border-black/[0.06] dark:border-white/10 flex-shrink-0">
        <div className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mb-3">Token metrics</div>
        <div className="grid grid-cols-3 gap-2 text-center">
          {[
            { label: 'Input', value: tokens.input, cls: 'text-[#0a84ff]' },
            { label: 'Output', value: tokens.output, cls: 'text-emerald-500' },
            { label: 'Total', value: tokens.total, cls: 'text-slate-800 dark:text-slate-100' },
          ].map((m) => (
            <div key={m.label} className="bg-white dark:bg-white/[0.04] p-2.5 rounded-xl border border-black/[0.06] dark:border-white/10 flex flex-col justify-center">
              <div className="text-[9px] font-sans text-slate-400 uppercase font-semibold">{m.label}</div>
              <div className={cn('font-semibold text-[13px] font-mono tracking-tight mt-0.5 tabular-nums', m.cls)}>
                {(m.value || 0).toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

TraceTimeline.propTypes = {
  traceSteps: PropTypes.array.isRequired,
  tokens: PropTypes.object.isRequired,
};
