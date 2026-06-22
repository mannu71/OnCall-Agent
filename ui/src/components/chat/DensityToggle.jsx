import React from 'react';
import PropTypes from 'prop-types';
import { Rows3, Rows2, AlignJustify } from 'lucide-react';
import { cn } from '@/lib/utils';

export const DENSITIES = ['compact', 'comfortable', 'spacious'];
const ICONS = { compact: Rows3, comfortable: Rows2, spacious: AlignJustify };
const LABELS = { compact: 'Compact', comfortable: 'Comfortable', spacious: 'Spacious' };

/** Segmented compact/comfortable/spacious density control for the conversation. */
export default function DensityToggle({ value, onChange }) {
  return (
    <div className="hidden sm:flex items-center rounded-lg border border-black/[0.08] dark:border-white/10 bg-white/60 dark:bg-white/[0.04] p-0.5">
      {DENSITIES.map((d) => {
        const Icon = ICONS[d];
        const active = value === d;
        return (
          <button
            key={d}
            type="button"
            onClick={() => onChange(d)}
            title={LABELS[d]}
            aria-pressed={active}
            className={cn(
              'size-6 rounded-md flex items-center justify-center transition-colors',
              active
                ? 'bg-[#0a84ff] text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-600 hover:bg-black/[0.04] dark:hover:bg-white/[0.06]',
            )}
          >
            <Icon className="size-3.5" />
          </button>
        );
      })}
    </div>
  );
}

DensityToggle.propTypes = {
  value: PropTypes.oneOf(DENSITIES).isRequired,
  onChange: PropTypes.func.isRequired,
};
