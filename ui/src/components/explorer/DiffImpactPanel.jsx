import React from 'react';
import { FileCode, Network, Play } from 'lucide-react';
import { Input } from '@/components/ui/input';

export default function DiffImpactPanel({
  diffFilesText,
  setDiffFilesText,
  diffSymbolsText,
  setDiffSymbolsText,
  handleRunDiffImpact,
  diffImpactResult,
}) {
  return (
    <div className="space-y-4 text-xs">
      <div className="space-y-2">
        <label className="font-semibold text-foreground flex items-center gap-1.5">
          <FileCode className="w-3.5 h-3.5 text-primary" />
          Modified Files (one per line)
        </label>
        <textarea
          value={diffFilesText}
          onChange={(e) => setDiffFilesText(e.target.value)}
          placeholder="e.g. src/Services/AuthService.cs"
          rows={3}
          className="w-full bg-background border border-border rounded-md p-2 text-foreground font-mono focus:outline-none focus:border-primary transition-colors"
        />
      </div>

      <div className="space-y-2">
        <label className="font-semibold text-foreground flex items-center gap-1.5">
          <Network className="w-3.5 h-3.5 text-indigo-500" />
          Modified Symbols (one per line)
        </label>
        <textarea
          value={diffSymbolsText}
          onChange={(e) => setDiffSymbolsText(e.target.value)}
          placeholder="e.g. CheckUserCredentials"
          rows={3}
          className="w-full bg-background border border-border rounded-md p-2 text-foreground font-mono focus:outline-none focus:border-primary transition-colors"
        />
      </div>

      <button
        onClick={handleRunDiffImpact}
        className="w-full bg-gradient-to-r from-primary to-indigo-600 hover:opacity-90 text-white rounded-md py-2 font-semibold shadow-md flex items-center justify-center gap-2 transition-all cursor-pointer"
      >
        <Play className="w-3.5 h-3.5" />
        Analyze Diff Impact
      </button>

      {diffImpactResult && (
        <div className="mt-4 p-3 bg-muted rounded-md border border-border space-y-3">
          <div className="flex justify-between items-center border-b border-border pb-1.5">
            <span className="font-bold text-foreground">Impact Summary</span>
            <span className="bg-primary/20 text-primary px-2 py-0.5 rounded text-[10px]">
              Deterministic
            </span>
          </div>
          
          <div className="space-y-1.5">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total Impacted Nodes:</span>
              <span className="font-semibold text-foreground">{diffImpactResult.total_impacted_nodes}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Entry Points Affected:</span>
              <span className="font-semibold text-rose-600 dark:text-rose-400">{diffImpactResult.affected_entry_points.length}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Impacted Unit Tests:</span>
              <span className="font-semibold text-purple-600 dark:text-purple-400">{diffImpactResult.affected_tests.length}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
