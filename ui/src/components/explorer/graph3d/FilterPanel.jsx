import { useMemo } from 'react';
import { colorForLabel } from './colors';
import { colorForEdgeType } from './palette';

export function FilterPanel({
  data,
  enabledLabels,
  enabledEdgeTypes,
  showLabels,
  showLinks,
  onToggleLabel,
  onToggleEdgeType,
  onToggleShowLabels,
  onToggleShowLinks,
  onEnableAll,
  onDisableAll,
}) {
  const { labelCounts, edgeTypeCounts } = useMemo(() => {
    const lc = new Map();
    for (const n of data.nodes) lc.set(n.label, (lc.get(n.label) ?? 0) + 1);
    const ec = new Map();
    for (const e of data.edges) ec.set(e.type, (ec.get(e.type) ?? 0) + 1);
    return {
      labelCounts: [...lc.entries()].sort((a, b) => b[1] - a[1]),
      edgeTypeCounts: [...ec.entries()].sort((a, b) => b[1] - a[1]),
    };
  }, [data]);

  return (
    <div className="px-4 py-3 border-b border-border/40 space-y-3">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-foreground/50 uppercase tracking-widest">
          Filters
        </span>
        <div className="flex items-center gap-2">
          <button onClick={onEnableAll} className="text-[10px] text-primary/70 hover:text-primary transition-colors">All</button>
          <span className="text-foreground/15">|</span>
          <button onClick={onDisableAll} className="text-[10px] text-primary/70 hover:text-primary transition-colors">None</button>
        </div>
      </div>

      {/* Node labels. Borderless rows rather than filled pills: 20+ bordered
          chips read as a wall of boxes and crowded the canvas. The colour
          identity lives in the dot, so the box around it was never carrying
          information. */}
      <div>
        <p className="text-[10px] text-foreground/40 mb-1">Nodes</p>
        <div className="grid grid-cols-2 gap-x-3 gap-y-[2px]">
          {labelCounts.map(([label, count]) => {
            const on = enabledLabels.has(label);
            const c = colorForLabel(label);
            return (
              <button
                key={label}
                onClick={() => onToggleLabel(label)}
                className={`group flex items-center gap-1.5 text-[10px] leading-4 transition-opacity ${
                  on ? 'opacity-100' : 'opacity-35'
                }`}
              >
                <span
                  className="w-[6px] h-[6px] rounded-full flex-shrink-0"
                  style={{ backgroundColor: on ? c : 'transparent', boxShadow: on ? `0 0 5px ${c}` : 'none', border: on ? 'none' : '1px solid currentColor' }}
                />
                <span className="truncate text-foreground/75">{label}</span>
                <span className="ml-auto text-foreground/35 tabular-nums">{count.toLocaleString()}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Edge types, behind a disclosure. There are a dozen of them, most
          people never touch them, and expanded they doubled the height of the
          panel for something that is off by default anyway. */}
      <details className="group">
        <summary className="text-[10px] text-foreground/40 cursor-pointer list-none flex items-center gap-1 select-none">
          <span className="transition-transform group-open:rotate-90">&#9656;</span>
          Relationships
          <span className="ml-auto text-foreground/25 tabular-nums">
            {enabledEdgeTypes.size}/{edgeTypeCounts.length}
          </span>
        </summary>
        <div className="grid grid-cols-2 gap-x-3 gap-y-[2px] mt-1.5">
          {edgeTypeCounts.map(([type, count]) => {
            const on = enabledEdgeTypes.has(type);
            const c = colorForEdgeType(type);
            return (
              <button
                key={type}
                onClick={() => onToggleEdgeType(type)}
                className={`flex items-center gap-1.5 text-[10px] leading-4 transition-opacity ${
                  on ? 'opacity-100' : 'opacity-35'
                }`}
              >
                <span
                  className="w-[6px] h-[6px] rounded-full flex-shrink-0"
                  style={{ backgroundColor: on ? c : 'transparent', boxShadow: on ? `0 0 5px ${c}` : 'none', border: on ? 'none' : '1px solid currentColor' }}
                />
                <span className="truncate text-foreground/70">{type.replace(/_/g, ' ').toLowerCase()}</span>
                <span className="ml-auto text-foreground/35 tabular-nums">{count.toLocaleString()}</span>
              </button>
            );
          })}
        </div>
      </details>

      {/* Show labels toggle */}
      <button
        onClick={onToggleShowLabels}
        className={`inline-flex items-center gap-1.5 text-[11px] font-medium transition-all ${
          showLabels ? 'text-primary' : 'text-foreground/30'
        }`}
      >
        <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center transition-all ${
          showLabels ? 'border-primary bg-primary/20' : 'border-foreground/15'
        }`}>
          {showLabels && <span className="text-primary text-[9px]">✓</span>}
        </span>
        Show labels
      </button>

      {/* Connections are off by default: drawn over the whole galaxy at once
          they form a web that completely hides the spiral underneath.
          Selecting a node still shows its own connections regardless. */}
      <button
        onClick={onToggleShowLinks}
        className={`inline-flex items-center gap-1.5 text-[11px] font-medium transition-all ${
          showLinks ? 'text-primary' : 'text-foreground/30'
        }`}
      >
        <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center transition-all ${
          showLinks ? 'border-primary bg-primary/20' : 'border-foreground/15'
        }`}>
          {showLinks && <span className="text-primary text-[9px]">✓</span>}
        </span>
        Show all connections
      </button>
    </div>
  );
}
