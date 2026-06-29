import React from 'react';
import PropTypes from 'prop-types';
import { cn } from '@/lib/utils';

// Segmented pill for choosing the agent runtime ("harness"), styled like the
// Agent/Chat toggle. Wording per product: "Legacy" = the in-house ReAct loop;
// "Deepagent" = the official deepagents stack. When `allowDefault` is set a
// leading "Default" segment (value '') defers to the node/server default — used
// in the node config panel; the Chat composer omits it (explicit per-turn pick).
const OPTIONS = [
  { value: 'legacy',     label: 'Legacy',    title: 'In-house ReAct loop' },
  { value: 'deepagents', label: 'Deepagent', title: 'Official deepagents stack' },
];

export default function HarnessToggle({ value, onChange, allowDefault = false }) {
  const opts = allowDefault
    ? [{ value: '', label: 'Default', title: 'Defer to the server/node default' }, ...OPTIONS]
    : OPTIONS;
  // No explicit value → "Default" when offered, else the current server default.
  const active = value || (allowDefault ? '' : 'deepagents');
  return (
    <div className="inline-flex items-center rounded-lg border border-black/[0.08] dark:border-white/10 bg-white/60 dark:bg-white/[0.04] p-0.5 select-none">
      {opts.map((o) => {
        const isActive = active === o.value;
        return (
          <button
            key={o.value || 'default'}
            type="button"
            onClick={() => onChange(o.value)}
            title={o.title}
            aria-pressed={isActive}
            className={cn(
              'h-7 px-2.5 rounded-md text-xs font-semibold transition-colors',
              isActive
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-slate-500 hover:text-slate-700 hover:bg-black/[0.04] dark:text-slate-400 dark:hover:text-slate-200 dark:hover:bg-white/[0.06]',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

HarnessToggle.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  allowDefault: PropTypes.bool,
};
