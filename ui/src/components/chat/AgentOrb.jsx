import React from 'react';
import PropTypes from 'prop-types';
import { ThinkingOrb } from 'thinking-orbs';
import { cn } from '@/lib/utils';

/**
 * The single import site for `thinking-orbs` (MIT, zero runtime deps, 2D canvas).
 * Everything else in the app goes through this wrapper, so the package can be
 * swapped, vendored or dropped by editing one file.
 *
 * The library resolves its own palette from the nearest `dark`/`light` class,
 * which is exactly how this app toggles theme (`Chat.jsx` puts `dark` on
 * documentElement), so `theme` is left at its `auto` default. It also honours
 * `prefers-reduced-motion` and pauses offscreen/backgrounded on its own.
 */

/**
 * Map free-form agent activity — a tool name, or a status line written for a
 * human — onto one of the six shipped orb states.
 *
 * This is a presentation heuristic, not a classification: the six states are
 * visual moods, and a wrong guess costs nothing but a mismatched animation. It
 * is ordered because the patterns overlap — `cloudwatch_analyze_patterns` is
 * "solving" rather than "searching", and `codegraph_find_symbol` is "searching"
 * rather than "shaping" — so the first match wins and the order encodes which
 * signal is the stronger one.
 */
const STATE_RULES = [
  [/analy|diagnos|reason|think|plan\b|rca|investigat|evaluat|grade|solv|root.?cause/i, 'solving'],
  [/writ|compos|summar|report|draft|generat|answer|respond|synthes/i, 'composing'],
  [/wait|approv|confirm|human|listen|pending/i, 'listening'],
  [/search|quer|find|grep|lookup|fetch|list|read|log|recall|retriev|explor|scan/i, 'searching'],
  [/index|graph|transform|compil|migrat|structur|shape|build/i, 'shaping'],
];

function orbStateFor(hint) {
  if (!hint) return 'working';
  const text = String(hint);
  for (const [re, state] of STATE_RULES) {
    if (re.test(text)) return state;
  }
  return 'working';
}

/**
 * Animated activity indicator. Pass `hint` (a tool name or status message) and
 * the matching state is chosen; pass `state` to pin one explicitly.
 *
 * `size` accepts only the library's two tuned presets (64 and 20) — they carry
 * different dot counts and speeds and are separate designs, not a scale factor.
 * `scale` renders one of those designs smaller via a CSS transform, for slots
 * where 20px would break the row; the canvas is painted at up to 2x device
 * pixels, so it stays crisp.
 */
export default function AgentOrb({ hint, state, size = 20, scale = 1, theme, className, label }) {
  const resolved = state || orbStateFor(hint);
  const box = Math.round(size * scale);
  const orb = (
    <ThinkingOrb
      state={resolved}
      size={size}
      // `auto` reads the page theme, which is the wrong signal on a filled
      // button: the ink has to contrast with the button, not the page. Callers
      // sitting on a coloured surface pin it — `dark` paints light ink.
      theme={theme || 'auto'}
      aria-label={label || (hint ? String(hint) : `Agent ${resolved}`)}
      style={scale === 1 ? undefined : { transform: `scale(${scale})`, transformOrigin: 'center' }}
    />
  );
  if (scale === 1) return <span className={cn('inline-flex shrink-0', className)}>{orb}</span>;
  // Collapse the transformed canvas back to its visual footprint so the row
  // lays out against what is actually drawn, not the untransformed 20px box.
  return (
    <span
      className={cn('inline-flex items-center justify-center shrink-0', className)}
      style={{ width: box, height: box }}
    >
      {orb}
    </span>
  );
}

AgentOrb.propTypes = {
  hint: PropTypes.string,
  state: PropTypes.oneOf(['working', 'searching', 'solving', 'listening', 'composing', 'shaping']),
  size: PropTypes.oneOf([20, 64]),
  scale: PropTypes.number,
  theme: PropTypes.oneOf(['auto', 'dark', 'light']),
  className: PropTypes.string,
  label: PropTypes.string,
};
