import React from 'react';
import PropTypes from 'prop-types';
import { Send, Square } from 'lucide-react';
import { cn } from '@/lib/utils';
import AgentOrb from './AgentOrb';

const BASE = 'h-8 px-4 rounded-xl text-xs font-semibold inline-flex items-center justify-center'
  + ' cursor-pointer transition-colors disabled:cursor-not-allowed';

/**
 * The composer's single action control.
 *
 * Idle it sends; while a run is live it *is* the stop button — the orb and the
 * label say what the agent is doing, and hovering swaps them for the stop
 * affordance. A separate Stop button beside it was a second control for the
 * same subject: once a run is in flight, stopping it is the only thing left to
 * do here, so there is nothing for two buttons to disambiguate.
 *
 * Phase is derived by the caller from run state:
 *   - idle:        paper-plane, springs on click (launch)
 *   - processing:  orb working (run started, before tokens stream)
 *   - receiving:   orb composing (answer streaming back)
 *
 * The orb's theme is pinned to `dark` — it has to contrast with the filled
 * primary button it sits on, not with the page, and `dark` is the light-ink
 * palette.
 */
export default function SendButton({
  onClick, onStop, disabled, phase = 'idle', launching = false,
  stopping = false, stale = false,
}) {
  const running = phase === 'processing' || phase === 'receiving';

  if (!running) {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label="Send"
        className={cn(
          BASE, 'gap-1.5 min-w-[92px]',
          'bg-primary hover:bg-primary/90 text-primary-foreground shadow-sm shadow-primary/20',
          'disabled:opacity-50',
        )}
      >
        <Send className={cn('size-4', launching && 'chat-send-launch')} />
        Send
      </button>
    );
  }

  if (stopping) {
    return (
      <button
        type="button"
        disabled
        aria-label="Stopping agent"
        className={cn(BASE, 'gap-1.5 min-w-[92px] border bg-red-50 text-red-600 border-red-200 opacity-70')}
      >
        <Square className="size-3 fill-current" />
        Stopping…
      </button>
    );
  }

  // Watchdog: no activity for 90s+. Stopping is the recommended action, so it
  // is stated outright rather than hidden behind a hover.
  if (stale) {
    return (
      <button
        type="button"
        onClick={onStop}
        aria-label="Force stop agent"
        title="Force stop (no response in 90s+)"
        className={cn(
          BASE, 'gap-1.5 min-w-[92px] border animate-pulse',
          'bg-amber-50 text-amber-700 border-amber-300 hover:bg-amber-100',
        )}
      >
        <Square className="size-3 fill-current" />
        Force stop
      </button>
    );
  }

  const label = phase === 'receiving' ? 'Streaming' : 'Working';
  return (
    <button
      type="button"
      onClick={onStop}
      aria-label="Stop agent"
      title={`${label} — click to stop`}
      className={cn(
        BASE, 'group min-w-[92px] border border-transparent',
        'bg-primary text-primary-foreground shadow-sm shadow-primary/20',
        'hover:bg-red-50 hover:text-red-600 hover:border-red-200 hover:shadow-none',
      )}
    >
      <span className="inline-flex items-center gap-1.5 group-hover:hidden">
        <AgentOrb
          state={phase === 'receiving' ? 'composing' : 'working'}
          size={20}
          scale={0.8}
          theme="dark"
          label={label}
        />
        {label}
      </span>
      <span className="hidden items-center gap-1.5 group-hover:inline-flex">
        <Square className="size-3 fill-current" />
        Stop
      </span>
    </button>
  );
}

SendButton.propTypes = {
  onClick: PropTypes.func.isRequired,
  onStop: PropTypes.func,
  disabled: PropTypes.bool,
  phase: PropTypes.oneOf(['idle', 'processing', 'receiving']),
  launching: PropTypes.bool,
  stopping: PropTypes.bool,
  stale: PropTypes.bool,
};
