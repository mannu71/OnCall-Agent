import React, { useState, useRef } from 'react';
import PropTypes from 'prop-types';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Generic smooth (max-height) collapsible block — header with chevron + title +
 * optional count badge, animated content. Used for the "Reasoning / activity"
 * and "Sources" sections in the chat. Borrows the reference UI's collapsible
 * thinking/sources pattern without copying its code.
 */
export default function CollapsibleSection({
  icon: Icon,
  title,
  count,
  defaultOpen = false,
  accent = 'slate',
  children,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyRef = useRef(null);

  const accents = {
    slate: 'text-slate-500 dark:text-slate-400',
    primary: 'text-primary',
    blue: 'text-blue-600 dark:text-blue-400',
    emerald: 'text-emerald-600 dark:text-emerald-400',
  };

  return (
    <div className="mt-3 rounded-lg border border-black/[0.07] dark:border-white/10 bg-black/[0.015] dark:bg-white/[0.03] overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left hover:bg-black/[0.03] dark:hover:bg-white/[0.06] transition-colors"
      >
        <ChevronRight
          className={cn('size-3.5 text-slate-400 shrink-0 transition-transform duration-200', open && 'rotate-90')}
        />
        {Icon && <Icon className={cn('size-3.5 shrink-0', accents[accent] || accents.slate)} />}
        <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          {title}
        </span>
        {count != null && (
          <span className="ml-auto text-[10px] font-mono text-slate-400">{count}</span>
        )}
      </button>
      <div
        className="chat-collapsible"
        style={{ maxHeight: open ? (bodyRef.current?.scrollHeight ? `${bodyRef.current.scrollHeight}px` : '600px') : '0px' }}
      >
        <div ref={bodyRef} className="px-2.5 pb-2 pt-1 border-t border-black/[0.06] dark:border-white/10">
          {children}
        </div>
      </div>
    </div>
  );
}

CollapsibleSection.propTypes = {
  icon: PropTypes.elementType,
  title: PropTypes.string.isRequired,
  count: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  defaultOpen: PropTypes.bool,
  accent: PropTypes.string,
  children: PropTypes.node,
};
