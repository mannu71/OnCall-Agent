import React, { Suspense, lazy, useState, useEffect, useCallback, useMemo } from 'react';
import {
  Network,
  Activity,
  RefreshCw,
  Trash2,
  Loader2,
} from 'lucide-react';
import { agentApiClient } from '../services/agentApiClient';

// three.js + r3f are heavy (~600KB+) and only needed for the codegraph 3D graph —
// defer loading until that view actually mounts.
const CodegraphGraphView = lazy(() => import('../components/explorer/graph3d/CodegraphGraphView'));

export default function CodebaseExplorer() {
  // Code Crawler is backed by the codegraph engine (its own store/endpoints under
  // /api/v1/codegraph). This page manages that store and renders its 3D graph.
  const [repos, setRepos] = useState([]);
  const [selectedRepo, setSelectedRepo] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false); // reindex / delete in flight
  const [actionError, setActionError] = useState('');
  // Portal target for the codegraph 3D view's filters + file tree, so they fill
  // this otherwise-empty left panel instead of overlaying the graph.
  const [cgSidebarEl, setCgSidebarEl] = useState(null);

  // Data adapter over the codegraph store's REST shim.
  const api = useMemo(() => ({
    listRepos: () => agentApiClient.listCodegraphRepos(),
    reindex: (repo) => agentApiClient.reindexCodegraphRepo(repo),
    deleteIndex: (repo) => agentApiClient.deleteCodegraphIndex(repo),
  }), []);

  // Fetch available indexed repositories. Returns the repo list so callers
  // (initial load, post-reindex, post-delete) can react to the new state.
  const loadRepos = useCallback(async ({ preferRepo } = {}) => {
    setIsLoading(true);
    try {
      const res = await api.listRepos();
      const list = res?.repos || [];
      setRepos(list);
      if (list.length > 0) {
        const stillThere = preferRepo && list.some(r => r.repo_name === preferRepo);
        setSelectedRepo(stillThere ? preferRepo : list[0].repo_name);
      } else {
        setSelectedRepo('');
      }
      return list;
    } catch (err) {
      console.error('Failed to load repositories', err);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadRepos();
  }, [loadRepos]);

  // Force a full rebuild of the selected repo's index, then refresh counts.
  const handleReindex = useCallback(async () => {
    if (!selectedRepo || isMutating) return;
    setIsMutating(true);
    setActionError('');
    try {
      await api.reindex(selectedRepo);
      await loadRepos({ preferRepo: selectedRepo });
    } catch (err) {
      console.error('Reindex failed', err);
      setActionError(`Reindex failed: ${err?.message || 'unknown error'}`);
    } finally {
      setIsMutating(false);
    }
  }, [selectedRepo, isMutating, loadRepos, api]);

  // Delete the entire index for the selected repo after explicit confirmation.
  const handleDeleteIndex = useCallback(async () => {
    if (!selectedRepo || isMutating) return;
    const confirmed = window.confirm(
      `Delete the entire index for "${selectedRepo}"?\n\n` +
      'This removes its knowledge graph and overview. You can rebuild it later by re-indexing.'
    );
    if (!confirmed) return;
    setIsMutating(true);
    setActionError('');
    try {
      await api.deleteIndex(selectedRepo);
      await loadRepos();
    } catch (err) {
      console.error('Delete index failed', err);
      setActionError(`Delete failed: ${err?.message || 'unknown error'}`);
    } finally {
      setIsMutating(false);
    }
  }, [selectedRepo, isMutating, loadRepos, api]);

  return (
    <div className="flex h-screen max-h-screen bg-background text-foreground overflow-hidden font-sans">
      {/* LEFT SIDEBAR: repo selection + index lifecycle. codegraph portals its
          own filters + file tree into the panel below. */}
      <div className="w-64 border-r border-border bg-card flex flex-col flex-shrink-0">
        <div className="p-4 border-b border-border">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Network className="w-5 h-5 text-primary animate-pulse" />
              <h2 className="font-bold text-lg tracking-tight bg-gradient-to-r from-primary to-indigo-500 bg-clip-text text-transparent">
                Codebase Explorer
              </h2>
            </div>
            {isLoading && <Activity className="w-4 h-4 text-primary animate-spin" />}
          </div>

          {/* Repo selector */}
          {repos.length === 0 ? (
            <p className="text-[11px] text-muted-foreground italic px-1 py-1.5">
              No Code Crawler-indexed repos yet.
            </p>
          ) : (
            <select
              value={selectedRepo}
              onChange={(e) => setSelectedRepo(e.target.value)}
              className="w-full bg-background border border-border text-foreground rounded-md py-1.5 px-3 text-sm focus:outline-none focus:border-primary transition-colors"
            >
              {repos.map(r => (
                <option key={r.repo_name} value={r.repo_name}>
                  {r.repo_name} ({r.files_indexed} files)
                </option>
              ))}
            </select>
          )}

          {/* Index lifecycle actions */}
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={handleReindex}
              disabled={!selectedRepo || isMutating}
              title="Rebuild this repository's index from scratch"
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isMutating
                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : <RefreshCw className="w-3.5 h-3.5" />}
              Reindex
            </button>
            <button
              onClick={handleDeleteIndex}
              disabled={!selectedRepo || isMutating}
              title="Delete this repository's entire index"
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-destructive/30 bg-background px-2.5 py-1.5 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Delete Index
            </button>
          </div>
          {actionError && (
            <p className="mt-2 text-[11px] text-destructive leading-snug">{actionError}</p>
          )}
        </div>

        {/* codegraph portals its filters + file tree here so the graph itself
            gets the full central width. */}
        <div ref={setCgSidebarEl} className="flex-1 min-h-0 flex flex-col overflow-hidden" />
      </div>

      {/* CENTRAL PANEL: codegraph's native 3D force-directed graph (real physics
          layout, own sidebar/filter/detail panel). */}
      <div className="flex-1 min-w-0 h-full relative bg-background">
        <Suspense fallback={
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-8 h-8 text-primary animate-spin" />
          </div>
        }>
          <CodegraphGraphView repo={selectedRepo} sidebarContainer={cgSidebarEl} />
        </Suspense>
      </div>
    </div>
  );
}
