import React from 'react';
import { CheckSquare, Square, ListChecks } from 'lucide-react';

// Match GitHub task-list items: "- [ ] step" / "- [x] step" / "* [X] step".
const TASK_RE = /^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$/;

/**
 * Parse markdown task-list items out of *content*.
 * @returns {Array<{ done: boolean, text: string }>}
 */
export function parseChecklist(content) {
  if (!content || typeof content !== 'string') return [];
  const items = [];
  for (const line of content.split('\n')) {
    const m = line.match(TASK_RE);
    if (m) items.push({ done: m[1].toLowerCase() === 'x', text: m[2] });
  }
  return items;
}

/**
 * PlanChecklist — pinned plan card.
 *
 * When an agent answer contains a markdown task list, this surfaces it as a
 * compact, progress-tracked card at the top of the message (the items still
 * render inline in the prose below via remark-gfm). Renders nothing when the
 * content has no checklist, so non-planning answers are unaffected.
 *
 * Props: { content: string }
 */
export default function PlanChecklist({ content }) {
  const items = parseChecklist(content);
  if (items.length < 2) return null; // not a real plan — leave to inline markdown

  const done = items.filter((i) => i.done).length;
  const pct = Math.round((done / items.length) * 100);

  return (
    <div className="mb-3 rounded-xl border border-black/[0.07] dark:border-white/10 bg-black/[0.02] dark:bg-white/[0.04] p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        <ListChecks className="h-3.5 w-3.5" />
        Plan
        <span className="ml-auto font-normal tabular-nums text-slate-400 dark:text-slate-500">
          {done}/{items.length}
        </span>
      </div>
      <div className="mb-2 h-1 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-white/10">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-1.5 text-[12.5px] leading-snug">
            {it.done
              ? <CheckSquare className="mt-[1px] h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
              : <Square className="mt-[1px] h-3.5 w-3.5 shrink-0 text-slate-300 dark:text-slate-600" />}
            <span className={it.done ? 'text-slate-400 dark:text-slate-500 line-through' : 'text-slate-700 dark:text-slate-200'}>
              {it.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
