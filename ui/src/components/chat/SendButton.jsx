import React from 'react';
import PropTypes from 'prop-types';
import { Send, Siren, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Composer send button with reference-inspired states:
 *   - idle:        paper-plane, springs on click (launch)
 *   - processing:  siren icon pulsing (run started, before tokens stream)
 *   - receiving:   quarter-turn spinner (answer streaming back)
 * Phase is derived by the caller from run state.
 */
export default function SendButton({ onClick, disabled, phase = 'idle', launching = false }) {
  const streaming = phase === 'processing' || phase === 'receiving';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Send"
      className={cn(
        'h-8 px-4 rounded-xl text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer transition-colors',
        'bg-primary hover:bg-primary/90 text-primary-foreground shadow-sm shadow-primary/20',
        'disabled:opacity-50 disabled:cursor-not-allowed',
      )}
    >
      {phase === 'processing' ? (
        <Siren className="size-4 chat-siren" />
      ) : phase === 'receiving' ? (
        <Loader2 className="size-4 chat-quarter-turn" />
      ) : (
        <Send className={cn('size-4', launching && 'chat-send-launch')} />
      )}
      {streaming ? (phase === 'receiving' ? 'Streaming' : 'Working') : 'Send'}
    </button>
  );
}

SendButton.propTypes = {
  onClick: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  phase: PropTypes.oneOf(['idle', 'processing', 'receiving']),
  launching: PropTypes.bool,
};
