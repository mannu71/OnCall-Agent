import React, { useState } from 'react';
import PropTypes from 'prop-types';
import { ChevronRight, CheckCircle2, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import AgentOrb from './AgentOrb';

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

function fmtDuration(ms) {
  if (ms == null) return null;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Fold a flat step list into the shape it actually has.
 *
 * Steps arrive flat, each carrying its own origin agent and model, so rendering
 * one-row-per-step repeated those two badges on every line — for a delegated run
 * that is the *same* pair a dozen times over, and it crowded the tool name down
 * to an unreadable stub. Origin changes rarely, so it belongs on a header for
 * the contiguous run that shares it.
 *
 * Consecutive calls to the same tool are also folded into a single row with a
 * repeat count. Retries and paged reads make this common, and ten rows differing
 * only in duration read as noise. Every call is kept in `calls` so expanding
 * still shows each one's arguments and result.
 */
function groupSteps(steps) {
  const groups = [];
  for (const step of steps) {
    const origin = step.agent || 'agent';
    const model = step.model || '';
    let group = groups[groups.length - 1];
    if (!group || group.origin !== origin || group.model !== model) {
      group = { key: `${origin}-${model}-${groups.length}`, origin, model, rows: [] };
      groups.push(group);
    }
    const last = group.rows[group.rows.length - 1];
    // Status is part of the fold key, so a finished call never merges into a
    // running one — a row is always wholly done, wholly failed, or wholly live,
    // and the single icon it shows is true for every call inside it.
    if (last && last.name === step.name && last.status === (step.status || 'running')) {
      last.calls.push(step);
      if (step.durationMs != null) last.durationMs = (last.durationMs || 0) + step.durationMs;
    } else {
      group.rows.push({
        key: step.id,
        name: step.name,
        status: step.status || 'running',
        durationMs: step.durationMs,
        calls: [step],
      });
    }
  }
  return groups;
}

/** One row on the timeline — a tool call (or a folded run of identical calls). */
function ThreadRow({ row }) {
  const [open, setOpen] = useState(false);
  const { status, calls } = row;
  const repeat = calls.length;
  const dur = fmtDuration(row.durationMs);
  const expandable = calls.some(
    (c) =>
      (c.args && typeof c.args === 'object' && Object.keys(c.args).length > 0) ||
      (c.result != null && c.result !== ''),
  );

  const dot =
    status === 'done'
      ? 'bg-emerald-500 border-emerald-200 dark:border-emerald-500/30'
      : status === 'error'
        ? 'bg-red-500 border-red-200 dark:border-red-500/30'
        : 'bg-primary border-primary/30 chat-thread-pulse';

  return (
    <div className="relative pl-6 py-[3px]">
      <span
        className={cn(
          'absolute left-0 top-[9px] size-2.5 rounded-full border-2 border-white dark:border-[#1c1c1e] transition-all',
          dot,
        )}
      />
      <button
        type="button"
        onClick={() => expandable && setOpen((o) => !o)}
        disabled={!expandable}
        className={cn(
          'w-full flex items-center gap-1.5 text-left rounded px-1 -mx-1 py-0.5',
          expandable && 'hover:bg-slate-100/70 dark:hover:bg-white/[0.04] transition-colors',
        )}
      >
        <ChevronRight
          className={cn(
            'size-3 shrink-0 transition-transform duration-200',
            open && 'rotate-90',
            expandable ? 'text-slate-400' : 'text-transparent',
          )}
        />
        <span className="font-mono text-[11px] font-medium text-slate-700 dark:text-slate-200 truncate">
          {row.name}
        </span>
        {repeat > 1 && (
          <span
            className="shrink-0 rounded px-1 py-px text-[9px] font-semibold tabular-nums bg-slate-100 text-slate-500 dark:bg-white/10 dark:text-slate-400"
            title={`Called ${repeat} times in a row`}
          >
            ×{repeat}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 shrink-0">
          {dur && <span className="text-[9.5px] text-slate-400 font-mono tabular-nums">{dur}</span>}
          {status === 'running' && <AgentOrb hint={row.name} size={20} scale={0.75} />}
          {status === 'done' && <CheckCircle2 className="size-3 text-emerald-500" />}
          {status === 'error' && <XCircle className="size-3 text-red-500" />}
        </span>
      </button>
      {open && (
        <div className="mt-1 space-y-1.5">
          {calls.map((c, i) => (
            <div key={c.id || i} className="space-y-1">
              {repeat > 1 && (
                <div className="text-[8.5px] font-semibold uppercase tracking-wider text-slate-400">
                  Call {i + 1} of {repeat}
                  {c.durationMs != null && ` · ${fmtDuration(c.durationMs)}`}
                </div>
              )}
              {c.args && typeof c.args === 'object' && Object.keys(c.args).length > 0 && (
                <pre className="text-[10px] font-mono text-slate-600 dark:text-slate-300 bg-white dark:bg-black/30 border border-slate-200/70 dark:border-white/10 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-all">{safeJson(c.args)}</pre>
              )}
              {c.result != null && c.result !== '' && (
                <pre className="text-[10px] font-mono text-slate-600 dark:text-slate-300 bg-white dark:bg-black/30 border border-slate-200/70 dark:border-white/10 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-all max-h-48 overflow-y-auto">{safeJson(c.result)}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

ThreadRow.propTypes = { row: PropTypes.object.isRequired };

/** Who ran the calls below — printed once per contiguous run, not per row. */
function GroupHeader({ origin, model }) {
  const isMain = origin === 'agent';
  return (
    <div className="relative pl-6 pt-1.5 pb-0.5 flex items-center gap-1.5">
      <span
        className={cn(
          'rounded-full px-1.5 py-px text-[9px] font-semibold tracking-wide',
          isMain
            ? 'bg-slate-100 text-slate-500 dark:bg-white/10 dark:text-slate-400'
            : 'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300',
        )}
        title={isMain ? 'Run by the main agent' : `Run by subagent: ${origin}`}
      >
        {isMain ? 'main agent' : origin}
      </span>
      {model && (
        <span
          className="min-w-0 truncate inline-flex items-center gap-1 text-[9px] font-mono text-slate-400 dark:text-slate-500"
          title={`Model: ${model}`}
        >
          <span className="opacity-50">◆</span>
          {shortModel(model)}
        </span>
      )}
    </div>
  );
}

GroupHeader.propTypes = { origin: PropTypes.string.isRequired, model: PropTypes.string };

/**
 * Inline "agent thread" timeline — a vertical line with a node dot per tool
 * call. While the run is live, a synapse pulse travels down the line.
 */
export default function AgentThread({ steps, live = false }) {
  if (!steps || steps.length === 0) return null;
  const groups = groupSteps(steps);
  return (
    <div className="mb-3 mt-0.5">
      {groups.map((g, gi) => {
        // Delegated work is indented under the call that spawned it, so the
        // handoff is visible as structure rather than only as a badge — which
        // means the rail has to be drawn per group, at that group's own indent.
        // A single rail on the thread root left every indented dot floating
        // beside a line that no longer ran through it.
        const nested = g.origin !== 'agent';
        const isLive = live && g.rows.some((r) => r.status === 'running');
        return (
          <div key={g.key} className={cn('relative', nested && 'ml-3')}>
            {/* The rail is 2px at left-4 so its centre lands on 5px — exactly the
                centre of a `size-2.5` dot pinned to left-0. A 1px rail cannot sit
                on that half-pixel and reads as off-axis at every zoom level. */}
            <div className={cn(
              'absolute left-1 top-2 w-0.5 rounded-full bg-slate-200/80 dark:bg-white/10',
              // Run flush to the edge when another group follows, so its elbow
              // has an unbroken rail to join; stop short on the last one.
              gi === groups.length - 1 ? 'bottom-1' : 'bottom-0',
            )} />
            {/* Elbow joining this branch back to the parent's rail: a stub that
                carries the parent's x down to this group, then a turn inward.
                -left-2 is the parent rail's x once this group's ml-3 is undone. */}
            {nested && (
              <>
                <div className="absolute -left-2 top-0 h-3 w-0.5 bg-slate-200/80 dark:bg-white/10" />
                <div className="absolute -left-2 top-3 w-[14px] h-0.5 rounded-full bg-slate-200/80 dark:bg-white/10" />
              </>
            )}
            {isLive && (
              <div className="absolute left-[3px] w-[5px] h-[5px] rounded-full bg-primary chat-synapse" />
            )}
            <GroupHeader origin={g.origin} model={g.model} />
            {g.rows.map((row) => (
              <ThreadRow key={row.key} row={row} />
            ))}
          </div>
        );
      })}
    </div>
  );
}

AgentThread.propTypes = {
  steps: PropTypes.array,
  live: PropTypes.bool,
};
