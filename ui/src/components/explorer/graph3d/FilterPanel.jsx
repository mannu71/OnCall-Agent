import { useMemo } from 'react';
import { colorForLabel } from './colors';

export function FilterPanel({
  data,
  enabledLabels,
  enabledEdgeTypes,
  showLabels,
  onToggleLabel,
  onToggleEdgeType,
  onToggleShowLabels,
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

      {/* Node labels — the color identity lives in the dot; the label text stays
          a neutral theme colour so it reads on the light panel. */}
      <div>
        <p className="text-[10px] text-foreground/40 mb-1.5">Nodes</p>
        <div className="flex flex-wrap gap-1">
          {labelCounts.map(([label, count]) => {
            const on = enabledLabels.has(label);
            const c = colorForLabel(label);
            return (
              <button
                key={label}
                onClick={() => onToggleLabel(label)}
                className={`inline-flex items-center gap-1 px-1.5 py-[3px] rounded-md text-[10px] font-medium transition-all border ${
                  on ? 'border-border bg-muted text-foreground/80' : 'border-transparent opacity-40 text-foreground/40'
                }`}
              >
                <span className="w-[5px] h-[5px] rounded-full" style={{ backgroundColor: on ? c : '#cbd5e1' }} />
                <span>{label}</span>
                <span className="text-foreground/40 tabular-nums">{count.toLocaleString()}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Edge types */}
      <div>
        <p className="text-[10px] text-foreground/40 mb-1.5">Edges</p>
        <div className="flex flex-wrap gap-1">
          {edgeTypeCounts.map(([type, count]) => {
            const on = enabledEdgeTypes.has(type);
            return (
              <button
                key={type}
                onClick={() => onToggleEdgeType(type)}
                className={`inline-flex items-center gap-1 px-1.5 py-[3px] rounded-md text-[10px] font-medium transition-all border ${
                  on ? 'border-border bg-muted text-foreground/70' : 'border-transparent opacity-40 text-foreground/40'
                }`}
              >
                {type.replace(/_/g, ' ').toLowerCase()}
                <span className="text-foreground/40 tabular-nums">{count.toLocaleString()}</span>
              </button>
            );
          })}
        </div>
      </div>

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
    </div>
  );
}
