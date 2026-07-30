import React, { Suspense, lazy, useState, useEffect, useCallback, useMemo } from 'react';
import {
  Network,
  Activity,
  RefreshCw,
  Trash2,
  Loader2,
  GitBranch,
  DownloadCloud,
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

  // Branch state for the selected repo (the git checkout the index is built
  // from). branchInfo is the /branches payload; selectedBranch is the dropdown
  // value the user wants to switch to.
  const [branchInfo, setBranchInfo] = useState(null);
  const [selectedBranch, setSelectedBranch] = useState('');
  const [branchLoading, setBranchLoading] = useState(false);
  const [isFetching, setIsFetching] = useState(false);

  // Data adapter over the codegraph store's REST shim.
  const api = useMemo(() => ({
    listRepos: () => agentApiClient.listCodegraphRepos(),
    reindex: (repo) => agentApiClient.reindexCodegraphRepo(repo),
    deleteIndex: (repo) => agentApiClient.deleteCodegraphIndex(repo),
    getBranches: (repo) => agentApiClient.getRepoBranches(repo),
    fetchBranches: (repo) => agentApiClient.fetchRepoBranches(repo),
    checkoutBranch: (repo, branch) => agentApiClient.checkoutRepoBranch(repo, branch),
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

  // Load the selected repo's git branches whenever the repo changes. A non-git
  // dir returns { is_git: false } and simply hides the picker.
  const loadBranches = useCallback(async (repo) => {
    if (!repo) {
      setBranchInfo(null);
      setSelectedBranch('');
      return;
    }
    setBranchLoading(true);
    try {
      const info = await api.getBranches(repo);
      setBranchInfo(info);
      setSelectedBranch(info?.current || '');
    } catch (err) {
      console.error('Failed to load branches', err);
      setBranchInfo(null);
      setSelectedBranch('');
    } finally {
      setBranchLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadBranches(selectedRepo);
  }, [selectedRepo, loadBranches]);

  // git fetch --prune, then repopulate the branch list. Fetch never hard-fails
  // the request: a creds/offline problem comes back as { ok: false, error }.
  const handleFetchBranches = useCallback(async () => {
    if (!selectedRepo || isFetching) return;
    setIsFetching(true);
    setActionError('');
    try {
      const info = await api.fetchBranches(selectedRepo);
      setBranchInfo(info);
      setSelectedBranch(info?.current || '');
      if (info && info.ok === false) {
        setActionError(`Fetch could not reach the remote: ${info.error || 'unknown error'}. Showing local branches.`);
      }
    } catch (err) {
      console.error('Fetch failed', err);
      setActionError(`Fetch failed: ${err?.message || 'unknown error'}`);
    } finally {
      setIsFetching(false);
    }
  }, [selectedRepo, isFetching, api]);

  // Switch the repo to the chosen branch, then reindex so the graph reflects it.
  // A dirty working tree is refused server-side (409) — surface the file list.
  const handleSwitchBranch = useCallback(async () => {
    if (!selectedRepo || isMutating || !selectedBranch) return;
    if (branchInfo?.current === selectedBranch) return;
    setIsMutating(true);
    setActionError('');
    try {
      await api.checkoutBranch(selectedRepo, selectedBranch);
      await api.reindex(selectedRepo);
      await loadBranches(selectedRepo);
      await loadRepos({ preferRepo: selectedRepo });
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 409 && detail) {
        const files = (detail.dirty_files || []).slice(0, 10).join(', ');
        setActionError(
          `${detail.message || 'Repo has uncommitted changes.'}${files ? ` Modified: ${files}` : ''}`
        );
      } else {
        const msg = typeof detail === 'string' ? detail : (err?.message || 'unknown error');
        setActionError(`Switch failed: ${msg}`);
      }
      // Refresh branch state so the dropdown snaps back to the actual branch.
      await loadBranches(selectedRepo);
    } finally {
      setIsMutating(false);
    }
  }, [selectedRepo, isMutating, selectedBranch, branchInfo, api, loadBranches, loadRepos]);

  return (
    // Dark shell. The canvas is a starfield, and a light chrome wrapped around
    // it read as two unrelated apps. Sidebar/FilterPanel already use semantic
    // tokens, so they flip for free. bg-slate-950 is explicit because
    // index.css sets `body { bg-slate-50 }` at element level, which `.dark`
    // does not override.
    //
    // --primary is #dc2626 in BOTH themes and drives every selection state;
    // against a starfield red reads as an error and collides with the
    // red-dwarf star colour. Overriding it here scopes the change to this
    // page only.
    <div
      className="dark flex h-screen max-h-screen bg-slate-950 text-foreground overflow-hidden font-sans"
      style={{ '--primary': '#38bdf8', '--ring': '#38bdf8' }}
    >
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

          {/* Branch selector — only for repos that are real git checkouts. The
              index is built from whatever branch is checked out on disk, so
              switching here checks out the branch and reindexes. */}
          {selectedRepo && branchInfo && (
            branchInfo.is_git ? (
              <div className="mt-3">
                <div className="flex items-center gap-1.5 mb-1">
                  <GitBranch className="w-3.5 h-3.5 text-muted-foreground" />
                  <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
                    Branch
                  </span>
                </div>
                {branchInfo.detached ? (
                  <p className="text-[11px] text-muted-foreground italic px-1 py-1">
                    Detached HEAD — check out a branch on disk to switch.
                  </p>
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      <select
                        value={selectedBranch}
                        onChange={(e) => setSelectedBranch(e.target.value)}
                        disabled={branchLoading || isMutating}
                        className="flex-1 min-w-0 bg-background border border-border text-foreground rounded-md py-1.5 px-2 text-sm focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
                      >
                        {branchInfo.local?.length > 0 && (
                          <optgroup label="Local">
                            {branchInfo.local.map((b) => (
                              <option key={`l-${b}`} value={b}>
                                {b === branchInfo.current ? `${b} (current)` : b}
                              </option>
                            ))}
                          </optgroup>
                        )}
                        {branchInfo.remote?.length > 0 && (
                          <optgroup label="Remote">
                            {branchInfo.remote.map((b) => (
                              <option key={`r-${b}`} value={b}>{b}</option>
                            ))}
                          </optgroup>
                        )}
                      </select>
                      <button
                        onClick={handleFetchBranches}
                        disabled={isFetching || isMutating}
                        title="Fetch remote branches from the origin (git fetch)"
                        className="inline-flex items-center justify-center rounded-md border border-border bg-background p-1.5 text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {isFetching
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <DownloadCloud className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                    {!branchInfo.remote_loaded && (
                      <p className="mt-1 text-[10px] text-muted-foreground leading-snug">
                        Showing local branches. Fetch <DownloadCloud className="inline w-2.5 h-2.5 -mt-0.5" /> to load remote branches.
                      </p>
                    )}
                    <button
                      onClick={handleSwitchBranch}
                      disabled={
                        isMutating || branchLoading || !selectedBranch ||
                        selectedBranch === branchInfo.current
                      }
                      title="Check out this branch and rebuild the index"
                      className="mt-2 w-full inline-flex items-center justify-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-2.5 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {isMutating
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <GitBranch className="w-3.5 h-3.5" />}
                      Switch &amp; reindex
                    </button>
                  </>
                )}
              </div>
            ) : (
              <p className="mt-3 text-[11px] text-muted-foreground italic px-1">
                Not a git checkout — branch switching unavailable.
              </p>
            )
          )}

          {/* Index lifecycle actions */}
          <div className="mt-3 flex items-center gap-2">
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
