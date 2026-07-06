import React from 'react';
import PropTypes from 'prop-types';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

const RADIUS = 9;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function thresholdColor(pct) {
  if (pct >= 90) return { stroke: 'stroke-red-500', text: 'text-red-600' };
  if (pct >= 70) return { stroke: 'stroke-amber-500', text: 'text-amber-600' };
  return { stroke: 'stroke-emerald-500', text: 'text-slate-400' };
}

/** Compact circular context-usage indicator; click opens a popup with the
 * context window, session token, and cached token breakdown that used to be
 * shown inline in the header. */
export default function ContextRing({ contextUsage, sessionTokens }) {
  const pct = Math.max(0, Math.min(100, contextUsage.pct));
  const offset = CIRCUMFERENCE * (1 - pct / 100);
  const { stroke, text } = thresholdColor(pct);
  const sessionTotal = sessionTokens.input + sessionTokens.output;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={`${contextUsage.used.toLocaleString()} / ${contextUsage.window.toLocaleString()} tokens this turn (${pct}%)`}
          className="hidden md:flex items-center justify-center size-7 shrink-0 rounded-full hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" className="-rotate-90">
            <circle
              cx="12" cy="12" r={RADIUS}
              className="stroke-slate-100 dark:stroke-white/10"
              strokeWidth="3" fill="none"
            />
            <circle
              cx="12" cy="12" r={RADIUS}
              className={cn(stroke, 'transition-all duration-500')}
              strokeWidth="3" fill="none"
              strokeDasharray={CIRCUMFERENCE}
              strokeDashoffset={offset}
              strokeLinecap="round"
            />
          </svg>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64 p-3">
        <div className="space-y-3">
          <div>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="font-semibold text-slate-600 dark:text-slate-300">Context window</span>
              <span className={cn('font-semibold tabular-nums', text)}>{pct}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-slate-100 dark:bg-white/10 overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full transition-all duration-500',
                  pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-500' : 'bg-emerald-500'
                )}
                style={{ width: `${Math.max(4, pct)}%` }}
              />
            </div>
            <div className="text-[11px] text-slate-400 mt-1 tabular-nums">
              {contextUsage.used.toLocaleString()} / {contextUsage.window.toLocaleString()} tokens this turn
            </div>
          </div>

          {sessionTotal > 0 && (
            <div className="text-xs space-y-1 pt-2 border-t border-black/[0.06] dark:border-white/10">
              <div className="flex items-center justify-between">
                <span className="text-slate-500 dark:text-slate-400">Session tokens</span>
                <span className="font-semibold tabular-nums text-slate-700 dark:text-slate-200">
                  {sessionTotal.toLocaleString()}
                </span>
              </div>
              <div className="flex items-center justify-between text-[11px] text-slate-400">
                <span>in / out</span>
                <span className="tabular-nums">
                  {sessionTokens.input.toLocaleString()} / {sessionTokens.output.toLocaleString()}
                </span>
              </div>
              {sessionTokens.cacheRead > 0 && (
                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <span>cached (read)</span>
                  <span className="tabular-nums">{sessionTokens.cacheRead.toLocaleString()}</span>
                </div>
              )}
              {sessionTokens.cacheCreation > 0 && (
                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <span>cached (write)</span>
                  <span className="tabular-nums">{sessionTokens.cacheCreation.toLocaleString()}</span>
                </div>
              )}
            </div>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

ContextRing.propTypes = {
  contextUsage: PropTypes.shape({
    window: PropTypes.number.isRequired,
    used: PropTypes.number.isRequired,
    pct: PropTypes.number.isRequired,
  }).isRequired,
  sessionTokens: PropTypes.shape({
    input: PropTypes.number.isRequired,
    output: PropTypes.number.isRequired,
    cacheRead: PropTypes.number.isRequired,
    cacheCreation: PropTypes.number.isRequired,
  }).isRequired,
};
